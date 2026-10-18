"""R6: ошибки драйвера на границах настоящих PostgreSQL-транзакций.

SQL исполняется сервером, состояние проверяется независимым соединением.
Только выбранный метод соединения получает управляемый OperationalError;
бизнес-ответы и реальные эффекты MaterialsService не подменяются.
"""

import copy
import json
import os
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class FaultConnection:
    """Одноразовый failpoint вокруг реального соединения psycopg."""

    def __init__(self, raw, fault):
        self.raw = raw
        self.fault = fault
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    def execute(self, *args, **kwargs):
        if self.fault == "entry_cleanup":
            import psycopg
            raise psycopg.OperationalError("injected entry failure")
        return self.raw.execute(*args, **kwargs)

    def commit(self):
        self.commits += 1
        self.raw.commit()
        if self.fault == "unknown_commit":
            import psycopg
            # Сервер уже зафиксировал данные, подтверждение клиент потерял.
            raise psycopg.OperationalError("injected lost commit response")

    def rollback(self):
        self.rollbacks += 1
        if self.fault in ("unknown_rollback", "entry_cleanup"):
            import psycopg
            raise psycopg.OperationalError("injected rollback failure")
        self.raw.rollback()

    def close(self):
        self.closes += 1
        self.raw.close()
        import psycopg
        raise psycopg.OperationalError("injected close failure")


@unittest.skipUnless(os.environ.get("B8_TEST_DSN"), "real PostgreSQL DSN required")
class PostgresCleanupRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from ACCEPTANCE_RUNNER import Ports
        from first_materials_runtime import acceptance_pg
        from first_materials_runtime.api import create_service

        cls.psycopg = psycopg
        cls.Ports = Ports
        cls.pg = acceptance_pg
        cls.make_service = staticmethod(create_service)
        cls.fixtures = [
            f for f in json.loads((ROOT / "acceptance/fixtures.json").read_text())
            if f["name"] in (
                "approved_presentation", "initial_qualification_materials_auto_queue"
            )
        ]

    def connect(self):
        return self.psycopg.connect(os.environ["B8_TEST_DSN"])

    def fixture(self, original):
        fixture = copy.deepcopy(original)
        materializer = (
            "f129.materialize" if fixture["operation"] == "prepare"
            else "f130.materialize_initial"
        )
        fixture["effects"] = {
            materializer: {"staging": {"current-request": {"written": True}}},
            "materials.save": {"materials": {"current-request": {"saved": True}}},
            "dispatch.propose": {"proposals": {"current-request": {"prepared": True}}},
        }
        return fixture

    def service(self, fixture, fault):
        from first_materials_runtime.acceptance_pg import PostgresPorts

        base = os.environ.get("B8_TEST_SCHEMA", "b8_accept_r6")
        suffix = self._testMethodName.removeprefix("test_")[:10]
        # Разные схемы для каждого теста и operation, без общего состояния.
        schema = f"{base}_{suffix}_{'i' if fixture['operation']=='prepare_initial' else 'p'}"
        self.pg.seed(self.connect, schema, fixture)
        delegate = self.Ports(fixture)
        connections = []
        transactions = []
        first = True

        def factory():
            nonlocal first
            if first:
                first = False
                if fault == "connect":
                    raise self.psycopg.OperationalError("injected connect failure")
                connection = FaultConnection(self.connect(), fault)
                connections.append(connection)
                self.addCleanup(connection.raw.close)
                return connection
            return self.connect()

        class RecordingPorts(PostgresPorts):
            def transaction(self, scope):
                transaction = super().transaction(scope)
                transactions.append(transaction)
                return transaction

        backend = RecordingPorts(factory, schema, delegate)
        return self.make_service(backend), backend, delegate, schema, connections, transactions

    def counts(self, schema, scope):
        from psycopg import sql

        with self.connect() as connection:
            rows = connection.execute(
                sql.SQL("SELECT bucket,count(*) FROM {}.state WHERE scope_key=%s GROUP BY bucket")
                .format(sql.Identifier(schema)),
                (self.pg._scope_key(scope),),
            ).fetchall()
        buckets = dict(rows)
        return {key: buckets.get(key, 0) for key in ("staging", "materials", "proposals", "replay")}

    def assert_detached(self, backend, transactions):
        self.assertIsNone(backend._active)
        self.assertTrue(transactions)
        self.assertTrue(all(tx.connection is None for tx in transactions))

    def report(self, fixture, result, counts, delegate):
        print(json.dumps({
            "test": self._testMethodName, "operation": fixture["operation"],
            "result": result, "database_effect_counts": counts,
            "save_calls": sum(name == "materials.save" for name, _ in delegate.calls),
            "propose_calls": sum(name == "dispatch.propose" for name, _ in delegate.calls),
        }, sort_keys=True))

    def test_connect_operational_error_before_effects(self):
        for original in self.fixtures:
            with self.subTest(operation=original["operation"]):
                fixture = self.fixture(original)
                service, backend, delegate, schema, _, transactions = self.service(fixture, "connect")
                result = service.execute(fixture["operation"], fixture["request"])
                self.assertEqual(result, {"status": "DEPENDENCY_UNAVAILABLE", "data": {}})
                self.assertEqual(delegate.calls, [])
                self.assertEqual(delegate.writes, [])
                self.assert_detached(backend, transactions)
                counts = self.counts(schema, fixture["fixture_scope"])
                self.assertEqual(set(counts.values()), {0})
                self.report(fixture, result, counts, delegate)
                self.assertEqual(service.execute(fixture["operation"], fixture["request"])["status"], "OK")

    def test_commit_confirmed_close_fails_preserves_ok_and_replay(self):
        for original in self.fixtures:
            with self.subTest(operation=original["operation"]):
                fixture = self.fixture(original)
                service, backend, delegate, schema, connections, transactions = self.service(fixture, "close")
                result = service.execute(fixture["operation"], fixture["request"])
                self.assertEqual(result, fixture["expect"])
                calls = copy.deepcopy(delegate.calls)
                replay = service.execute(fixture["operation"], fixture["request"])
                self.assertEqual(replay, {"status": "REPLAY", "data": result["data"]})
                self.assertEqual(delegate.calls, calls)
                self.assertEqual((connections[0].commits, connections[0].rollbacks, connections[0].closes), (1, 0, 1))
                self.assert_detached(backend, transactions)
                counts = self.counts(schema, fixture["fixture_scope"])
                self.assertEqual(set(counts.values()), {1})
                self.report(fixture, result, counts, delegate)

    def test_rollback_confirmed_close_fails_preserves_stale(self):
        for original in self.fixtures:
            with self.subTest(operation=original["operation"]):
                fixture = self.fixture(original)
                fixture["effects"]["dispatch.propose"]["catalog"] = {"current": {"version": "v2"}}
                service, backend, delegate, schema, connections, transactions = self.service(fixture, "close")
                result = service.execute(fixture["operation"], fixture["request"])
                self.assertEqual(result, {"status": "STALE", "data": {}})
                self.assertEqual(delegate.state, fixture["seed"])
                self.assertEqual(delegate.writes, [])
                self.assertEqual((connections[0].commits, connections[0].rollbacks, connections[0].closes), (0, 1, 1))
                self.assert_detached(backend, transactions)
                counts = self.counts(schema, fixture["fixture_scope"])
                self.assertEqual(set(counts.values()), {0})
                self.report(fixture, result, counts, delegate)

    def test_body_transport_rollback_confirmed_close_fails(self):
        for original in self.fixtures:
            with self.subTest(operation=original["operation"]):
                fixture = self.fixture(original)
                service, backend, delegate, schema, _, transactions = self.service(fixture, "close")
                original_call = delegate.call

                def fail_after_proposal(name, request):
                    result = original_call(name, request)
                    if name == "dispatch.propose":
                        raise ConnectionError("injected dependency error after staging")
                    return result

                delegate.call = fail_after_proposal
                result = service.execute(fixture["operation"], fixture["request"])
                self.assertEqual(result, {"status": "DEPENDENCY_UNAVAILABLE", "data": {}})
                self.assertEqual(delegate.state, fixture["seed"])
                self.assertEqual(delegate.writes, [])
                self.assert_detached(backend, transactions)
                counts = self.counts(schema, fixture["fixture_scope"])
                self.assertEqual(set(counts.values()), {0})
                self.report(fixture, result, counts, delegate)

    def test_unknown_commit_close_error_does_not_repeat_effects(self):
        for original in self.fixtures:
            with self.subTest(operation=original["operation"]):
                fixture = self.fixture(original)
                service, backend, delegate, schema, _, transactions = self.service(fixture, "unknown_commit")
                result = service.execute(fixture["operation"], fixture["request"])
                self.assertEqual(result, {"status": "UNKNOWN", "data": {"request_id": fixture["request"]["request_id"]}})
                calls = copy.deepcopy(delegate.calls)
                self.assertEqual(sum(name == "materials.save" for name, _ in calls), 1)
                self.assertEqual(sum(name == "dispatch.propose" for name, _ in calls), 1)
                self.assertGreaterEqual(len(transactions), 2)  # diagnostic scoped reread
                replay = service.execute(fixture["operation"], fixture["request"])
                self.assertEqual(replay["status"], "REPLAY")
                self.assertEqual(delegate.calls, calls)
                self.assert_detached(backend, transactions)
                counts = self.counts(schema, fixture["fixture_scope"])
                self.assertEqual(set(counts.values()), {1})
                self.report(fixture, result, counts, delegate)

    def test_unknown_rollback_close_error_does_not_repeat_effects(self):
        for original in self.fixtures:
            with self.subTest(operation=original["operation"]):
                fixture = self.fixture(original)
                fixture["effects"]["dispatch.propose"]["catalog"] = {"current": {"version": "v2"}}
                service, backend, delegate, schema, _, transactions = self.service(fixture, "unknown_rollback")
                result = service.execute(fixture["operation"], fixture["request"])
                self.assertEqual(result, {"status": "UNKNOWN", "data": {"request_id": fixture["request"]["request_id"]}})
                self.assertEqual(sum(name == "materials.save" for name, _ in delegate.calls), 1)
                self.assertEqual(sum(name == "dispatch.propose" for name, _ in delegate.calls), 1)
                self.assertEqual(delegate.state, fixture["seed"])
                self.assertEqual(delegate.writes, [])
                self.assertGreaterEqual(len(transactions), 2)
                self.assert_detached(backend, transactions)
                counts = self.counts(schema, fixture["fixture_scope"])
                self.assertEqual(set(counts.values()), {0})
                self.report(fixture, result, counts, delegate)

    def test_entry_error_with_rollback_and_close_errors_is_dependency_unavailable(self):
        for original in self.fixtures:
            with self.subTest(operation=original["operation"]):
                fixture = self.fixture(original)
                service, backend, delegate, schema, connections, transactions = self.service(fixture, "entry_cleanup")
                result = service.execute(fixture["operation"], fixture["request"])
                self.assertEqual(result, {"status": "DEPENDENCY_UNAVAILABLE", "data": {}})
                self.assertEqual(delegate.calls, [])
                self.assertEqual(delegate.writes, [])
                self.assertEqual((connections[0].commits, connections[0].rollbacks, connections[0].closes), (0, 1, 1))
                self.assert_detached(backend, transactions)
                counts = self.counts(schema, fixture["fixture_scope"])
                self.assertEqual(set(counts.values()), {0})
                self.report(fixture, result, counts, delegate)


if __name__ == "__main__":
    unittest.main()
