"""Regression unit tests of PG failures; this SQL double is NOT a CP2 run.

The actual MaterialsService, PostgresPorts and PostgresTransaction execute
against issued Ports/fixtures. Only the SQL connection is replaced: it models
committed rows and per-connection pending rows, and injects genuine psycopg
exceptions. It does not model PostgreSQL locking, crashes or server restarts;
the separate real-PostgreSQL tests remain required for those properties.
"""

import copy
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "solution"))

try:
    import psycopg
except ImportError:
    psycopg = None

from ACCEPTANCE_RUNNER import Ports
from first_materials_runtime import acceptance_pg as pg
from first_materials_runtime.api import create_service


class CursorDouble:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class SQLDouble:
    """Single-threaded SQL boundary double; never represents a real PG test."""

    def __init__(self, fixture):
        scope = pg._scope_key(fixture["fixture_scope"])
        self.rows = {
            (scope, bucket, key): copy.deepcopy(value)
            for bucket, rows in fixture["seed"].items()
            for key, value in rows.items()
        }
        self.plans = []
        self.connections = []
        self.attempts = 0

    def connect(self):
        self.attempts += 1
        plan = self.plans.pop(0) if self.plans else {}
        if isinstance(plan, BaseException):
            raise plan
        connection = ConnectionDouble(self, plan)
        self.connections.append(connection)
        return connection


class ConnectionDouble:
    """commit_error=after persists rows before losing the acknowledgement."""

    def __init__(self, database, faults):
        self.database = database
        self.faults = faults
        self.pending = copy.deepcopy(database.rows)
        self.sql_calls = []
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0
        self.closed = False

    def execute(self, statement, parameters=()):
        text = (
            statement if isinstance(statement, str)
            else statement.as_string()
        )
        self.sql_calls.append((text, copy.deepcopy(parameters)))
        if text.startswith("SET TRANSACTION"):
            if self.faults.get("entry_error"):
                raise psycopg.OperationalError("injected opening failure")
            return CursorDouble()
        if ".scope_locks" in text:
            return CursorDouble([(parameters[0],)])
        if text.startswith("SELECT value FROM"):
            value = self.pending.get(tuple(parameters))
            return CursorDouble(
                [] if value is None else [(copy.deepcopy(value),)]
            )
        if text.startswith("INSERT INTO") and ".state" in text:
            self.pending[tuple(parameters[:3])] = json.loads(parameters[3])
            return CursorDouble()
        if text.startswith("DELETE FROM") and ".state" in text:
            self.pending.pop(tuple(parameters), None)
            return CursorDouble()
        if text.startswith("SELECT item_key,value FROM"):
            scope, bucket = parameters
            return CursorDouble(sorted(
                (key, copy.deepcopy(value))
                for (row_scope, row_bucket, key), value in self.pending.items()
                if (row_scope, row_bucket) == (scope, bucket)
            ))
        raise AssertionError("Unexpected SQL in boundary double: " + text)

    def commit(self):
        self.commit_calls += 1
        fault = self.faults.get("commit_error")
        if fault != "before":
            self.database.rows = copy.deepcopy(self.pending)
        if fault:
            raise psycopg.OperationalError("injected commit acknowledgement loss")

    def rollback(self):
        self.rollback_calls += 1
        self.pending = copy.deepcopy(self.database.rows)
        if self.faults.get("rollback_error"):
            raise psycopg.OperationalError("injected rollback acknowledgement loss")

    def close(self):
        self.close_calls += 1
        self.closed = True
        if self.faults.get("close_error"):
            raise psycopg.OperationalError("injected close failure")


class RecordingPorts(pg.PostgresPorts):
    def __init__(self, *args):
        super().__init__(*args)
        self.transactions = []

    def transaction(self, scope):
        transaction = super().transaction(scope)
        self.transactions.append(transaction)
        return transaction


@unittest.skipIf(psycopg is None, "psycopg is required for PG failure regression")
class PostgresFailureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        issued = json.loads(
            (ROOT / "acceptance" / "fixtures.json").read_text(encoding="utf-8")
        )
        cls.fixtures = [
            fixture for fixture in issued if fixture["name"] in {
                "approved_presentation", "initial_qualification_materials_auto_queue"
            }
        ]
        assert {item["operation"] for item in cls.fixtures} == {
            "prepare", "prepare_initial"
        }

    def setup_case(self, fixture):
        fixture = copy.deepcopy(fixture)
        materialize = (
            "f129.materialize" if fixture["operation"] == "prepare"
            else "f130.materialize_initial"
        )
        fixture["effects"] = {
            materialize: {"staging": {"draft": {"prepared": True}}},
            "materials.save": {"materials": {"draft": {"saved": True}}},
            "dispatch.propose": {"proposals": {"draft": {"proposed": True}}},
        }
        delegate = Ports(fixture)
        database = SQLDouble(fixture)
        adapter = RecordingPorts(database.connect, "b8_accept_failure_unit", delegate)
        service = create_service(adapter)  # No initialize(): execute boundary only.
        return fixture, delegate, database, adapter, service, materialize

    def run_request(self, fixture, service):
        return service.execute(fixture["operation"], fixture["request"])

    def assert_clean(self, adapter, database):
        self.assertIsNone(adapter._active)
        self.assertTrue(all(tx.connection is None for tx in adapter.transactions))
        self.assertTrue(all(connection.closed for connection in database.connections))

    def assert_effect_chain(self, delegate, count):
        names = [name for name, _ in delegate.calls]
        self.assertEqual(names.count("materials.save"), count)
        self.assertEqual(names.count("dispatch.propose"), count)
        self.assertNotIn("provider.send", names)

    def assert_no_effect_rows(self, database):
        self.assertFalse(any(
            bucket in {"staging", "materials", "proposals", "replay"}
            for _, bucket, _ in database.rows
        ))

    def test_connect_operational_error_before_effects_and_recovery(self):
        for fixture in self.fixtures:
            with self.subTest(operation=fixture["operation"]):
                fixture, delegate, database, adapter, service, _ = self.setup_case(fixture)
                database.plans.append(psycopg.OperationalError("connect failed"))
                self.assertEqual(self.run_request(fixture, service), {
                    "status": "DEPENDENCY_UNAVAILABLE", "data": {},
                })
                self.assertEqual(delegate.calls, [])
                self.assertEqual(delegate.writes, [])
                self.assertEqual(database.connections, [])
                self.assert_no_effect_rows(database)
                self.assert_clean(adapter, database)
                self.assertEqual(self.run_request(fixture, service), fixture["expect"])
                self.assert_effect_chain(delegate, 1)
                self.assert_clean(adapter, database)

    def test_entry_error_with_failing_cleanup_is_dependency_unavailable(self):
        for fixture in self.fixtures:
            for rollback_error in (False, True):
                with self.subTest(operation=fixture["operation"], rollback=rollback_error):
                    fixture, delegate, database, adapter, service, _ = self.setup_case(fixture)
                    database.plans.append({
                        "entry_error": True, "rollback_error": rollback_error,
                        "close_error": True,
                    })
                    self.assertEqual(self.run_request(fixture, service), {
                        "status": "DEPENDENCY_UNAVAILABLE", "data": {},
                    })
                    self.assertEqual(delegate.calls, [])
                    self.assert_no_effect_rows(database)
                    self.assertEqual(database.connections[0].commit_calls, 0)
                    self.assertEqual(database.connections[0].close_calls, 1)
                    self.assert_clean(adapter, database)
                    self.assertEqual(self.run_request(fixture, service), fixture["expect"])

    def test_confirmed_commit_close_error_preserves_ok_and_replay(self):
        for fixture in self.fixtures:
            with self.subTest(operation=fixture["operation"]):
                fixture, delegate, database, adapter, service, _ = self.setup_case(fixture)
                database.plans.append({"close_error": True})
                self.assertEqual(self.run_request(fixture, service), fixture["expect"])
                committed = copy.deepcopy(database.rows)
                first = database.connections[0]
                self.assertEqual((first.commit_calls, first.rollback_calls), (1, 0))
                self.assert_clean(adapter, database)
                database.plans.append({"close_error": True})
                self.assertEqual(self.run_request(fixture, service), fixture["repeat_expect"])
                self.assertEqual(database.rows, committed)
                self.assert_effect_chain(delegate, 1)
                self.assert_clean(adapter, database)

    def test_confirmed_rollback_close_error_preserves_original_response(self):
        for fixture in self.fixtures:
            for refusal in ("INVALID", "STALE", "BLOCKED", "TRANSPORT"):
                with self.subTest(operation=fixture["operation"], refusal=refusal):
                    fixture, delegate, database, adapter, service, materialize = self.setup_case(fixture)
                    database.plans.append({"close_error": True})
                    before = copy.deepcopy(database.rows)
                    state_before = copy.deepcopy(delegate.state)
                    if refusal == "INVALID":
                        delegate.responses[materialize]["sha256"] = "invalid"
                        expected = {"status": "INVALID", "data": {"code": "INVALID_HASH"}}
                    elif refusal in ("STALE", "BLOCKED"):
                        bucket, patch = (
                            ("catalog", {"version": "v2", "revision": 2})
                            if refusal == "STALE" else
                            ("authority", {"status": "STOPPED", "revision": 2})
                        )
                        delegate.effects["dispatch.propose"][bucket] = {"current": patch}
                        expected = {"status": refusal, "data": {}}
                    else:
                        original_call = delegate.call
                        original_error = ConnectionError("materialization disconnected")

                        def failing_call(name, request):
                            value = original_call(name, request)
                            if name == materialize:
                                raise original_error
                            return value

                        delegate.call = failing_call
                        expected = {"status": "DEPENDENCY_UNAVAILABLE", "data": {}}
                    self.assertEqual(self.run_request(fixture, service), expected)
                    self.assertEqual(database.rows, before)
                    self.assertEqual(delegate.state, state_before)
                    self.assertEqual(delegate.writes, [])
                    first = database.connections[0]
                    self.assertEqual((first.commit_calls, first.rollback_calls), (0, 1))
                    # No diagnostic retry is needed after a confirmed rollback.
                    self.assertEqual(database.attempts, 1)
                    self.assert_clean(adapter, database)

    def test_unknown_commit_close_error_has_no_automatic_effect_retry(self):
        for fixture in self.fixtures:
            for commit_error in ("before", "after"):
                with self.subTest(operation=fixture["operation"], commit=commit_error):
                    fixture, delegate, database, adapter, service, _ = self.setup_case(fixture)
                    database.plans.append({"commit_error": commit_error, "close_error": True})
                    self.assertEqual(self.run_request(fixture, service), {
                        "status": "UNKNOWN",
                        "data": {"request_id": fixture["request"]["request_id"]},
                    })
                    self.assert_effect_chain(delegate, 1)
                    self.assertEqual(database.attempts, 2)  # Read-only diagnostic transaction.
                    self.assertFalse(any(
                        text.startswith("INSERT INTO") and ".state" in text
                        for text, _ in database.connections[1].sql_calls
                    ))
                    self.assert_clean(adapter, database)
                    if commit_error == "after":
                        self.assertEqual(self.run_request(fixture, service), fixture["repeat_expect"])
                        self.assert_effect_chain(delegate, 1)
                    else:
                        self.assert_no_effect_rows(database)

    def test_unknown_rollback_close_error_has_no_effect_retry(self):
        for fixture in self.fixtures:
            with self.subTest(operation=fixture["operation"]):
                fixture, delegate, database, adapter, service, materialize = self.setup_case(fixture)
                delegate.responses[materialize]["sha256"] = "invalid"
                database.plans.append({"rollback_error": True, "close_error": True})
                self.assertEqual(self.run_request(fixture, service), {
                    "status": "UNKNOWN",
                    "data": {"request_id": fixture["request"]["request_id"]},
                })
                self.assertEqual([name for name, _ in delegate.calls].count(materialize), 1)
                self.assert_effect_chain(delegate, 0)
                self.assert_no_effect_rows(database)
                self.assertEqual(database.attempts, 2)
                self.assert_clean(adapter, database)

    def test_failed_nested_enter_keeps_outer_transaction_active(self):
        for fixture in self.fixtures:
            with self.subTest(operation=fixture["operation"]):
                fixture, _, database, adapter, _, _ = self.setup_case(fixture)
                with adapter.transaction(fixture["fixture_scope"]) as outer:
                    nested = adapter.transaction(fixture["fixture_scope"])
                    with self.assertRaisesRegex(RuntimeError, "nested"):
                        nested.__enter__()
                    self.assertIs(adapter._active, outer)
                    self.assertIsNone(nested.connection)
                    self.assertIsNotNone(outer.get("authority", "current"))
                    self.assertEqual(database.attempts, 1)
                self.assert_clean(adapter, database)


if __name__ == "__main__":
    unittest.main()
