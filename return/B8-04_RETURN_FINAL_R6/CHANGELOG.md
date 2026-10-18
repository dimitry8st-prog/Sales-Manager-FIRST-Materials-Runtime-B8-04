# R6 changes from R5

- Protect connection establishment against actual psycopg.OperationalError.
- Make cleanup best-effort without replacing a confirmed commit/rollback or a pending transaction outcome.
- Preserve UNKNOWN for an unconfirmed commit or rollback; never retry effectful work automatically.
- Add unit regressions and real PostgreSQL fault-injection regressions for connection/rollback/close.
- Retain all existing R5 crash, replay, STOP ordering, constraints and scoped-isolation checks.
- Regenerate the integration patch, manifest and fresh two-pass evidence.

These descriptions are implementation intent. RESULT.json and raw logs determine which checks actually passed.
