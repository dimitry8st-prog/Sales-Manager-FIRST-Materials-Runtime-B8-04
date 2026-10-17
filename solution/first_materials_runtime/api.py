"""Подготовка утверждённых материалов: компонент B8-04."""

from typing import Any, ContextManager, cast
from uuid import UUID


# Точный набор полей для каждой операции.
REQUEST_FIELDS = {
    "prepare": {
        "scope",
        "request_id",
        "event_id",
        "catalog_version",
        "kind",
    },
    "prepare_initial": {
        "scope",
        "request_id",
        "candidate_ref",
        "assessment_ref",
        "catalog_version",
    },
}

SCOPE_FIELDS = {
    "client_id",
    "campaign_id",
    "lead_id",
    "mailbox_ref",
    "thread_id",
}


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

    if type(scope) is not dict:
        return False

    if set(scope) != SCOPE_FIELDS:
        return False

    for field in SCOPE_FIELDS:
        value = scope[field]

        if field == "thread_id" and operation == "prepare_initial":
            # До первого сообщения thread ещё отсутствует.
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

class DependencyUnavailable(RuntimeError):
    """Обязательная зависимость недоступна."""

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
        try:
            context = cast(
                ContextManager[Any],
                transaction(request["scope"]),
            )
            with context as tx:
                return self._execute_in_transaction(
                    tx, operation, request
                )
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
    def _call(self, name, request):
        """Вызываем зависимость через доверенный интерфейс."""
        call = getattr(self.ports, "call", None)

        if not callable(call):
            raise DependencyUnavailable(name)

        try:
            return call(name, request)
        except Exception as exc:
            if type(exc).__name__ == "MissingDependency":
                raise DependencyUnavailable(name) from exc
            raise

    def _execute_in_transaction(self, tx, operation, request):
        """Проверяем состояние перед подготовкой материалов."""
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
            "STOPPED",
            "HUMAN",
            "HANDED_OFF",
        ):
            return {
                "status": "BLOCKED",
                "data": {},
            }

        # Продолжение возможно только при явном ACTIVE.
        if authority.get("status") != "ACTIVE":
            return {
                "status": "INVALID",
                "data": {"code": "INVALID_STATE"},
            }


        # Запрашиваем нужную версию каталога через доверенный порт.
        resolved = self._call("catalog.resolve", {
            "scope": request["scope"],
            "version": request["catalog_version"],
        })

        # Проверяем точный состав ответа.
        if (
            type(resolved) is not dict
            or set(resolved) != {"version", "bundle_ref", "approved"}
        ):
            return {
                "status": "INVALID",
                "data": {"code": "INVALID_CATALOG_RESPONSE"},
            }

        # Проверяем типы и идентификаторы.
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

        # Неутверждённые материалы использовать запрещено.
        if not resolved["approved"]:
            return {
                "status": "BLOCKED",
                "data": {},
            }

        if operation == "prepare":
            # Получаем событие из истории указанного диалога.
            event = self._call("d1.event", {
                "scope": request["scope"],
                "event_id": request["event_id"],
            })

            if type(event) is not dict:
                return {
                    "status": "INVALID",
                    "data": {"code": "INVALID_EVENT_RESPONSE"},
                }

            # Зависимость должна вернуть именно запрошенное событие.
            if event.get("event_id") != request["event_id"]:
                return {
                    "status": "INVALID",
                    "data": {"code": "EVENT_ID_MISMATCH"},
                }

            # Если scope присутствует в ответе, он обязан совпадать.
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

            # Только информационный вопрос допускает эту ветку.
            # INTEREST обрабатывается отдельной веткой передачи человеку.
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

            # Пока проверяем, что зависимость вернула словарь.
            # Проверку всех полей результата добавим следующим шагом.
            if type(material) is not dict:
                return {
                    "status": "INVALID",
                    "data": {"code": "INVALID_MATERIAL_DESCRIPTOR"},
                }

            raise NotImplementedError(
                "Материал получен; проверка и сохранение ещё не добавлены"
            )

        # Первый контакт требует отдельной квалификации кандидата.
        raise NotImplementedError(
            "Ветка prepare_initial ещё не добавлена"
        )


# Функция находится ВНЕ класса: перед def нет пробелов.
def create_service(ports):
    """Точка подключения, предусмотренная контрактом."""
    return MaterialsService(ports)