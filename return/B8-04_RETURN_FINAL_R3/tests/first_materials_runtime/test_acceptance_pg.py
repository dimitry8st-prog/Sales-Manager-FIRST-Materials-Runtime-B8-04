"""Дополнительные CP2-проверки на настоящем PostgreSQL."""

import copy
import json
import os
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


if __name__ == "__main__":
    unittest.main()
