"""Отдельный процесс для обязательных crash/concurrency сценариев CP2."""

from __future__ import annotations

import copy
import json
import os
import sys
import time
from pathlib import Path

import psycopg
from psycopg import sql


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "solution"))

from ACCEPTANCE_RUNNER import Ports  # noqa: E402
from first_materials_runtime import acceptance_pg  # noqa: E402
from first_materials_runtime.api import (  # noqa: E402
    create_service as create_materials_service,
)


DSN = os.environ["B8_TEST_DSN"]
SCHEMA = os.environ["B8_WORKER_SCHEMA"]
MODE = os.environ["B8_WORKER_MODE"]
APPLICATION_NAME = os.environ["B8_WORKER_APPLICATION"]


def connect():
    return psycopg.connect(DSN, application_name=APPLICATION_NAME)


def control_connect():
    return psycopg.connect(
        DSN, application_name=f"{APPLICATION_NAME}-control"
    )


def mark(event, detail=None):
    with control_connect() as connection:
        connection.execute(
            sql.SQL(
                "INSERT INTO {}.control_events(event,detail) "
                "VALUES (%s,%s::jsonb) "
                "ON CONFLICT(event) DO UPDATE SET detail=excluded.detail"
            ).format(sql.Identifier(SCHEMA)),
            (event, json.dumps(detail or {}, sort_keys=True)),
        )


def event_exists(event):
    with control_connect() as connection:
        return connection.execute(
            sql.SQL(
                "SELECT 1 FROM {}.control_events WHERE event=%s"
            ).format(sql.Identifier(SCHEMA)),
            (event,),
        ).fetchone() is not None


def wait_for(event, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if event_exists(event):
            return
        time.sleep(0.05)
    raise TimeoutError(f"worker barrier timeout: {event}")


def fixture():
    fixtures = json.loads(
        (ROOT / "acceptance" / "fixtures.json").read_text(
            encoding="utf-8"
        )
    )
    value = copy.deepcopy(next(
        item for item in fixtures
        if item["name"] == "approved_presentation"
    ))
    value["effects"] = {
        "f129.materialize": {
            "staging": {"current-request": {"written": True}},
        },
        "materials.save": {
            "materials": {"current-request": {"saved": True}},
        },
        "dispatch.propose": {
            "proposals": {"current-request": {"prepared": True}},
        },
    }
    return value


class DispatchBarrierPorts(acceptance_pg.PostgresPorts):
    def __init__(self, *args, event, release, **kwargs):
        super().__init__(*args, **kwargs)
        self.event = event
        self.release = release

    def call(self, name, request):
        result = super().call(name, request)
        if name == "dispatch.propose":
            # super() уже перенёс staging/save/proposal в SQL-транзакцию.
            mark(self.event, {"pid": os.getpid(), "effects_mirrored": True})
            wait_for(self.release)
        return result


class CommitBarrierTransaction:
    def __init__(self, inner):
        self.inner = inner

    def __enter__(self):
        return self.inner.__enter__()

    def __exit__(self, exc_type, exc, tb):
        result = self.inner.__exit__(exc_type, exc, tb)
        if exc_type is None:
            # inner.__exit__ вернулся только после успешного PostgreSQL commit.
            mark("commit_complete", {
                "pid": os.getpid(),
                "postgres_commit_returned": True,
            })
            wait_for("release_after_commit")
        return result


class CommitBarrierPorts(acceptance_pg.PostgresPorts):
    def transaction(self, scope):
        return CommitBarrierTransaction(super().transaction(scope))


def run_prepare_before_commit_crash():
    value = fixture()
    backend = DispatchBarrierPorts(
        connect,
        SCHEMA,
        Ports(value),
        event="before_commit_ready",
        release="release_before_commit",
    )
    result = create_materials_service(backend).execute(
        value["operation"], value["request"]
    )
    print(json.dumps(result, sort_keys=True), flush=True)


def run_prepare_after_commit_crash():
    value = fixture()
    backend = CommitBarrierPorts(connect, SCHEMA, Ports(value))
    result = create_materials_service(backend).execute(
        value["operation"], value["request"]
    )
    print(json.dumps(result, sort_keys=True), flush=True)


def run_prepare_ordering_barrier():
    value = fixture()
    backend = DispatchBarrierPorts(
        connect,
        SCHEMA,
        Ports(value),
        event="prepare_after_dispatch",
        release="release_prepare",
    )
    result = create_materials_service(backend).execute(
        value["operation"], value["request"]
    )
    print(json.dumps(result, sort_keys=True), flush=True)


def run_state_update():
    value = fixture()
    update = os.environ["B8_WORKER_UPDATE"]
    mark("update_attempt", {"pid": os.getpid(), "update": update})
    backend = acceptance_pg.PostgresPorts(
        connect, SCHEMA, Ports(value)
    )
    with backend.transaction(value["fixture_scope"]) as transaction:
        if update == "catalog":
            transaction.put("catalog", "current", {
                "version": "v2",
                "revision": 2,
            })
        elif update in ("STOPPED", "HUMAN"):
            transaction.put("authority", "current", {
                "status": update,
                "revision": 2,
                "catalog_version": "v1",
            })
        else:
            raise ValueError(f"unknown update: {update}")
    mark("update_committed", {"pid": os.getpid(), "update": update})
    print(json.dumps({"updated": update}, sort_keys=True), flush=True)


if MODE == "crash_before_commit":
    run_prepare_before_commit_crash()
elif MODE == "crash_after_commit":
    run_prepare_after_commit_crash()
elif MODE == "prepare_ordering":
    run_prepare_ordering_barrier()
elif MODE == "update_state":
    run_state_update()
else:
    raise ValueError(f"unknown worker mode: {MODE}")
