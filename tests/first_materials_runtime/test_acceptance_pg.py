"""Дополнительные CP2-проверки на настоящем PostgreSQL."""

import copy
import json
import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(
    os.environ.get("B8_TEST_DSN"),
    "B8_TEST_DSN is required for real PostgreSQL tests",
)
class PostgresAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        from ACCEPTANCE_RUNNER import Ports
        from first_materials_runtime import acceptance_pg

        cls.psycopg = psycopg
        cls.Ports = Ports
        cls.pg = acceptance_pg
        cls.dsn = os.environ["B8_TEST_DSN"]
        cls.base_schema = os.environ.get(
            "B8_TEST_SCHEMA", "b8_accept_b804_component"
        )
        fixtures = json.loads(
            (ROOT / "acceptance" / "fixtures.json").read_text(
                encoding="utf-8"
            )
        )
        cls.fixture = next(
            item for item in fixtures
            if item["name"] == "approved_presentation"
        )

    def connect(self):
        return self.psycopg.connect(self.dsn)

    def schema(self, suffix):
        return f"{self.base_schema}_{suffix}"

    def state_count(self, schema, scope, bucket=None):
        from psycopg import sql

        scope_key = self.pg._scope_key(scope)
        query = sql.SQL(
            "SELECT count(*) FROM {}.state WHERE scope_key=%s"
        ).format(sql.Identifier(schema))
        params = [scope_key]
        if bucket is not None:
            query += sql.SQL(" AND bucket=%s")
            params.append(bucket)
        with self.connect() as connection:
            return connection.execute(query, params).fetchone()[0]

    def state_value(self, schema, scope, bucket, key):
        from psycopg import sql

        with self.connect() as connection:
            row = connection.execute(
                sql.SQL(
                    "SELECT value FROM {}.state "
                    "WHERE scope_key=%s AND bucket=%s AND item_key=%s"
                ).format(sql.Identifier(schema)),
                (self.pg._scope_key(scope), bucket, key),
            ).fetchone()
        return None if row is None else row[0]

    def setup_control(self, schema, fixture):
        from psycopg import sql

        self.pg.seed(self.connect, schema, fixture)
        with self.connect() as connection:
            connection.execute(
                sql.SQL(
                    "CREATE TABLE IF NOT EXISTS {}.control_events("
                    "event text PRIMARY KEY, detail jsonb NOT NULL)"
                ).format(sql.Identifier(schema))
            )
            connection.execute(
                sql.SQL("TRUNCATE {}.control_events").format(
                    sql.Identifier(schema)
                )
            )

    def control_event(self, schema, event):
        from psycopg import sql

        with self.connect() as connection:
            row = connection.execute(
                sql.SQL(
                    "SELECT detail FROM {}.control_events WHERE event=%s"
                ).format(sql.Identifier(schema)),
                (event,),
            ).fetchone()
        return None if row is None else row[0]

    def mark_control(self, schema, event, detail=None):
        from psycopg import sql

        with self.connect() as connection:
            connection.execute(
                sql.SQL(
                    "INSERT INTO {}.control_events(event,detail) "
                    "VALUES (%s,%s::jsonb) "
                    "ON CONFLICT(event) DO UPDATE SET detail=excluded.detail"
                ).format(sql.Identifier(schema)),
                (event, json.dumps(detail or {}, sort_keys=True)),
            )

    def wait_control(self, schema, event, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            detail = self.control_event(schema, event)
            if detail is not None:
                return detail
            time.sleep(0.05)
        self.fail(f"database barrier timeout: {event}")

    def start_worker(self, schema, mode, application, update=None):
        environment = os.environ.copy()
        environment.update({
            "B8_TEST_DSN": self.dsn,
            "B8_WORKER_SCHEMA": schema,
            "B8_WORKER_MODE": mode,
            "B8_WORKER_APPLICATION": application,
            "PYTHONPATH": os.pathsep.join((
                str(ROOT / "solution"), str(ROOT),
            )),
        })
        if update is not None:
            environment["B8_WORKER_UPDATE"] = update
        return subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(
                    ROOT / "tests" / "first_materials_runtime"
                    / "pg_worker.py"
                ),
            ],
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )

    def finish_worker(self, process, timeout=15):
        stdout, stderr = process.communicate(timeout=timeout)
        if stdout:
            print("worker stdout:", stdout.strip())
        if stderr:
            print("worker stderr:", stderr.strip())
        self.assertEqual(process.returncode, 0)
        return stdout

    def kill_worker(self, process):
        process.kill()
        stdout, stderr = process.communicate(timeout=10)
        self.assertNotEqual(process.returncode, 0)
        return {
            "returncode": process.returncode,
            "stdout": stdout.strip(),
            "stderr": stderr.strip(),
        }

    def worker_activity(self, application):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT state,wait_event_type,wait_event,"
                "xact_start IS NOT NULL "
                "FROM pg_stat_activity WHERE application_name=%s",
                (application,),
            ).fetchone()
        return row

    def test_dependency_staging_rolls_back_with_invalid_material(self):
        fixture = copy.deepcopy(self.fixture)
        fixture["responses"]["f129.materialize"]["sha256"] = "wrong"
        fixture["effects"] = {
            "f129.materialize": {
                "staging": {"draft": {"partial": True}}
            }
        }
        schema = self.schema("rollback")
        self.pg.seed(self.connect, schema, fixture)
        ports = self.Ports(fixture)
        service = self.pg.create_service(self.connect, schema, ports)

        result = service.execute(fixture["operation"], fixture["request"])

        self.assertEqual(result, {
            "status": "INVALID",
            "data": {"code": "INVALID_HASH"},
        })
        self.assertEqual(
            self.state_count(
                schema, fixture["fixture_scope"], "staging"
            ),
            0,
        )
        self.assertEqual(
            self.state_count(
                schema, fixture["fixture_scope"], "replay"
            ),
            0,
        )
        self.assertNotIn(
            "materials.save", [name for name, _ in ports.calls]
        )
        self.assertNotIn(
            "dispatch.propose", [name for name, _ in ports.calls]
        )

    def test_same_scope_race_has_one_effect_chain(self):
        fixture = copy.deepcopy(self.fixture)
        schema = self.schema("race")
        self.pg.seed(self.connect, schema, fixture)
        entered = threading.Event()
        release = threading.Event()

        class BlockingPorts(self.Ports):
            def call(inner_self, name, request):
                if name == "catalog.resolve":
                    entered.set()
                    if not release.wait(5):
                        raise TimeoutError("test barrier")
                return super().call(name, request)

        first_ports = BlockingPorts(fixture)
        second_ports = self.Ports(fixture)
        first_service = self.pg.create_service(
            self.connect, schema, first_ports
        )
        second_service = self.pg.create_service(
            self.connect, schema, second_ports
        )
        results = []

        first = threading.Thread(
            target=lambda: results.append(
                first_service.execute(
                    fixture["operation"], fixture["request"]
                )
            )
        )
        second = threading.Thread(
            target=lambda: results.append(
                second_service.execute(
                    fixture["operation"], fixture["request"]
                )
            )
        )
        first.start()
        self.assertTrue(entered.wait(5))
        second.start()
        time.sleep(0.2)
        self.assertTrue(second.is_alive())
        self.assertEqual(second_ports.calls, [])
        release.set()
        first.join(10)
        second.join(10)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(
            sorted(item["status"] for item in results),
            ["OK", "REPLAY"],
        )
        all_calls = first_ports.calls + second_ports.calls
        self.assertEqual(
            sum(name == "materials.save" for name, _ in all_calls), 1
        )
        self.assertEqual(
            sum(name == "dispatch.propose" for name, _ in all_calls), 1
        )
        self.assertEqual(
            self.state_count(
                schema, fixture["fixture_scope"], "replay"
            ),
            1,
        )

    def test_same_request_id_is_isolated_between_scopes(self):
        first_fixture = copy.deepcopy(self.fixture)
        second_fixture = copy.deepcopy(self.fixture)
        second_scope = copy.deepcopy(second_fixture["fixture_scope"])
        second_scope["lead_id"] = "lead-b"
        second_scope["thread_id"] = "thread-b"
        second_fixture["fixture_scope"] = second_scope
        second_fixture["request"]["scope"] = copy.deepcopy(second_scope)
        schema = self.schema("scopes")
        self.pg.seed(self.connect, schema, first_fixture)

        second_ports = self.Ports(second_fixture)
        backend = self.pg.PostgresPorts(
            self.connect, schema, second_ports
        )
        with backend.transaction(second_scope) as transaction:
            transaction.put("authority", "current", {
                "status": "ACTIVE",
                "revision": 1,
                "catalog_version": "v1",
            })
            transaction.put("catalog", "current", {
                "version": "v1",
                "revision": 1,
            })

        first_ports = self.Ports(first_fixture)
        first_service = self.pg.create_service(
            self.connect, schema, first_ports
        )
        second_service = self.pg.create_service(
            self.connect, schema, second_ports
        )

        first_result = first_service.execute(
            first_fixture["operation"], first_fixture["request"]
        )
        second_result = second_service.execute(
            second_fixture["operation"], second_fixture["request"]
        )

        self.assertEqual(first_result["status"], "OK")
        self.assertEqual(second_result["status"], "OK")
        self.assertEqual(
            self.state_count(
                schema, first_fixture["fixture_scope"], "replay"
            ),
            1,
        )
        self.assertEqual(
            self.state_count(schema, second_scope, "replay"), 1
        )

    def test_sql_constraints_reject_empty_bucket(self):
        fixture = copy.deepcopy(self.fixture)
        schema = self.schema("constraints")
        self.pg.seed(self.connect, schema, fixture)
        backend = self.pg.PostgresPorts(
            self.connect, schema, self.Ports(fixture)
        )

        with self.assertRaises(Exception):
            with backend.transaction(
                fixture["fixture_scope"]
            ) as transaction:
                transaction.put("", "bad", {"value": True})

        self.assertEqual(
            self.state_count(
                schema, fixture["fixture_scope"], "replay"
            ),
            0,
        )

    def test_process_crash_before_commit_rolls_back_all_effects(self):
        fixture = copy.deepcopy(self.fixture)
        schema = self.schema("crash_before")
        self.setup_control(schema, fixture)
        application = "b804-crash-before"
        process = self.start_worker(
            schema, "crash_before_commit", application
        )
        try:
            marker = self.wait_control(schema, "before_commit_ready")
            activity = self.worker_activity(application)
            self.assertIsNotNone(activity)
            self.assertTrue(activity[3])
            invisible = {
                bucket: self.state_count(
                    schema, fixture["fixture_scope"], bucket
                )
                for bucket in (
                    "staging", "materials", "proposals", "replay"
                )
            }
            self.assertEqual(invisible, {
                "staging": 0,
                "materials": 0,
                "proposals": 0,
                "replay": 0,
            })
            killed = self.kill_worker(process)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)

        committed = {
            bucket: self.state_count(
                schema, fixture["fixture_scope"], bucket
            )
            for bucket in (
                "staging", "materials", "proposals", "replay"
            )
        }
        self.assertEqual(committed, {
            "staging": 0,
            "materials": 0,
            "proposals": 0,
            "replay": 0,
        })
        print("crash_before_commit", json.dumps({
            "marker": marker,
            "activity": activity,
            "visible_while_open": invisible,
            "worker": killed,
            "committed_after_kill": committed,
        }, ensure_ascii=False, default=str, sort_keys=True))

    def test_process_crash_after_real_commit_replays_without_effects(self):
        fixture = copy.deepcopy(self.fixture)
        schema = self.schema("crash_after")
        self.setup_control(schema, fixture)
        application = "b804-crash-after"
        process = self.start_worker(
            schema, "crash_after_commit", application
        )
        try:
            marker = self.wait_control(schema, "commit_complete")
            committed = {
                bucket: self.state_count(
                    schema, fixture["fixture_scope"], bucket
                )
                for bucket in (
                    "staging", "materials", "proposals", "replay"
                )
            }
            self.assertEqual(committed, {
                "staging": 1,
                "materials": 1,
                "proposals": 1,
                "replay": 1,
            })
            killed = self.kill_worker(process)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)

        fresh_ports = self.Ports(fixture)
        fresh_service = self.pg.create_service(
            self.connect, schema, fresh_ports
        )
        replay = fresh_service.execute(
            fixture["operation"], fixture["request"]
        )
        self.assertEqual(replay["status"], "REPLAY")
        self.assertEqual(fresh_ports.calls, [])
        self.assertEqual(
            self.state_count(
                schema, fixture["fixture_scope"], "replay"
            ),
            1,
        )
        print("crash_after_commit", json.dumps({
            "marker": marker,
            "committed_before_kill": committed,
            "worker": killed,
            "repeat_status": replay["status"],
            "repeat_calls": fresh_ports.calls,
        }, ensure_ascii=False, sort_keys=True))

    def test_independent_stop_and_config_sessions_use_scoped_locks(self):
        fixture = copy.deepcopy(self.fixture)

        stop_schema = self.schema("stop_session")
        self.setup_control(stop_schema, fixture)
        stop_process = self.start_worker(
            stop_schema,
            "update_state",
            "b804-stop-session",
            update="STOPPED",
        )
        self.wait_control(stop_schema, "update_committed")
        self.finish_worker(stop_process)
        stop_state = self.state_value(
            stop_schema,
            fixture["fixture_scope"],
            "authority",
            "current",
        )
        stop_ports = self.Ports(fixture)
        stop_result = self.pg.create_service(
            self.connect, stop_schema, stop_ports
        ).execute(fixture["operation"], fixture["request"])
        self.assertEqual(stop_result, {"status": "BLOCKED", "data": {}})
        self.assertEqual(stop_ports.calls, [])
        self.assertEqual(stop_state["status"], "STOPPED")

        config_schema = self.schema("config_session")
        self.setup_control(config_schema, fixture)
        prepare = self.start_worker(
            config_schema,
            "prepare_ordering",
            "b804-prepare-ordering",
        )
        updater = None
        try:
            self.wait_control(config_schema, "prepare_after_dispatch")
            prepare_activity = self.worker_activity(
                "b804-prepare-ordering"
            )
            self.assertTrue(prepare_activity[3])
            updater = self.start_worker(
                config_schema,
                "update_state",
                "b804-config-updater",
                update="catalog",
            )
            self.wait_control(config_schema, "update_attempt")

            deadline = time.monotonic() + 5
            update_activity = None
            while time.monotonic() < deadline:
                update_activity = self.worker_activity(
                    "b804-config-updater"
                )
                if (
                    update_activity is not None
                    and update_activity[1] == "Lock"
                ):
                    break
                time.sleep(0.05)
            self.assertIsNotNone(update_activity)
            self.assertEqual(update_activity[1], "Lock")
            self.assertIsNone(
                self.control_event(config_schema, "update_committed")
            )
            self.assertIsNone(updater.poll())
            self.assertEqual(
                self.state_value(
                    config_schema,
                    fixture["fixture_scope"],
                    "catalog",
                    "current",
                )["version"],
                "v1",
            )

            self.mark_control(config_schema, "release_prepare")
            prepare_stdout = self.finish_worker(prepare)
            self.wait_control(config_schema, "update_committed")
            updater_stdout = self.finish_worker(updater)
        finally:
            for process in (prepare, updater):
                if process is not None and process.poll() is None:
                    process.kill()
                    process.communicate(timeout=10)

        final_catalog = self.state_value(
            config_schema,
            fixture["fixture_scope"],
            "catalog",
            "current",
        )
        self.assertEqual(final_catalog, {
            "version": "v2",
            "revision": 2,
        })
        self.assertEqual(
            self.state_count(
                config_schema, fixture["fixture_scope"], "replay"
            ),
            1,
        )

        stale_fixture = copy.deepcopy(fixture)
        stale_fixture["request"]["request_id"] = "request-after-config"
        stale_ports = self.Ports(stale_fixture)
        stale_result = self.pg.create_service(
            self.connect, config_schema, stale_ports
        ).execute(stale_fixture["operation"], stale_fixture["request"])
        self.assertEqual(stale_result, {"status": "STALE", "data": {}})
        self.assertEqual(stale_ports.calls, [])
        print("stop_config_sessions", json.dumps({
            "stop_state": stop_state,
            "stop_result": stop_result,
            "prepare_activity": prepare_activity,
            "updater_wait_activity": update_activity,
            "prepare_stdout": prepare_stdout.strip(),
            "updater_stdout": updater_stdout.strip(),
            "final_catalog": final_catalog,
            "new_request_status": stale_result["status"],
        }, ensure_ascii=False, default=str, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
