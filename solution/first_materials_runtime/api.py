"""Подготовка утверждённых материалов: компонент B8-04."""

import hashlib
import json
import sys
from typing import Any, ContextManager, cast
from uuid import UUID


# Точный набор полей для каждой операции.
REQUEST_FIELDS = {
    "prepare": {
        "scope", "request_id", "event_id",
        "catalog_version", "kind",
    },
    "prepare_initial": {
        "scope", "request_id", "candidate_ref",
        "assessment_ref", "catalog_version",
    },
}

SCOPE_FIELDS = {
    "client_id", "campaign_id", "lead_id",
    "mailbox_ref", "thread_id",
}

# Только эти исключения принадлежат исходным доменным компонентам,
# которые стоят за портами B8-04. Одного атрибута ``code`` недостаточно:
# произвольная ошибка зависимости не должна превращаться в INVALID.
TRUSTED_DOMAIN_ERRORS = {
    ("sales_discovery_qualification", "QualificationError"),
    ("reference_source.sales_discovery_qualification", "QualificationError"),
    ("sales_template_materialization", "TemplateMaterializationError"),
    ("reference_source.sales_template_materialization", "TemplateMaterializationError"),
    ("sales_language_registry", "LanguageRegistryError"),
    ("reference_source.sales_language_registry", "LanguageRegistryError"),
    ("sales_action_locale", "ActionLocaleError"),
    ("reference_source.sales_action_locale", "ActionLocaleError"),
    ("sales_rules", "RuleViolation"),
    ("reference_source.sales_rules", "RuleViolation"),
}

TRANSPORT_ERRORS = (TimeoutError, ConnectionError)


def valid_id(value):
    """Проверяем строковый идентификатор по правилам контракта."""
    return (
        type(value) is str
        and 1 <= len(value) <= 128
        and value == value.strip()
        and all(
            ord(char) >= 32 and ord(char) != 127
            for char in value
        )
    )


def valid_request(operation, request):
    """Проверяем операцию, поля запроса и значения scope."""
    if type(operation) is not str:
        return False

    if operation not in REQUEST_FIELDS:
        return False

    if type(request) is not dict:
        return False

    # Запрещаем пропущенные и дополнительные поля.
    if set(request) != REQUEST_FIELDS[operation]:
        return False

    scope = request["scope"]
    if type(scope) is not dict or set(scope) != SCOPE_FIELDS:
        return False

    for field in SCOPE_FIELDS:
        value = scope[field]

        if field == "thread_id" and operation == "prepare_initial":
            # До первого сообщения provider thread отсутствует.
            if value is not None:
                return False
        elif not valid_id(value):
            return False

    # Campaign ID: UUID в каноническом нижнем регистре.
    try:
        if str(UUID(scope["campaign_id"])) != scope["campaign_id"]:
            return False
    except ValueError:
        return False

    for field in REQUEST_FIELDS[operation] - {"scope"}:
        if not valid_id(request[field]):
            return False

    if operation == "prepare":
        if request["kind"] not in ("PRODUCT_INFO", "PRESENTATION"):
            return False

    return True


def material_error(material):
    """Возвращаем код ошибки или None для корректного описания."""
    if (
        type(material) is not dict
        or set(material) != {
            "material_ref", "sha256", "approved_fact_ids"
        }
    ):
        return "INVALID_MATERIAL_DESCRIPTOR"

    if not valid_id(material["material_ref"]):
        return "INVALID_MATERIAL_DESCRIPTOR"

    # Проверяем формат SHA-256, а не содержимое самого материала.
    digest = material["sha256"]
    if (
        type(digest) is not str
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
    ):
        return "INVALID_HASH"

    facts = material["approved_fact_ids"]
    if type(facts) is not list:
        return "INVALID_MATERIAL_DESCRIPTOR"

    if not all(valid_id(fact) for fact in facts):
        return "INVALID_MATERIAL_DESCRIPTOR"

    # Повторяющиеся идентификаторы фактов запрещены.
    if len(set(facts)) != len(facts):
        return "INVALID_MATERIAL_DESCRIPTOR"

    return None


def save_response_error(saved, material_ref):
    """Код ошибки ответа materials.save или None."""
    if (
        type(saved) is not dict
        or set(saved) != {"material_ref"}
        or not valid_id(saved["material_ref"])
    ):
        return "INVALID_SAVE_RESPONSE"

    # Сохранённая ссылка должна совпасть с переданным описанием.
    if saved["material_ref"] != material_ref:
        return "MATERIAL_REF_MISMATCH"

    return None


def request_fingerprint(request):
    """Одинаковый запрос всегда даёт один и тот же отпечаток."""
    raw = json.dumps(
        request,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def replay_key(request):
    """Ключ повтора: scope и request_id, без текста письма."""
    return json.dumps(
        [request["scope"], request["request_id"]],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def stored_result(tx, request):
    """REPLAY для того же запроса и CONFLICT, если байты изменились."""
    stored = tx.get("replay", replay_key(request))
    if stored is None:
        return None

    if (
        type(stored) is dict
        and stored.get("fingerprint") == request_fingerprint(request)
        and type(stored.get("data")) is dict
    ):
        return {
            "status": "REPLAY",
            "data": dict(stored["data"]),
        }

    return {
        "status": "CONFLICT",
        "data": {},
    }


def qualification_error(value):
    """Код ошибки ответа квалификации или None."""
    verdicts = (
        "QUALIFIED_FOR_PREPARATION",
        "NOT_QUALIFIED",
        "NEEDS_REVIEW",
    )
    if (
        type(value) is not dict
        or set(value) != {
            "verdict", "qualification_ref", "reason_codes"
        }
        or value["verdict"] not in verdicts
        or not valid_id(value["qualification_ref"])
    ):
        return "INVALID_QUALIFICATION_RESPONSE"

    reasons = value["reason_codes"]
    if (
        type(reasons) is not list
        or not all(type(item) is str for item in reasons)
    ):
        return "INVALID_QUALIFICATION_RESPONSE"

    return None


def dispatch_response_error(value):
    """Код ошибки ответа dispatch.propose или None."""
    if (
        type(value) is not dict
        or set(value) != {"action_id", "state"}
        or not valid_id(value["action_id"])
        or value["state"] != "PREPARED"
    ):
        return "INVALID_DISPATCH_RESPONSE"

    return None


class DependencyUnavailable(RuntimeError):
    """Обязательная зависимость недоступна."""


class InvalidPortResponse(Exception):
    """Неверный ответ зависимости. Исключение выходит из транзакции и откатывает её."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def trusted_domain_code(error):
    """Возвращаем код только для известного класса исходного компонента."""
    identity = (
        type(error).__module__,
        type(error).__name__,
    )
    if identity not in TRUSTED_DOMAIN_ERRORS:
        return None

    module = sys.modules.get(identity[0])
    trusted_type = getattr(module, identity[1], None)
    if type(error) is not trusted_type:
        return None

    code = getattr(error, "code", None)
    if not valid_id(code):
        return None
    return code


class MaterialsService:
    def __init__(self, ports: Any):
        # Доверенные зависимости передаются снаружи.
        self.ports = ports

    def execute(self, operation, request):
        # 1. Проверяем входные данные до авторизации.
        if not valid_request(operation, request):
            return {
                "status": "INVALID",
                "data": {"code": "INVALID_REQUEST"},
            }

        # 2. Получаем сведения о пользователе из доверенных ports.
        auth = getattr(self.ports, "auth", None)
        if (
            type(auth) is not dict
            or auth.get("authenticated") is not True
        ):
            return {
                "status": "UNAUTHENTICATED",
                "data": {},
            }

        # 3. Проверяем разрешение на подготовку материалов.
        capabilities = auth.get("capabilities", [])
        if (
            type(capabilities) is not list
            or not (
                "materials.prepare" in capabilities
                or "*" in capabilities
            )
        ):
            return {
                "status": "UNAUTHORIZED",
                "data": {},
            }

        # 4. Запрещаем доступ к данным другого клиента.
        if auth.get("client_id") != request["scope"]["client_id"]:
            return {
                "status": "UNAUTHORIZED",
                "data": {},
            }

        # 5. Проверяем наличие интерфейса транзакций.
        transaction = getattr(self.ports, "transaction", None)
        if not callable(transaction):
            return {
                "status": "DEPENDENCY_UNAVAILABLE",
                "data": {},
            }

        # 6. Работаем с состоянием в пределах заданного scope.
        body_error = None
        body_finished = False
        try:
            context = cast(
                ContextManager[Any],
                transaction(request["scope"]),
            )
            with context as tx:
                try:
                    result = self._execute_in_transaction(
                        tx, operation, request
                    )
                except BaseException as error:
                    # Нужна именно личность объекта: если после него наружу
                    # вышла другая транспортная ошибка, откат/commit неясен.
                    body_error = error
                    raise
                body_finished = True
            return result
        except PermissionError:
            return {
                "status": "UNAUTHORIZED",
                "data": {},
            }
        except DependencyUnavailable:
            return {
                "status": "DEPENDENCY_UNAVAILABLE",
                "data": {},
            }
        except InvalidPortResponse as error:
            # Откат уже выполнен: исключение вышло из контекста транзакции.
            return {
                "status": "INVALID",
                "data": {"code": error.code},
            }
        except TRANSPORT_ERRORS as error:
            if error is body_error:
                # Контекст уже успешно обработал исключение из тела,
                # следовательно откат подтверждён.
                return {
                    "status": "DEPENDENCY_UNAVAILABLE",
                    "data": {},
                }

            if body_finished or body_error is not None:
                # Ошибка пришла из __exit__: результат commit/rollback
                # неизвестен. Разрешено только чтение, но не повтор эффектов.
                self._reread_after_uncertain_commit(request)
                return {
                    "status": "UNKNOWN",
                    "data": {"request_id": request["request_id"]},
                }

            # Создать или открыть транзакцию не удалось; эффектов ещё нет.
            return {
                "status": "DEPENDENCY_UNAVAILABLE",
                "data": {},
            }

    def _call(self, name, request):
        """Вызываем зависимость через доверенный интерфейс."""
        call = getattr(self.ports, "call", None)
        if not callable(call):
            raise DependencyUnavailable(name)

        try:
            return call(name, request)
        except Exception as exc:
            # Так выданный стенд сообщает об отсутствии зависимости.
            if type(exc).__name__ == "MissingDependency":
                raise DependencyUnavailable(name) from exc

            code = trusted_domain_code(exc)
            if code is not None:
                raise InvalidPortResponse(code) from exc

            # Остальные ошибки не маскируем.
            raise

    def _reread_after_uncertain_commit(self, request):
        """Проверяем scoped replay без повторения внешних эффектов."""
        transaction = getattr(self.ports, "transaction", None)
        if not callable(transaction):
            return

        try:
            context = cast(
                ContextManager[Any],
                transaction(request["scope"]),
            )
            with context as tx:
                stored_result(tx, request)
        except Exception:
            # Повторное чтение диагностическое: UNKNOWN остаётся UNKNOWN.
            return

    def _execute_in_transaction(self, tx, operation, request):
        """Проверяем состояние, каталог, событие и материал."""
        authority = tx.get("authority", "current")
        catalog = tx.get("catalog", "current")

        # Отсутствующие данные нельзя заменять выдуманными.
        if authority is None or catalog is None:
            return {
                "status": "DEPENDENCY_UNAVAILABLE",
                "data": {},
            }

        if (
            type(authority) is not dict
            or type(catalog) is not dict
        ):
            return {
                "status": "INVALID",
                "data": {"code": "INVALID_STATE"},
            }

        # Остановка и передача человеку блокируют автоматизацию.
        if authority.get("status") in (
            "STOPPED", "HUMAN", "HANDED_OFF"
        ):
            return {
                "status": "BLOCKED",
                "data": {},
            }

        if authority.get("status") != "ACTIVE":
            return {
                "status": "INVALID",
                "data": {"code": "INVALID_STATE"},
            }

        # Повтор узнаём до новых вызовов каталога, сохранения и очереди.
        stored = stored_result(tx, request)
        if stored is not None:
            return stored

        # Запрашиваем именно нужную версию каталога.
        resolved = self._call("catalog.resolve", {
            "scope": request["scope"],
            "version": request["catalog_version"],
        })

        if (
            type(resolved) is not dict
            or set(resolved) != {
                "version", "bundle_ref", "approved"
            }
        ):
            return {
                "status": "INVALID",
                "data": {"code": "INVALID_CATALOG_RESPONSE"},
            }

        if (
            not valid_id(resolved["version"])
            or not valid_id(resolved["bundle_ref"])
            or type(resolved["approved"]) is not bool
        ):
            return {
                "status": "INVALID",
                "data": {"code": "INVALID_CATALOG_RESPONSE"},
            }

        # Другая версия не может подменить запрошенную.
        if resolved["version"] != request["catalog_version"]:
            return {
                "status": "INVALID",
                "data": {"code": "CATALOG_VERSION_MISMATCH"},
            }

        if not resolved["approved"]:
            return {
                "status": "BLOCKED",
                "data": {},
            }

        if operation == "prepare":
            # Читаем событие только из указанного диалога.
            event = self._call("d1.event", {
                "scope": request["scope"],
                "event_id": request["event_id"],
            })

            if type(event) is not dict:
                return {
                    "status": "INVALID",
                    "data": {"code": "INVALID_EVENT_RESPONSE"},
                }

            if event.get("event_id") != request["event_id"]:
                return {
                    "status": "INVALID",
                    "data": {"code": "EVENT_ID_MISMATCH"},
                }

            if "scope" in event and event["scope"] != request["scope"]:
                return {
                    "status": "INVALID",
                    "data": {"code": "SCOPE_MISMATCH"},
                }

            # При неполной истории автоматический ответ запрещён.
            if event.get("coverage_complete") is not True:
                return {
                    "status": "BLOCKED",
                    "data": {},
                }

            # INTEREST не разрешает продолжать автоматическую продажу.
            if event.get("kind") != "HUMAN_QUESTION":
                return {
                    "status": "BLOCKED",
                    "data": {},
                }

            # Подготовку выполняет существующий компонент F129.
            material = self._call("f129.materialize", {
                "scope": request["scope"],
                "event": event,
                "catalog_version": request["catalog_version"],
                "kind": request["kind"],
            })

            # Проверяем точный формат описания материала.
            code = material_error(material)
            if code is not None:
                raise InvalidPortResponse(code)
            material = cast(dict[str, Any], material)
            # Материал может использовать только разрешённые факты.
            allowed_facts = event.get("approved_fact_ids")
            if (
                type(allowed_facts) is not list
                or not all(valid_id(fact) for fact in allowed_facts)
            ):
                raise InvalidPortResponse("FACT_SCOPE_MISMATCH")

            if not set(material["approved_fact_ids"]).issubset(
                set(allowed_facts)
            ):
                raise InvalidPortResponse("FACT_SCOPE_MISMATCH")

            # Списки фактов сохраняют исходный порядок.
            return self._save_and_propose(
                tx,
                request,
                material,
                request["kind"],
                {
                    "request_id": request["request_id"],
                    "material_ref": material["material_ref"],
                    "catalog_version": request["catalog_version"],
                },
            )

        qualification = self._call("f129.qualify", {
            "scope": request["scope"],
            "candidate_ref": request["candidate_ref"],
            "assessment_ref": request["assessment_ref"],
            "catalog_version": request["catalog_version"],
        })
        code = qualification_error(qualification)
        if code is not None:
            raise InvalidPortResponse(code)
        qualification = cast(dict[str, Any], qualification)

        # Без допуска к подготовке материал не сохраняем и не ставим в очередь.
        if qualification["verdict"] != "QUALIFIED_FOR_PREPARATION":
            return {
                "status": "BLOCKED",
                "data": {"verdict": qualification["verdict"]},
            }

        material = self._call("f130.materialize_initial", {
            "scope": request["scope"],
            "qualification_ref": qualification["qualification_ref"],
            "catalog_version": request["catalog_version"],
        })
        code = material_error(material)
        if code is not None:
            raise InvalidPortResponse(code)
        material = cast(dict[str, Any], material)

        return self._save_and_propose(
            tx,
            request,
            material,
            "INITIAL_OUTREACH",
            {
                "request_id": request["request_id"],
                "material_ref": material["material_ref"],
                "catalog_version": request["catalog_version"],
                "verdict": "QUALIFIED_FOR_PREPARATION",
            },
        )

    def _save_and_propose(self, tx, request, material, kind, data):
        """Сохраняем исходный descriptor и ставим действие в очередь."""
        material_ref = material["material_ref"]
        saved = self._call("materials.save", {
            "scope": request["scope"],
            "request_id": request["request_id"],
            "material": material,
        })
        code = save_response_error(saved, material_ref)
        if code is not None:
            # Выход исключением откатывает транзакцию.
            raise InvalidPortResponse(code)

        proposed = self._call("dispatch.propose", {
            "scope": request["scope"],
            "action_id": request["request_id"],
            "kind": kind,
            "payload_ref": material_ref,
        })
        code = dispatch_response_error(proposed)
        if code is not None:
            raise InvalidPortResponse(code)

        # В повтор кладём описание и исходные идентификаторы, не текст письма.
        tx.put("replay", replay_key(request), {
            "fingerprint": request_fingerprint(request),
            "scope": request["scope"],
            "request_id": request["request_id"],
            "catalog_version": request["catalog_version"],
            "material": {
                "material_ref": material["material_ref"],
                "sha256": material["sha256"],
                "approved_fact_ids": list(
                    material["approved_fact_ids"]
                ),
            },
            "data": data,
        })
        return {
            "status": "OK",
            "data": data,
        }


def create_service(ports):
    """Точка подключения, предусмотренная контрактом."""
    return MaterialsService(ports)
