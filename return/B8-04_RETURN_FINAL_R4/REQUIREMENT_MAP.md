# Карта «требование → код → тест → сырой лог»

| Требование | Код | Проверка | Доказательство |
|---|---|---|---|
| Повторная проверка после dispatch | `api.py::_save_and_propose`, `_ensure_state_unchanged` | `DispatchFinalStateTests` для обеих операций | `reproduction_before_fix.*`, `dispatch_final_check.*` |
| Каталог v1→v2 даёт STALE и откат | те же функции | два catalog-change теста | `dispatch_final_check.stderr.txt` |
| STOPPED/HUMAN даёт BLOCKED; безопасность выше STALE | те же функции | terminal и simultaneous тесты | `dispatch_final_check.stderr.txt` |
| Нет staging/save/proposal/replay текущего запроса | исключительный выход из транзакции | assertions по `Ports.state/writes`; crash-before-commit | `dispatch_final_check.stderr.txt`, `cp2_process_scenarios.stdout.txt` |
| Crash worker до commit | `pg_worker.py`, PostgreSQL transaction adapter | `test_process_crash_before_commit_rolls_back_all_effects` | `cp2_process_scenarios.stdout.txt` |
| Commit подтверждён, ответ потерян | `pg_worker.py::CommitBarrierTransaction` | `test_process_crash_after_real_commit_replays_without_effects` | `cp2_process_scenarios.stdout.txt` |
| Независимые STOP/config и scoped locks | `acceptance_pg.py`, `pg_worker.py` | `test_independent_stop_and_config_sessions_use_scoped_locks` | `cp2_process_scenarios.stdout.txt` |
| Replay сохраняется после restart без seed | PostgreSQL replay store | PG_GATE before/after | `pg_gate_*`, `restart_proof.txt` |
| Полный контракт/ABI/NO_OUTBOUND | `api.py` | 50 быстрых тестов, CP1 19/19 | `unit_tests.*`, `cp1.*` |
| Защищённая база не менялась | — | SHA-256 по `PACKAGE_SHA256.json` | `protected_manifest.stdout.txt` |
| Patch восстанавливает результат | `integration_proposal/B8-04.patch` | apply check + 7 SHA-256 + clean tests | `patch_verification.txt`, `clean_patch_*` |
