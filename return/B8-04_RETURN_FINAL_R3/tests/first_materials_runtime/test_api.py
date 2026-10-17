"""Проверки входных данных и прав доступа B8-04."""

import unittest
from typing import Any
from types import SimpleNamespace

from first_materials_runtime.api import create_service


class MaterialsServiceTests(unittest.TestCase):
    def setUp(self):
        # Перед каждым тестом создаётся новый корректный запрос.
        self.request = {
            "scope": {
                "client_id": "client-a",
                "campaign_id": "00000000-0000-0000-0000-000000000001",
                "lead_id": "lead-a",
                "mailbox_ref": "mailbox-a",
                "thread_id": "thread-a",
            },
            "request_id": "request-a",
            "event_id": "event-a",
            "catalog_version": "v1",
            "kind": "PRODUCT_INFO",
        }

    def service(self, *, authenticated=True,
                capabilities=None, client_id="client-a"):
        # Это тестовый источник доверенных сведений о пользователе.
        if capabilities is None:
            capabilities = ["materials.prepare"]

        ports = SimpleNamespace(auth={
            "authenticated": authenticated,
            "capabilities": capabilities,
            "client_id": client_id,
        })
        return create_service(ports)

    def assert_invalid(self, operation, request):
        result = create_service(None).execute(operation, request)
        self.assertEqual(result, {
            "status": "INVALID",
            "data": {"code": "INVALID_REQUEST"},
        })

    def test_unknown_operation(self):
        self.assert_invalid("unknown", self.request)

    def test_request_must_be_dictionary(self):
        self.assert_invalid("prepare", None)

    def test_missing_field(self):
        del self.request["event_id"]
        self.assert_invalid("prepare", self.request)

    def test_extra_field_before_authentication(self):
        # Отсутствие ports не мешает отклонить неверный запрос.
        self.request["trusted"] = True
        self.assert_invalid("prepare", self.request)

    def test_unauthenticated(self):
        result = self.service(authenticated=False).execute(
            "prepare", self.request
        )
        self.assertEqual(result, {
            "status": "UNAUTHENTICATED",
            "data": {},
        })

    def test_missing_capability(self):
        result = self.service(capabilities=[]).execute(
            "prepare", self.request
        )
        self.assertEqual(result, {
            "status": "UNAUTHORIZED",
            "data": {},
        })

    def test_foreign_client(self):
        result = self.service(client_id="client-b").execute(
            "prepare", self.request
        )
        self.assertEqual(result, {
            "status": "UNAUTHORIZED",
            "data": {},
        })

    def test_missing_transaction(self):
        # Авторизация есть, интерфейс хранилища отсутствует.
        result = self.service().execute("prepare", self.request)

        self.assertEqual(result, {
            "status": "DEPENDENCY_UNAVAILABLE",
            "data": {},
        })

    def test_missing_call_port_precedes_terminal_state(self):
        state = {
            "authority": {
                "current": {"status": "STOPPED", "revision": 2},
            },
            "catalog": {
                "current": {"version": "v1", "revision": 1},
            },
        }
        service = self.service()
        service.ports.transaction = lambda scope: RestoringTransaction(
            state
        )

        result = service.execute("prepare", self.request)

        self.assertEqual(result, {
            "status": "DEPENDENCY_UNAVAILABLE",
            "data": {},
        })

    def test_missing_state(self):
        from contextlib import nullcontext
        from unittest.mock import Mock

        service = self.service()
        tx = Mock()

        # Проверяем отсутствие каждого обязательного состояния.
        for missing in ("authority", "catalog"):
            with self.subTest(missing=missing):
                states: dict[str, Any] = {
                    "authority": {"status": "ACTIVE", "revision": 1},
                    "catalog": {"version": "v1", "revision": 1},
                }
                states[missing] = None
                tx.get.side_effect = lambda bucket, key: states[bucket]
                service.ports.transaction = Mock(
                    return_value=nullcontext(tx)
                )

                result = service.execute("prepare", self.request)

                self.assertEqual(result, {
                    "status": "DEPENDENCY_UNAVAILABLE",
                    "data": {},
                })

    def test_invalid_state_revision_rejected(self):
        from contextlib import nullcontext
        from unittest.mock import Mock

        for bad_revision in (True, -1, "1"):
            with self.subTest(revision=bad_revision):
                service = self.service()
                tx = Mock()
                states = {
                    "authority": {
                        "status": "ACTIVE",
                        "revision": bad_revision,
                    },
                    "catalog": {"version": "v1", "revision": 1},
                }
                tx.get.side_effect = lambda bucket, key: states[bucket]
                service.ports.transaction = Mock(
                    return_value=nullcontext(tx)
                )
                service.ports.call = Mock()

                result = service.execute("prepare", self.request)

                self.assertEqual(result, {
                    "status": "INVALID",
                    "data": {"code": "INVALID_STATE"},
                })
                service.ports.call.assert_not_called()

    def test_terminal_states_block_without_effects(self):
        from contextlib import nullcontext
        from unittest.mock import Mock

        for status in ("STOPPED", "HUMAN"):
            with self.subTest(status=status):
                service = self.service()
                tx = Mock()

                states = {
                    "authority": {"status": status, "revision": 1},
                    "catalog": {"version": "v1", "revision": 1},
                }
                tx.get.side_effect = lambda bucket, key: states[bucket]

                service.ports.transaction = Mock(
                    return_value=nullcontext(tx)
                )
                service.ports.call = Mock()

                result = service.execute("prepare", self.request)

                self.assertEqual(result, {
                    "status": "BLOCKED",
                    "data": {},
                })

                # Запрещены записи и вызовы подготовки материалов.
                tx.put.assert_not_called()
                tx.remove.assert_not_called()
                service.ports.call.assert_not_called()

                # В хранилище передан исходный scope.
                service.ports.transaction.assert_called_once_with(
                    self.request["scope"]
                )
    def catalog_service(self):
        """Создаём сервис с активным состоянием и тестовыми портами."""
        from contextlib import nullcontext
        from unittest.mock import Mock

        service = self.service()
        tx = Mock()
        states = {
            "authority": {"status": "ACTIVE", "revision": 1},
            "catalog": {"version": "v1", "revision": 1},
        }
        tx.get.side_effect = lambda bucket, key: states.get(bucket)
        service.ports.transaction = Mock(
            return_value=nullcontext(tx)
        )
        service.ports.call = Mock()
        return service, tx

    def test_catalog_dependency_unavailable(self):
        service, tx = self.catalog_service()

        # Имитируем сигнал отсутствующей зависимости из стенда.
        class MissingDependency(RuntimeError):
            pass

        service.ports.call.side_effect = MissingDependency(
            "catalog.resolve"
        )

        result = service.execute("prepare", self.request)

        self.assertEqual(result, {
            "status": "DEPENDENCY_UNAVAILABLE",
            "data": {},
        })
        tx.put.assert_not_called()

    def test_unapproved_catalog_blocked(self):
        service, tx = self.catalog_service()
        service.ports.call.return_value = {
            "version": "v1",
            "bundle_ref": "bundle-a",
            "approved": False,
        }

        result = service.execute("prepare", self.request)

        self.assertEqual(result, {
            "status": "BLOCKED",
            "data": {},
        })
        service.ports.call.assert_called_once_with(
            "catalog.resolve",
            {
                "scope": self.request["scope"],
                "version": "v1",
            },
        )
        tx.put.assert_not_called()

    def test_catalog_version_mismatch(self):
        service, tx = self.catalog_service()
        service.ports.call.return_value = {
            "version": "v2",
            "bundle_ref": "bundle-a",
            "approved": True,
        }

        result = service.execute("prepare", self.request)

        self.assertEqual(result, {
            "status": "INVALID",
            "data": {"code": "CATALOG_VERSION_MISMATCH"},
        })
        tx.put.assert_not_called()

    def test_invalid_catalog_response(self):
        responses = [
            None,
            {},
            {"version": "v1", "bundle_ref": "bundle-a", "approved": 1},
            {"version": "v1", "bundle_ref": "", "approved": True},
            {
                "version": "v1",
                "bundle_ref": "bundle-a",
                "approved": True,
                "extra": True,
            },
        ]

        for response in responses:
            with self.subTest(response=response):
                service, tx = self.catalog_service()
                service.ports.call.return_value = response

                result = service.execute("prepare", self.request)

                self.assertEqual(result, {
                    "status": "INVALID",
                    "data": {"code": "INVALID_CATALOG_RESPONSE"},
                })
                tx.put.assert_not_called()

    def event_service(self, event):
        """Каталог корректен; вторым вызовом возвращаем событие."""
        service, tx = self.catalog_service()
        service.ports.call.side_effect = [
            {
                "version": "v1",
                "bundle_ref": "bundle-a",
                "approved": True,
            },
            event,
        ]
        return service, tx

    def test_invalid_event_response(self):
        service, tx = self.event_service(None)

        result = service.execute("prepare", self.request)

        self.assertEqual(result, {
            "status": "INVALID",
            "data": {"code": "INVALID_EVENT_RESPONSE"},
        })
        tx.put.assert_not_called()

    def test_event_identity_mismatch(self):
        service, tx = self.event_service({
            "event_id": "foreign-event",
            "kind": "HUMAN_QUESTION",
            "coverage_complete": True,
        })

        result = service.execute("prepare", self.request)

        self.assertEqual(result, {
            "status": "INVALID",
            "data": {"code": "EVENT_ID_MISMATCH"},
        })
        tx.put.assert_not_called()

    def test_event_scope_mismatch(self):
        foreign_scope = dict(self.request["scope"])
        foreign_scope["client_id"] = "client-b"

        service, tx = self.event_service({
            "event_id": "event-a",
            "scope": foreign_scope,
            "kind": "HUMAN_QUESTION",
            "coverage_complete": True,
        })

        result = service.execute("prepare", self.request)

        self.assertEqual(result, {
            "status": "INVALID",
            "data": {"code": "SCOPE_MISMATCH"},
        })
        tx.put.assert_not_called()

    def test_unsuitable_events_blocked_without_materials(self):
        for kind, coverage in (
            ("HUMAN_QUESTION", False),
            ("INTEREST", True),
            ("HUMAN_OBJECTION", True),
        ):
            with self.subTest(kind=kind, coverage=coverage):
                service, tx = self.event_service({
                    "event_id": "event-a",
                    "kind": kind,
                    "coverage_complete": coverage,
                })

                result = service.execute("prepare", self.request)

                self.assertEqual(result, {
                    "status": "BLOCKED",
                    "data": {},
                })
                tx.put.assert_not_called()

                # Были только чтения каталога и события.
                # Подготовка, сохранение и постановка в очередь не вызывались.
                self.assertEqual(
                    [
                        item.args[0]
                        for item in service.ports.call.call_args_list
                    ],
                    ["catalog.resolve", "d1.event"],
                )
                service.ports.call.assert_called_with(
                    "d1.event",
                    {
                        "scope": self.request["scope"],
                        "event_id": "event-a",
                    },
                )
    def test_invalid_material_response_rejected(self):
        event = {
            "event_id": "event-a",
            "kind": "HUMAN_QUESTION",
            "coverage_complete": True,
            "approved_fact_ids": ["fact-a"],
        }

        cases = [
            (None, "INVALID_MATERIAL_DESCRIPTOR"),
            ({}, "INVALID_MATERIAL_DESCRIPTOR"),
            (
                {
                    "material_ref": "material-a",
                    "sha256": "wrong",
                    "approved_fact_ids": ["fact-a"],
                },
                "INVALID_HASH",
            ),
        ]

        for material, expected_code in cases:
            with self.subTest(material=material):
                service, tx = self.event_service(event)

                # Ответы зависимостей идут в порядке вызова:
                # каталог → событие → подготовленный материал.
                service.ports.call.side_effect = [
                    {
                        "version": "v1",
                        "bundle_ref": "bundle-a",
                        "approved": True,
                    },
                    event,
                    material,
                ]

                actual = service.execute(
                    "prepare", self.request
                )

                self.assertEqual(
                    actual,
                    {
                        "status": "INVALID",
                        "data": {"code": expected_code},
                    },
                )
                tx.put.assert_not_called()
                tx.remove.assert_not_called()
                self.assertEqual(
                    service.ports.call.call_count, 3
                )

    def test_material_with_foreign_facts_rejected(self):
        event = {
            "event_id": "event-a",
            "kind": "HUMAN_QUESTION",
            "coverage_complete": True,
            "approved_fact_ids": ["fact-a"],
        }

        service, tx = self.event_service(event)
        service.ports.call.side_effect = [
            {
                "version": "v1",
                "bundle_ref": "bundle-a",
                "approved": True,
            },
            event,
            {
                "material_ref": "material-a",
                "sha256": "a" * 64,
                "approved_fact_ids": ["foreign-fact"],
            },
        ]

        actual = service.execute("prepare", self.request)

        self.assertEqual(
            actual,
            {
                "status": "INVALID",
                "data": {"code": "FACT_SCOPE_MISMATCH"},
            },
        )

        # Чужие факты не должны попасть в хранилище.
        tx.put.assert_not_called()
        tx.remove.assert_not_called()
        self.assertEqual(service.ports.call.call_count, 3)

    def materialization_service(self, outcome, mutate=None):
        """Порт материализации пишет staging в откатываемое состояние."""
        import copy

        state = {
            "authority": {
                "current": {"status": "ACTIVE", "revision": 1},
            },
            "catalog": {
                "current": {"version": "v1", "revision": 1},
            },
        }
        before = copy.deepcopy(state)
        event = {
            "event_id": "event-a",
            "kind": "HUMAN_QUESTION",
            "coverage_complete": True,
            "approved_fact_ids": ["fact-a"],
        }
        calls = []
        transactions = []

        def transaction(scope):
            tx = RestoringTransaction(state)
            transactions.append(tx)
            return tx

        def call(name, payload):
            calls.append((name, copy.deepcopy(payload)))
            if name == "catalog.resolve":
                return {
                    "version": "v1",
                    "bundle_ref": "bundle-a",
                    "approved": True,
                }
            if name == "d1.event":
                return event
            if name == "f129.materialize":
                transactions[-1].put(
                    "staging", "draft", {"partial": True}
                )
                if mutate is not None:
                    mutate(transactions[-1])
                if isinstance(outcome, BaseException):
                    raise outcome
                return outcome
            if name == "materials.save":
                return {"material_ref": outcome["material_ref"]}
            if name == "dispatch.propose":
                return {
                    "action_id": self.request["request_id"],
                    "state": "PREPARED",
                }
            raise AssertionError(name)

        service = self.service()
        service.ports.transaction = transaction
        service.ports.call = call
        return service, state, before, calls, transactions

    def test_source_domain_error_becomes_invalid_and_rolls_back(self):
        import sys
        from pathlib import Path

        source = Path(__file__).resolve().parents[2] / "reference_source"
        sys.path.insert(0, str(source))
        try:
            from sales_template_materialization import (
                TemplateMaterializationError,
            )
        finally:
            sys.path.remove(str(source))

        service, state, before, calls, transactions = (
            self.materialization_service(
                TemplateMaterializationError("UNAPPROVED_FACT")
            )
        )

        result = service.execute("prepare", self.request)

        self.assertEqual(result, {
            "status": "INVALID",
            "data": {"code": "UNAPPROVED_FACT"},
        })
        self.assertEqual(state, before)
        self.assertTrue(transactions[0].rolled_back)
        self.assertEqual(
            [name for name, _ in calls],
            ["catalog.resolve", "d1.event", "f129.materialize"],
        )
        self.assertNotIn("replay", state)

    def test_arbitrary_code_exception_is_not_a_domain_error(self):
        class PretendDomainError(RuntimeError):
            code = "UNAPPROVED_FACT"

        service, state, before, calls, transactions = (
            self.materialization_service(PretendDomainError())
        )

        with self.assertRaises(PretendDomainError):
            service.execute("prepare", self.request)

        self.assertEqual(state, before)
        self.assertTrue(transactions[0].rolled_back)

    def test_transport_error_with_confirmed_rollback_is_not_retried(self):
        service, state, before, calls, transactions = (
            self.materialization_service(TimeoutError("materializer"))
        )

        result = service.execute("prepare", self.request)

        self.assertEqual(result, {
            "status": "DEPENDENCY_UNAVAILABLE",
            "data": {},
        })
        self.assertEqual(state, before)
        self.assertTrue(transactions[0].rolled_back)
        self.assertEqual(
            [name for name, _ in calls],
            ["catalog.resolve", "d1.event", "f129.materialize"],
        )
        self.assertNotIn("replay", state)

    def test_foreign_facts_after_staging_fully_roll_back(self):
        material = {
            "material_ref": "material-a",
            "sha256": "a" * 64,
            "approved_fact_ids": ["foreign-fact"],
        }
        service, state, before, calls, transactions = (
            self.materialization_service(material)
        )

        result = service.execute("prepare", self.request)

        self.assertEqual(result, {
            "status": "INVALID",
            "data": {"code": "FACT_SCOPE_MISMATCH"},
        })
        self.assertEqual(state, before)
        self.assertTrue(transactions[0].rolled_back)
        self.assertEqual(
            [name for name, _ in calls],
            ["catalog.resolve", "d1.event", "f129.materialize"],
        )
        self.assertNotIn("replay", state)

    def test_invalid_descriptor_after_staging_fully_rolls_back(self):
        material = {
            "material_ref": "material-a",
            "sha256": "wrong",
            "approved_fact_ids": ["fact-a"],
        }
        service, state, before, calls, transactions = (
            self.materialization_service(material)
        )

        result = service.execute("prepare", self.request)

        self.assertEqual(result, {
            "status": "INVALID",
            "data": {"code": "INVALID_HASH"},
        })
        self.assertEqual(state, before)
        self.assertTrue(transactions[0].rolled_back)
        self.assertEqual(
            [name for name, _ in calls],
            ["catalog.resolve", "d1.event", "f129.materialize"],
        )
        self.assertNotIn("replay", state)

    def test_stop_during_materialization_rolls_back_before_save(self):
        material = {
            "material_ref": "material-a",
            "sha256": "a" * 64,
            "approved_fact_ids": ["fact-a"],
        }

        def stop(tx):
            tx.put("authority", "current", {
                "status": "STOPPED",
                "revision": 2,
            })

        service, state, before, calls, transactions = (
            self.materialization_service(material, mutate=stop)
        )

        result = service.execute("prepare", self.request)

        self.assertEqual(result, {"status": "BLOCKED", "data": {}})
        self.assertEqual(state, before)
        self.assertTrue(transactions[0].rolled_back)
        self.assertEqual(
            [name for name, _ in calls],
            ["catalog.resolve", "d1.event", "f129.materialize"],
        )

    def test_catalog_change_during_materialization_rolls_back(self):
        material = {
            "material_ref": "material-a",
            "sha256": "a" * 64,
            "approved_fact_ids": ["fact-a"],
        }

        def change_catalog(tx):
            tx.put("catalog", "current", {
                "version": "v2",
                "revision": 2,
            })

        service, state, before, calls, transactions = (
            self.materialization_service(
                material, mutate=change_catalog
            )
        )

        result = service.execute("prepare", self.request)

        self.assertEqual(result, {"status": "STALE", "data": {}})
        self.assertEqual(state, before)
        self.assertTrue(transactions[0].rolled_back)
        self.assertNotIn("materials.save", [name for name, _ in calls])

    def save_service(self, on_save, material=None, on_dispatch=None):
        """Сервис с настоящей транзакцией, которая умеет откатывать состояние."""
        import copy

        if material is None:
            material = {
                "material_ref": "material-a",
                "sha256": "a" * 64,
                "approved_fact_ids": ["fact-b", "fact-a"],
            }

        state = {
            "authority": {
                "current": {"status": "ACTIVE", "revision": 1},
            },
            "catalog": {
                "current": {"version": "v1", "revision": 1},
            },
        }
        before = copy.deepcopy(state)
        event = {
            "event_id": "event-a",
            "kind": "HUMAN_QUESTION",
            "coverage_complete": True,
            "approved_fact_ids": list(material["approved_fact_ids"]),
        }
        calls = []
        transactions = []

        def transaction(scope):
            tx = RestoringTransaction(state)
            transactions.append((scope, tx))
            return tx

        def call(name, request):
            calls.append((name, copy.deepcopy(request)))
            if name == "catalog.resolve":
                return {
                    "version": "v1",
                    "bundle_ref": "bundle-a",
                    "approved": True,
                }
            if name == "d1.event":
                return event
            if name == "f129.materialize":
                return material
            if name == "materials.save":
                return on_save(transactions[-1][1], request)
            if name == "dispatch.propose":
                if on_dispatch is None:
                    return {
                        "action_id": "action-a",
                        "state": "PREPARED",
                    }
                return on_dispatch(transactions[-1][1], request)
            raise AssertionError(name)

        service = self.service()
        service.ports.transaction = transaction
        service.ports.call = call
        return service, state, before, calls, transactions

    def test_invalid_materials_save_response_rolls_back(self):
        responses = (
            None,
            [],
            {},
            {"material_ref": "material-a", "extra": True},
            {"material_ref": ""},
            {"material_ref": "material-a\n"},
        )

        for response in responses:
            with self.subTest(response=response):
                def on_save(tx, request, response=response):
                    # Запись должна исчезнуть, если ответ сохранения неверный.
                    tx.put("materials", "draft", {"saved": True})
                    return response

                service, state, before, calls, transactions = (
                    self.save_service(on_save)
                )
                result = service.execute("prepare", self.request)

                self.assertEqual(result, {
                    "status": "INVALID",
                    "data": {"code": "INVALID_SAVE_RESPONSE"},
                })
                self.assertEqual(state, before)
                self.assertTrue(transactions[-1][1].rolled_back)
                self.assertEqual(
                    [name for name, request in calls],
                    [
                        "catalog.resolve",
                        "d1.event",
                        "f129.materialize",
                        "materials.save",
                    ],
                )

    def test_materials_save_ref_mismatch_rolls_back(self):
        def on_save(tx, request):
            tx.put("materials", "draft", {"saved": True})
            return {"material_ref": "material-b"}

        service, state, before, calls, transactions = (
            self.save_service(on_save)
        )
        result = service.execute("prepare", self.request)

        self.assertEqual(result, {
            "status": "INVALID",
            "data": {"code": "MATERIAL_REF_MISMATCH"},
        })
        self.assertEqual(state, before)
        self.assertTrue(transactions[-1][1].rolled_back)
        self.assertNotIn(
            "dispatch.propose",
            [name for name, request in calls],
        )

    def test_catalog_change_during_save_rolls_back_before_dispatch(self):
        def on_save(tx, request):
            tx.put("materials", "draft", {"saved": True})
            tx.put("catalog", "current", {
                "version": "v2",
                "revision": 2,
            })
            return {"material_ref": "material-a"}

        service, state, before, calls, transactions = (
            self.save_service(on_save)
        )

        result = service.execute("prepare", self.request)

        self.assertEqual(result, {"status": "STALE", "data": {}})
        self.assertEqual(state, before)
        self.assertTrue(transactions[-1][1].rolled_back)
        self.assertNotIn(
            "dispatch.propose", [name for name, _ in calls]
        )

    def test_missing_materials_save_rolls_back(self):
        class MissingDependency(RuntimeError):
            pass

        def on_save(tx, request):
            tx.put("materials", "draft", {"saved": True})
            raise MissingDependency("materials.save")

        service, state, before, calls, transactions = (
            self.save_service(on_save)
        )
        result = service.execute("prepare", self.request)

        self.assertEqual(result, {
            "status": "DEPENDENCY_UNAVAILABLE",
            "data": {},
        })
        self.assertEqual(state, before)
        self.assertTrue(transactions[-1][1].rolled_back)
        self.assertNotIn(
            "dispatch.propose",
            [name for name, request in calls],
        )

    def test_materials_save_exact_request_then_dispatch(self):
        material = {
            "material_ref": "material-a",
            "sha256": "a" * 64,
            "approved_fact_ids": ["fact-b", "fact-a"],
        }

        def on_save(tx, request):
            tx.put("materials", "draft", {"saved": True})
            return {"material_ref": "material-a"}

        service, state, before, calls, transactions = (
            self.save_service(on_save, material)
        )
        result = service.execute("prepare", self.request)

        self.assertEqual(result, {
            "status": "OK",
            "data": {
                "request_id": "request-a",
                "material_ref": "material-a",
                "catalog_version": "v1",
            },
        })
        save_call = next(
            request for name, request in calls
            if name == "materials.save"
        )
        self.assertEqual(save_call, {
            "scope": self.request["scope"],
            "request_id": "request-a",
            "material": material,
        })
        self.assertEqual(
            save_call["material"]["approved_fact_ids"],
            ["fact-b", "fact-a"],
        )
        self.assertEqual(calls[-1], (
            "dispatch.propose",
            {
                "scope": self.request["scope"],
                "action_id": "request-a",
                "kind": "PRODUCT_INFO",
                "payload_ref": "material-a",
            },
        ))
        self.assertIn("replay", state)
        self.assertNotEqual(state, before)
        self.assertFalse(transactions[-1][1].rolled_back)

    def test_invalid_dispatch_response_rolls_back(self):
        def on_save(tx, request):
            tx.put("materials", "draft", {"saved": True})
            return {"material_ref": "material-a"}

        def on_dispatch(tx, request):
            return {"action_id": "action-a", "state": "SENT"}

        service, state, before, calls, transactions = (
            self.save_service(on_save, on_dispatch=on_dispatch)
        )
        result = service.execute("prepare", self.request)

        self.assertEqual(result, {
            "status": "INVALID",
            "data": {"code": "INVALID_DISPATCH_RESPONSE"},
        })
        self.assertEqual(state, before)
        self.assertTrue(transactions[-1][1].rolled_back)
        self.assertNotIn("provider.send", [name for name, _ in calls])

    def test_uncertain_commit_returns_unknown_without_retrying_effects(self):
        def on_save(tx, request):
            tx.put("materials", "draft", {"saved": True})
            return {"material_ref": "material-a"}

        service, state, before, calls, transactions = (
            self.save_service(on_save)
        )

        def transaction(scope):
            if not transactions:
                tx = UncertainCommitTransaction(state)
            else:
                tx = RestoringTransaction(state)
            transactions.append((scope, tx))
            return tx

        service.ports.transaction = transaction

        result = service.execute("prepare", self.request)

        self.assertEqual(result, {
            "status": "UNKNOWN",
            "data": {"request_id": "request-a"},
        })
        self.assertEqual(
            [name for name, _ in calls],
            [
                "catalog.resolve",
                "d1.event",
                "f129.materialize",
                "materials.save",
                "dispatch.propose",
            ],
        )
        self.assertEqual(len(transactions), 2)
        self.assertIn("replay", state)

    def test_same_request_replays_without_new_effects(self):
        def on_save(tx, request):
            tx.put("materials", "draft", {"saved": True})
            return {"material_ref": "material-a"}

        service, state, before, calls, transactions = (
            self.save_service(on_save)
        )
        import copy
        first = service.execute("prepare", self.request)
        committed = copy.deepcopy(state)
        seen = len(calls)

        second = service.execute("prepare", self.request)

        self.assertEqual(first["status"], "OK")
        self.assertEqual(second, {
            "status": "REPLAY",
            "data": first["data"],
        })
        self.assertEqual(state, committed)
        self.assertEqual(calls[seen:], [])
        self.assertFalse(transactions[-1][1].rolled_back)

    def test_changed_request_conflicts_without_new_effects(self):
        def on_save(tx, request):
            return {"material_ref": "material-a"}

        service, state, before, calls, transactions = (
            self.save_service(on_save)
        )
        import copy
        service.execute("prepare", self.request)
        committed = copy.deepcopy(state)
        seen = len(calls)
        changed = dict(self.request)
        changed["catalog_version"] = "v2"

        result = service.execute("prepare", changed)

        self.assertEqual(result, {
            "status": "CONFLICT",
            "data": {},
        })
        self.assertEqual(state, committed)
        self.assertEqual(calls[seen:], [])

    def test_initial_qualification_saves_and_queues(self):
        request = {
            "scope": {
                "client_id": "client-a",
                "campaign_id": "00000000-0000-0000-0000-000000000001",
                "lead_id": "lead-a",
                "mailbox_ref": "mailbox-a",
                "thread_id": None,
            },
            "request_id": "initial-a",
            "candidate_ref": "candidate-a",
            "assessment_ref": "assessment-a",
            "catalog_version": "v1",
        }
        material = {
            "material_ref": "material-initial",
            "sha256": "a" * 64,
            "approved_fact_ids": ["fact-a"],
        }
        service, state, before, calls, transactions = (
            self.initial_service(request, material, "QUALIFIED_FOR_PREPARATION")
        )
        result = service.execute("prepare_initial", request)

        self.assertEqual(result, {
            "status": "OK",
            "data": {
                "request_id": "initial-a",
                "material_ref": "material-initial",
                "catalog_version": "v1",
                "verdict": "QUALIFIED_FOR_PREPARATION",
            },
        })
        self.assertEqual(
            [name for name, _ in calls],
            [
                "catalog.resolve",
                "f129.qualify",
                "f130.materialize_initial",
                "materials.save",
                "dispatch.propose",
            ],
        )
        self.assertEqual(calls[-1][1]["kind"], "INITIAL_OUTREACH")
        self.assertEqual(calls[-1][1]["action_id"], "initial-a")
        self.assertIn("replay", state)

    def test_initial_needs_review_does_not_save(self):
        request = {
            "scope": {
                "client_id": "client-a",
                "campaign_id": "00000000-0000-0000-0000-000000000001",
                "lead_id": "lead-a",
                "mailbox_ref": "mailbox-a",
                "thread_id": None,
            },
            "request_id": "initial-a",
            "candidate_ref": "candidate-a",
            "assessment_ref": "assessment-a",
            "catalog_version": "v1",
        }
        service, state, before, calls, transactions = (
            self.initial_service(request, None, "NEEDS_REVIEW")
        )
        result = service.execute("prepare_initial", request)

        self.assertEqual(result, {
            "status": "BLOCKED",
            "data": {"verdict": "NEEDS_REVIEW"},
        })
        self.assertEqual(
            [name for name, _ in calls],
            ["catalog.resolve", "f129.qualify"],
        )
        self.assertEqual(state, before)

    def test_initial_invalid_descriptor_after_staging_rolls_back(self):
        request = {
            "scope": {
                "client_id": "client-a",
                "campaign_id": "00000000-0000-0000-0000-000000000001",
                "lead_id": "lead-a",
                "mailbox_ref": "mailbox-a",
                "thread_id": None,
            },
            "request_id": "initial-a",
            "candidate_ref": "candidate-a",
            "assessment_ref": "assessment-a",
            "catalog_version": "v1",
        }
        invalid_material = {
            "material_ref": "material-initial",
            "sha256": "wrong",
            "approved_fact_ids": ["fact-a"],
        }
        service, state, before, calls, transactions = self.initial_service(
            request,
            invalid_material,
            "QUALIFIED_FOR_PREPARATION",
            stage_material=True,
        )

        result = service.execute("prepare_initial", request)

        self.assertEqual(result, {
            "status": "INVALID",
            "data": {"code": "INVALID_HASH"},
        })
        self.assertEqual(state, before)
        self.assertTrue(transactions[0][1].rolled_back)
        self.assertEqual(
            [name for name, _ in calls],
            [
                "catalog.resolve",
                "f129.qualify",
                "f130.materialize_initial",
            ],
        )
        self.assertNotIn("replay", state)

    def initial_service(
        self, request, material, verdict, *, stage_material=False
    ):
        """Транзакция и ответы для первого контакта."""
        import copy

        state = {
            "authority": {
                "current": {"status": "ACTIVE", "revision": 1},
            },
            "catalog": {
                "current": {"version": "v1", "revision": 1},
            },
        }
        before = copy.deepcopy(state)
        calls = []
        transactions = []

        def transaction(scope):
            tx = RestoringTransaction(state)
            transactions.append((scope, tx))
            return tx

        def call(name, payload):
            calls.append((name, copy.deepcopy(payload)))
            if name == "catalog.resolve":
                return {
                    "version": "v1",
                    "bundle_ref": "bundle-a",
                    "approved": True,
                }
            if name == "f129.qualify":
                return {
                    "verdict": verdict,
                    "qualification_ref": "qualification-a",
                    "reason_codes": ["QUALIFICATION_INPUTS_SUFFICIENT"],
                }
            if name == "f130.materialize_initial":
                if stage_material:
                    transactions[-1][1].put(
                        "staging", "initial-draft", {"partial": True}
                    )
                return material
            if name == "materials.save":
                return {"material_ref": material["material_ref"]}
            if name == "dispatch.propose":
                return {"action_id": "initial-action", "state": "PREPARED"}
            raise AssertionError(name)

        service = self.service()
        service.ports.transaction = transaction
        service.ports.call = call
        return service, state, before, calls, transactions


class RestoringTransaction:
    """Тестовая транзакция: исключение возвращает прежнее состояние."""

    def __init__(self, state):
        self.state = state
        self.rolled_back = False

    def __enter__(self):
        import copy
        self._before = copy.deepcopy(self.state)
        return self

    def __exit__(self, exc_type, exc, tb):
        import copy
        if exc_type is not None:
            self.state.clear()
            self.state.update(copy.deepcopy(self._before))
            self.rolled_back = True
        return False

    def get(self, bucket, key):
        import copy
        value = self.state.get(bucket, {}).get(key)
        return copy.deepcopy(value)

    def put(self, bucket, key, value):
        import copy
        self.state.setdefault(bucket, {})[key] = copy.deepcopy(value)

    def remove(self, bucket, key):
        self.state.setdefault(bucket, {}).pop(key, None)


class UncertainCommitTransaction(RestoringTransaction):
    """Имитирует timeout после тела, когда результат commit неизвестен."""

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            return super().__exit__(exc_type, exc, tb)
        raise TimeoutError("commit outcome is unknown")


class MaterialDescriptorTests(unittest.TestCase):
    """Проверяем описание материала отдельно от хранилища."""

    def setUp(self):
        self.material = {
            "material_ref": "material-a",
            "sha256": "a" * 64,
            "approved_fact_ids": ["fact-a", "fact-b"],
        }

    def test_valid_material_descriptor(self):
        from first_materials_runtime.api import material_error

        self.assertIsNone(material_error(self.material))

        # Пустой список фактов также допустим по формату.
        self.material["approved_fact_ids"] = []
        self.assertIsNone(material_error(self.material))

    def test_invalid_material_descriptor(self):
        from first_materials_runtime.api import material_error

        invalid_materials = [
            None,
            {},
            {**self.material, "extra": True},
            {**self.material, "material_ref": ""},
            {**self.material, "material_ref": " material-a"},
            {**self.material, "material_ref": "material\n"},
        ]

        for material in invalid_materials:
            with self.subTest(material=material):
                self.assertEqual(
                    material_error(material),
                    "INVALID_MATERIAL_DESCRIPTOR",
                )

    def test_invalid_material_hash(self):
        from first_materials_runtime.api import material_error

        # SHA-256: ровно 64 символа из 0–9 и a–f.
        for digest in (None, "", "a" * 63, "a" * 65,
                       "A" * 64, "g" * 64):
            with self.subTest(digest=digest):
                material = {
                    **self.material,
                    "sha256": digest,
                }

                self.assertEqual(
                    material_error(material),
                    "INVALID_HASH",
                )

    def test_invalid_material_fact_ids(self):
        from first_materials_runtime.api import material_error

        invalid_facts = [
            None,
            "fact-a",
            ["fact-a", "fact-a"],
            [""],
            [" fact-a"],
            ["fact\n"],
            [42],
        ]

        for facts in invalid_facts:
            with self.subTest(facts=facts):
                material = {
                    **self.material,
                    "approved_fact_ids": facts,
                }

                self.assertEqual(
                    material_error(material),
                    "INVALID_MATERIAL_DESCRIPTOR",
                )

if __name__ == "__main__":
    unittest.main()
