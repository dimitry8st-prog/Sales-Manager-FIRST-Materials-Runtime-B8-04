#!/usr/bin/env python3
"""Run R6 twice on disposable PostgreSQL, then build an evidence-only return.

Usage: python return/tools/verify_r6.py --repo .
Requires Python 3.12+, git, Docker, psycopg. It never uses an external DSN.
The sole published ZIP is PASS only after every mandatory real-PG test ran.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
import zipfile


NAME = "B8-04_RETURN_FINAL_R6"
MODULE = "first_materials_runtime"
SUITE = "tests/first_materials_runtime"
REQUIRED = [f"solution/{MODULE}/api.py", f"solution/{MODULE}/acceptance_pg.py",
            f"{SUITE}/test_api.py", f"{SUITE}/test_acceptance_pg.py",
            f"{SUITE}/test_pg_failures.py", f"{SUITE}/test_pg_cleanup_real.py",
            f"{SUITE}/pg_worker.py"]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def extract_base(archive, destination):
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            name = PurePosixPath(member.filename)
            if name.is_absolute() or ".." in name.parts or "\\" in member.filename:
                raise AssertionError("Unsafe baseline ZIP path")
            if (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise AssertionError("Baseline symlink prohibited")
        bundle.extractall(destination)
    found = list(destination.rglob("PACKAGE_SHA256.json"))
    if len(found) != 1:
        raise AssertionError("Exactly one issued PACKAGE_SHA256.json required")
    return found[0].parent


def check_protected(base):
    manifest = json.loads((base / "PACKAGE_SHA256.json").read_text(encoding="utf-8"))
    if len(manifest) != 140:
        raise AssertionError(f"Expected 140 protected files, got {len(manifest)}")
    changed = [name for name, digest in manifest.items()
               if not (base / name).is_file() or sha(base / name) != digest]
    if changed:
        raise AssertionError(f"Issued files changed: {changed}")
    return {"protected_files": len(manifest), "changed_files": []}


def candidate_files(repo):
    files = []
    for prefix in (f"solution/{MODULE}", SUITE):
        root = repo / prefix
        if not root.is_dir():
            raise AssertionError(f"Missing candidate directory: {prefix}")
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise AssertionError(f"Candidate symlink prohibited: {path}")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                files.append(path.relative_to(repo).as_posix())
    if not set(REQUIRED) <= set(files):
        raise AssertionError(f"Missing candidate files: {sorted(set(REQUIRED) - set(files))}")
    return files


def overlay(repo, base, files):
    protected = json.loads((base / "PACKAGE_SHA256.json").read_text(encoding="utf-8"))
    for name in files:
        if name in protected and sha(repo / name) != protected[name]:
            raise AssertionError(f"Candidate tried to overwrite issued file: {name}")
        (base / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo / name, base / name)
    return check_protected(base)


class Evidence:
    def __init__(self, root):
        self.root, self.records = root, []
        root.mkdir(parents=True, exist_ok=True)

    def run(self, label, argv, *, cwd, env=None, timeout=300, required=True):
        started = datetime.now(timezone.utc).isoformat()
        folder = self.root / label
        folder.mkdir(parents=True, exist_ok=True)
        merged = dict(os.environ)
        merged.pop("B8_TEST_DSN", None)
        merged.pop("B8_TEST_SCHEMA", None)
        merged.update(env or {})
        record = {"label": label, "command": list(map(str, argv)), "cwd": str(cwd),
                  "started_utc": started, "timeout_seconds": timeout,
                  "environment_overrides": {key: ("<ephemeral test DSN omitted>" if key == "B8_TEST_DSN" else value)
                                            for key, value in (env or {}).items()}}
        try:
            result = subprocess.run(argv, cwd=cwd, env=merged, capture_output=True, timeout=timeout)
            stdout, stderr, code = result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired as exc:
            stdout, stderr, code = exc.stdout or b"", exc.stderr or b"", 124
            stderr += b"\nRUNNER_TIMEOUT\n"
        except OSError as exc:
            stdout, stderr, code = b"", str(exc).encode(), 127
        (folder / "stdout.log").write_bytes(stdout)
        (folder / "stderr.log").write_bytes(stderr)
        record.update(exit_code=code, finished_utc=datetime.now(timezone.utc).isoformat(),
                      stdout=f"evidence/{label}/stdout.log", stderr=f"evidence/{label}/stderr.log")
        write_json(folder / "command.json", record)
        self.records.append(record)
        print(f"[{label}] exit={code}: {shlex.join(map(str, argv))}", flush=True)
        if required and code:
            raise AssertionError(f"{label}: exit {code}; see raw logs")
        return stdout.decode("utf-8", "replace") + stderr.decode("utf-8", "replace")

    def test(self, label, argv, *, cwd, env=None, minimum=1, allow_skips=False):
        output = self.run(label, argv, cwd=cwd, env=env)
        counts = re.findall(r"Ran (\d+) tests?", output)
        if not counts or int(counts[-1]) < minimum:
            raise AssertionError(f"{label}: insufficient tests actually discovered")
        if not allow_skips and (re.search(r"\bskipped\b", output, re.I) or "NOT_RUN" in output):
            raise AssertionError(f"{label}: mandatory suite skipped")
        return {"tests": int(counts[-1]), "skips_allowed": allow_skips, "exit_code": 0}


def python_env(base, dsn=None, schema=None):
    env = {"PYTHONPATH": os.pathsep.join(map(str, [base, base / "solution", base / "reference_source"])),
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"}
    if dsn:
        env.update(B8_TEST_DSN=dsn, B8_TEST_SCHEMA=schema)
    return env


def pg_identity(ev, label, container, cwd):
    text = ev.run(label, ["docker", "exec", container, "psql", "-U", "b8", "-d", "b8",
                         "-At", "-c", "SELECT json_build_object('system_identifier',system_identifier::text,"
                         "'postmaster_started',pg_postmaster_start_time()::text,'version',version()) "
                         "FROM pg_control_system()"], cwd=cwd)
    return json.loads(text.strip())


def full_pass(ev, number, archive, repo, files, work, port):
    prefix = f"pass_{number}"
    base = extract_base(archive, work / prefix)
    protection = overlay(repo, base, files)
    env, py = python_env(base), [sys.executable, "-B"]
    result = {"status": "FAIL", "protected": protection, "environment": "disposable PostgreSQL 17"}
    ev.run(f"{prefix}/cp0", py + ["ACCEPTANCE_RUNNER.py", "cp0"], cwd=base, env=env)
    ev.run(f"{prefix}/cp1", py + ["ACCEPTANCE_RUNNER.py", "cp1", "--module", MODULE + ".api"], cwd=base, env=env)
    result["unit_without_dsn"] = ev.test(f"{prefix}/unit_without_dsn", py + ["-m", "unittest", "discover", "-s", SUITE, "-p", "test_*.py", "-v"], cwd=base, env=env, allow_skips=True)
    result["pg_error_regressions"] = ev.test(f"{prefix}/pg_error_regressions", py + ["-m", "unittest", "discover", "-s", SUITE, "-p", "test_pg_failures.py", "-v"], cwd=base, env=env, minimum=7)
    container = f"b804-r6-{number}-{uuid.uuid4().hex[:10]}"
    pg_env = python_env(base, f"host=127.0.0.1 port={port} user=b8 dbname=b8 connect_timeout=5", f"b8_accept_r6_{number}")
    try:
        ev.run(f"{prefix}/docker_run", ["docker", "run", "--detach", "--name", container,
               "--publish", f"127.0.0.1:{port}:5432", "--env", "POSTGRES_HOST_AUTH_METHOD=trust",
               "--env", "POSTGRES_USER=b8", "--env", "POSTGRES_DB=b8", "postgres:17"], cwd=base)
        ready = False
        for attempt in range(30):
            ev.run(f"{prefix}/ready_{attempt:02d}", ["docker", "exec", container, "pg_isready", "-U", "b8", "-d", "b8"], cwd=base, timeout=10, required=False)
            if ev.records[-1]["exit_code"] == 0:
                ready = True
                break
            time.sleep(1)
        if not ready:
            raise AssertionError("Disposable PostgreSQL did not become ready")
        ev.run(f"{prefix}/container_inspect_before", ["docker", "inspect", container], cwd=base)
        result["original_pg_tests"] = ev.test(f"{prefix}/original_pg_tests", py + ["-m", "unittest", "discover", "-s", SUITE, "-p", "test_acceptance_pg.py", "-v"], cwd=base, env=pg_env, minimum=9)
        result["real_pg_cleanup_tests"] = ev.test(f"{prefix}/real_pg_cleanup_tests", py + ["-m", "unittest", "discover", "-s", SUITE, "-p", "test_pg_cleanup_real.py", "-v"], cwd=base, env=pg_env, minimum=7)
        gate = py + ["PG_GATE.py", "--module", MODULE, "--schema", f"b8_accept_r6_gate_{number}", "--phase"]
        ev.run(f"{prefix}/before_restart", gate + ["before_restart"], cwd=base, env=pg_env)
        before = pg_identity(ev, f"{prefix}/identity_before", container, base)
        ev.run(f"{prefix}/docker_restart", ["docker", "restart", "--time", "10", container], cwd=base, timeout=90)
        for attempt in range(30):
            ev.run(f"{prefix}/restart_ready_{attempt:02d}", ["docker", "exec", container, "pg_isready", "-U", "b8", "-d", "b8"], cwd=base, timeout=10, required=False)
            if ev.records[-1]["exit_code"] == 0:
                break
            time.sleep(1)
        else:
            raise AssertionError("Restarted PostgreSQL did not become ready")
        after = pg_identity(ev, f"{prefix}/identity_after", container, base)
        if before["system_identifier"] != after["system_identifier"] or before["postmaster_started"] == after["postmaster_started"]:
            raise AssertionError("Real restart of the same database was not proven")
        ev.run(f"{prefix}/container_inspect_after", ["docker", "inspect", container], cwd=base)
        # This command invokes the issued gate's after branch; no seed/reinitialize by this runner.
        ev.run(f"{prefix}/after_restart", gate + ["after_restart"], cwd=base, env=pg_env)
        result.update(status="PASS", restart_proof={"before": before, "after": after,
                      "same_container": container, "after_restart_seed_called_by_runner": False})
        result["protected_after"] = check_protected(base)
        return result
    finally:
        ev.run(f"{prefix}/docker_logs", ["docker", "logs", container], cwd=base, required=False)
        ev.run(f"{prefix}/docker_remove", ["docker", "rm", "--force", "--volumes", container], cwd=base, required=False)


def make_patch(ev, archive, repo, files, work, payload):
    base = extract_base(archive, work / "patch_source")
    for cmd in (["git", "init", "-q"], ["git", "add", "--all"]):
        ev.run("patch/" + ("init" if "init" in cmd else "index_baseline"), cmd, cwd=base)
    overlay(repo, base, files)
    ev.run("patch/intent_to_add", ["git", "add", "--intent-to-add", "--", "solution", "tests"], cwd=base)
    text = ev.run("patch/generate", ["git", "diff", "--binary", "--no-ext-diff", "--no-color", "--", "solution", "tests"], cwd=base)
    patch = payload / "integration_proposal" / "B8-04.patch"
    patch.parent.mkdir(parents=True, exist_ok=True)
    # Preserve exact stdout bytes rather than combining diagnostic stderr into the patch.
    patch.write_bytes((ev.root / "patch/generate/stdout.log").read_bytes())
    if not text.strip():
        raise AssertionError("Empty candidate patch")
    target = extract_base(archive, work / "patch_reproduction")
    ev.run("patch/check", ["git", "apply", "--check", str(patch)], cwd=target)
    ev.run("patch/apply", ["git", "apply", str(patch)], cwd=target)
    for name in files:
        if not (target / name).is_file() or (target / name).read_bytes() != (repo / name).read_bytes():
            raise AssertionError(f"Clean patch reproduction mismatch: {name}")
    check_protected(target)
    env, py = python_env(target), [sys.executable, "-B"]
    ev.run("patch/cp1", py + ["ACCEPTANCE_RUNNER.py", "cp1", "--module", MODULE + ".api"], cwd=target, env=env)
    ev.test("patch/unit_without_dsn", py + ["-m", "unittest", "discover", "-s", SUITE, "-p", "test_*.py", "-v"], cwd=target, env=env, allow_skips=True)
    return {"status": "PASS", "files_reproduced": len(files), "protected_files": 140, "sha256": sha(patch)}


def document(payload, result, files):
    status = result["status"]
    (payload / "README.md").write_text(f"""# B8-04 R6 — {status}

This is the implementer's return, not a declaration that FIRST is production-ready.
CP0/CP1 and CP2 are PASS only if RESULT.json records both complete independent runs as PASS.
All raw command arguments, stdout, stderr, exit codes and timestamps are in evidence/.
The unit_without_dsn suite intentionally skips PostgreSQL-dependent tests; mandatory real-PG suites run separately without skips.

Reproduce in a clean Python 3.12+ environment with psycopg installed, git and Docker:
`python return/tools/verify_r6.py --repo .`
The repository must contain assignment/B8-04_R3.zip plus solution/ and tests/.
The tool creates only a disposable postgres:17 container bound to 127.0.0.1:55432.
No external B8_TEST_DSN is read, no credentials or real clients are used.
Two passes use different containers. Within each pass, before_restart and after_restart
use the same container/data directory and an actual Docker restart without a second seed.

CP3: NOT_RUN — production adapters, installed owner dependencies and shared runtime belong to the integrator.
CP4: NOT_RUN — independent installed-system audit is outside this return.
NO_OUTBOUND is preserved. F129/F130/materials/F115 owner ports remain synthetic in CP2.
Issued fixtures/contracts/reference_source/reference_d1 remain unchanged (140 protected files).
""", encoding="utf-8")
    (payload / "CHANGELOG.md").write_text("""# R6 changes from R5

- Protect connection establishment against actual psycopg.OperationalError.
- Make cleanup best-effort without replacing a confirmed commit/rollback or a pending transaction outcome.
- Preserve UNKNOWN for an unconfirmed commit or rollback; never retry effectful work automatically.
- Add unit regressions and real PostgreSQL fault-injection regressions for connection/rollback/close.
- Retain all existing R5 crash, replay, STOP ordering, constraints and scoped-isolation checks.
- Regenerate the integration patch, manifest and fresh two-pass evidence.

These descriptions are implementation intent. RESULT.json and raw logs determine which checks actually passed.
""", encoding="utf-8")
    rows = ["# Requirement → code → test → raw evidence", "", "| Requirement | Code | Test | Raw logs (each pass_1, pass_2) |", "|---|---|---|---|"]
    for text, tests, logs in [
        ("Connection failure before transaction → DEPENDENCY_UNAVAILABLE, no effects", [f"{SUITE}/test_pg_failures.py"], ["pg_error_regressions"]),
        ("Cleanup cannot overwrite commit/rollback or UNKNOWN; adapter reusable", [f"{SUITE}/test_pg_failures.py", f"{SUITE}/test_pg_cleanup_real.py"], ["pg_error_regressions", "real_pg_cleanup_tests"]),
        ("Original 9 PG cases: both STOP orders, crash before/after commit, isolation, constraints", [f"{SUITE}/test_acceptance_pg.py", f"{SUITE}/pg_worker.py"], ["original_pg_tests"]),
        ("Durable replay after actual same-container PostgreSQL restart; no second seed", ["PG_GATE.py (unchanged issued file)"], ["before_restart", "identity_before", "docker_restart", "identity_after", "after_restart"]),
        ("Protected baseline and public ABI/positive scenarios", ["ACCEPTANCE_RUNNER.py (unchanged issued file)", f"{SUITE}/test_api.py"], ["cp0", "cp1", "unit_without_dsn"]),
    ]:
        refs = "; ".join(f"`{test}`" for test in tests)
        raw = "; ".join(f"`evidence/pass_{number}/{log}/stdout.log`, `evidence/pass_{number}/{log}/stderr.log`" for number in (1, 2) for log in logs)
        rows.append(f"| {text} | `solution/{MODULE}/api.py`; `solution/{MODULE}/acceptance_pg.py` | {refs} | {raw} |")
    rows += ["", "## Included candidate regression methods", ""]
    for file in files:
        if Path(file).name in ("test_pg_failures.py", "test_pg_cleanup_real.py"):
            tree = ast.parse((payload / file).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                    rows.append(f"- `{file}:{node.lineno}` — `{node.name}`")
    (payload / "REQUIREMENT_MAP.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (payload / "CONNECTION_AND_ROLLBACK.md").write_text("""# Connection, installation and rollback boundaries

Only the candidate-owned acceptance adapter and its SQL namespace are exercised.
Public create_service/execute ABI is retained. The component never performs outbound delivery.
Apply integration_proposal/B8-04.patch to an untouched issued B8-04_R3 package and run CP0/CP1.
The runner separately verifies a clean patch apply and exact candidate bytes.
Real production connection roles, dependency hashes/native gates, migration installation,
backup/restore, rollback/reinstall and shared-runtime wiring require the CP3 integrator.
This return does not authorize production installation or modification of owner components.
For this disposable test environment rollback means remove only the container created by the runner.
For candidate-file review rollback means restore the clean issued copy; no production DDL is run.
UNKNOWN means commit or rollback was not confirmed; the caller must reconcile through durable identity,
without automatically repeating effects. A cleanup failure after confirmed commit cannot undo it.
""", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--port", type=int, default=55432)
    args = parser.parse_args()
    repo = args.repo.resolve()
    output = repo / "return"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="b804-r6-") as temporary:
        work = Path(temporary)
        payload = work / NAME
        payload.mkdir()
        ev = Evidence(payload / "evidence")
        result = {"revision": "R6", "status": "FAIL", "started_utc": datetime.now(timezone.utc).isoformat(),
                  "CP0": "NOT_RUN", "CP1": "NOT_RUN", "CP2": "NOT_RUN", "CP3": "NOT_RUN", "CP4": "NOT_RUN",
                  "NO_OUTBOUND": True, "integration_status": "NOT_CONNECTED", "passes": [],
                  "limitations": ["CP2 uses synthetic owner ports; production dependency/native gates not installed.",
                                  "CP3 belongs to integrator; CP4 belongs to independent auditor."]}
        files = []
        try:
            if sys.version_info < (3, 12):
                raise RuntimeError("Python 3.12 or newer required")
            archive = repo / "assignment" / "B8-04_R3.zip"
            result["issued_archive_sha256"] = sha(archive)
            files = candidate_files(repo)
            for name in files:
                (payload / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(repo / name, payload / name)
            tool = payload / "verification_tools" / "verify_r6.py"
            tool.parent.mkdir()
            shutil.copyfile(Path(__file__), tool)
            for label, command in [
                ("versions/python", [sys.executable, "--version"]),
                ("versions/psycopg", [sys.executable, "-c", "import psycopg; print('psycopg',psycopg.__version__); print('libpq',psycopg.pq.version())"]),
                ("versions/git", ["git", "--version"]),
                ("versions/docker", ["docker", "version"]),
            ]:
                ev.run(label, command, cwd=repo)
            result["patch_reproduction"] = make_patch(ev, archive, repo, files, work, payload)
            for number in (1, 2):
                try:
                    passed = full_pass(ev, number, archive, repo, files, work, args.port)
                except Exception as exc:
                    passed = {"status": "FAIL", "failure": f"{type(exc).__name__}: {exc}"}
                    write_json(payload / "evidence" / f"pass_{number}" / "RESULT.json", passed)
                    raise
                finally:
                    write_json(payload / "evidence" / f"pass_{number}" / "COMMANDS.json",
                               [record for record in ev.records if record["label"].startswith(f"pass_{number}/")])
                result["passes"].append(passed)
                write_json(payload / "evidence" / f"pass_{number}" / "RESULT.json", passed)
            result.update(status="PASS", CP0="PASS", CP1="PASS", CP2="PASS")
        except Exception as exc:
            result.update(status="FAIL", failure=f"{type(exc).__name__}: {exc}")
            (payload / "evidence" / "failure.log").write_text(traceback.format_exc(), encoding="utf-8")
        result["finished_utc"] = datetime.now(timezone.utc).isoformat()
        result["candidate_sha256"] = {name: sha(payload / name) for name in files if (payload / name).is_file()}
        write_json(payload / "evidence" / "COMMANDS.json", ev.records)
        write_json(payload / "RESULT.json", result)
        document(payload, result, [name for name in files if (payload / name).is_file()])
        manifest = "".join(f"{sha(path)}  {path.relative_to(payload).as_posix()}\n"
                           for path in sorted(payload.rglob("*")) if path.is_file())
        (payload / "SHA256SUMS.txt").write_text(manifest, encoding="utf-8")
        destination = output / (NAME + ".zip")
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted(payload.rglob("*")):
                if path.is_file():
                    bundle.write(path, (Path(NAME) / path.relative_to(payload)).as_posix())
        with zipfile.ZipFile(destination) as bundle:
            assert bundle.testzip() is None
            assert len(bundle.namelist()) == len(set(bundle.namelist()))
            assert all(name.startswith(NAME + "/") and "\\" not in name and ".." not in PurePosixPath(name).parts for name in bundle.namelist())
            mismatches = []
            for line in manifest.splitlines():
                digest, name = line.split("  ", 1)
                if hashlib.sha256(bundle.read(NAME + "/" + name)).hexdigest() != digest:
                    mismatches.append(name)
            assert not mismatches
        validation = {"status": result["status"], "zip": destination.name, "sha256": sha(destination),
                      "bytes": destination.stat().st_size, "manifest_mismatches": [],
                      "independent_full_passes": len(result["passes"]), "CP3": "NOT_RUN", "CP4": "NOT_RUN"}
        write_json(output / (NAME + ".zip.validation.json"), validation)
        print(json.dumps(validation, ensure_ascii=False, indent=2), flush=True)
        return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
