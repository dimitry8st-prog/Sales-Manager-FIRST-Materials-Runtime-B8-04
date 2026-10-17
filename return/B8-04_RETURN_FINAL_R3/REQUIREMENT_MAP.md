# Карта требований и доказательств

| Требование | Реализация | Тест | Сырой лог |
|---|---|---|---|
| Строгий request/auth/scope/dependency precedence | `solution/first_materials_runtime/api.py` | `test_api.py`: invalid/auth/missing dependency/foreign client | `unit_tests.stderr.txt`, `cp1.stdout.txt` |
| Обе операции `prepare` и `prepare_initial` | `api.py` | positive, blocked qualification, initial staging rollback | `unit_tests.stderr.txt`, `cp1.stdout.txt` |
| STOP/HUMAN и повторная проверка перед эффектами | `api.py::_ensure_state_unchanged` | terminal, stop during materialization, catalog change during materialization/save | `unit_tests.stderr.txt` |
| Replay/conflict без повторных эффектов | `api.py::stored_result` | same request replay, changed request conflict | `unit_tests.stderr.txt`, `cp1.stdout.txt` |
| Доверенные domain exceptions | `api.py::trusted_domain_code` | source exception and arbitrary code exception | `unit_tests.stderr.txt` |
| Transport rollback и uncertain commit | `api.py::execute` | confirmed rollback; uncertain commit without retry | `unit_tests.stderr.txt` |
| Descriptor, hash, fact scope, exact save ref | `api.py` validators | descriptor/hash/facts/save mismatch tests | `unit_tests.stderr.txt`, `cp1.stdout.txt` |
| Атомарные staging/save/dispatch/replay | `api.py`, `acceptance_pg.py` | invalid descriptor/facts/dispatch plus real PG rollback | `unit_tests.stderr.txt`, `cp2_own_tests.stderr.txt` |
| Реальные независимые PostgreSQL connections | `acceptance_pg.py` | PG_GATE and two-service race | `cp2_before_restart.stdout.txt`, `cp2_own_tests.stderr.txt` |
| Конкуренция и scope isolation | scoped row lock + composite state PK | same-scope race; same request ID in two scopes | `cp2_own_tests.stderr.txt` |
| Commit/rollback, persistence, restart | SQL transaction + persistent replay | PG_GATE before/after restart | `cp2_before_restart.stdout.txt`, `cp2_restart.stdout.txt`, `cp2_after_restart.stdout.txt` |
| SQL constraints | `migrations/0001.sql` | empty bucket rejected | `cp2_own_tests.stderr.txt` |
| Защищённая база неизменна | без изменений issued paths | `check_bytes()` / CP0 | `protected_manifest.stdout.txt`, `cp0.stdout.txt` |
| Patch применим и восстанавливает файлы | `integration_proposal/B8-04.patch` | apply check + SHA-256 comparison | `patch_verification.txt` |
| Pylance видит исходный компонент без подавления диагностики | локальный `.vscode/settings.json`, вне возврата | JSON path + runtime import check | `pylance_path_check.txt` |
| Настоящий restart на той же БД без seed | тот же container ID, schema и увеличившийся postmaster start | PG_GATE `after_restart` | `cp2_postmaster_before.txt`, `cp2_restart_proof.txt` |

Все имена сырых логов относятся к `evidence/raw/`.

