# Карта «требование → код → тест → сырой лог»

Текущая карта относится к R5. Строки R4 оставлены как история и ссылаются на прежние имена логов внутри этого архива только там, где те же файлы скопированы в `evidence/raw/pass1`.

| Требование | Код | Проверка | Доказательство R5 |
|---|---|---|---|
| Повторная проверка после dispatch | `api.py::_save_and_propose` | прежние `DispatchFinalStateTests`; поведение не менялось | `pass1/unit_no_dsn.stderr.txt`, `pass2/unit_no_dsn.stderr.txt` |
| STOP первым держит scoped lock, подготовка получает BLOCKED без эффектов | `pg_worker.py::run_stop_hold` | `test_stop_lock_first_makes_waiting_prepare_blocked` | `pass1/cp2_process.stdout.txt`, `pass2/cp2_process.stdout.txt` |
| Подготовка первой держит lock, STOP ждёт и не отменяет commit | `pg_worker.py::run_prepare_ordering_barrier` | `test_prepare_lock_first_then_stop_serializes_after_commit` | те же stdout |
| Crash до commit и после подтверждённого commit | `pg_worker.py` | два crash-теста | `pass1/cp2_process.stderr.txt`, `pass2/cp2_process.stderr.txt` |
| Replay после restart без повторного seed | PostgreSQL adapter | PG_GATE обе фазы, два цикла | `pass1/pg_gate_*.stdout.txt`, `pass1/restart_proof.txt`, `pass2/` |
| Изоляция scope и SQL-ограничение | `acceptance_pg.py`, `0001.sql` | соответствующие PG-тесты | `pass1/cp2_process.stderr.txt` |
| CP0/CP1 и неизменные защищённые файлы | без правки issued paths | CP0, CP1 | `pass1/cp0.stdout.txt`, `pass1/cp1.stdout.txt`, `pass2/` |
| Патч восстанавливает 7 файлов | `integration_proposal/B8-04.patch` | `git apply` и побайтное сравнение | `clean_patch/` |
| CP3 и CP4 | не входят в возврат исполнителя | не запускались | `RESULT.json`: NOT_RUN |
