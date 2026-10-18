# B8-04 R6 — PASS

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
