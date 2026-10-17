# Выполненные команды и коды выхода

Среда: Windows PowerShell; Python 3.12.13 из локальной изолированной установки.
Launcher `py -3.12` в этой среде не был зарегистрирован, поэтому те же модули
запускались напрямую этим CPython 3.12.13. Версии — в `raw/versions.txt`.

| Проверка | Команда по существу | Exit |
|---|---|---:|
| Unit discovery | `PYTHONUTF8=1 PYTHONPATH=solution python3.12 -B -m unittest discover -s tests/first_materials_runtime -p test_*.py -v` | 0 |
| CP0 | `PYTHONUTF8=1 python3.12 -B ACCEPTANCE_RUNNER.py cp0` | 0 |
| CP1 | `PYTHONUTF8=1 PYTHONPATH=solution python3.12 -B ACCEPTANCE_RUNNER.py cp1 --module first_materials_runtime.api` | 0 |
| CP2 before | `B8_TEST_DSN=<environment> PYTHONPATH=solution python3.12 -B PG_GATE.py --module first_materials_runtime --schema b8_accept_b804r3pylance --phase before_restart` | 0 |
| CP2 own PG | `B8_TEST_DSN=<environment> PYTHONPATH=solution python3.12 -B -m unittest tests.first_materials_runtime.test_acceptance_pg -v` | 0 |
| PostgreSQL restart | `docker restart b8-04-r3-cp2-final` | 0 |
| CP2 after | та же схема, `--phase after_restart`, без seed | 0 |
| Protected manifest | `ACCEPTANCE_RUNNER.check_bytes()` | 0 |
| Patch check/apply/hash compare | см. `raw/patch_verification.txt` | 0 |

Доказательство restart находится в `raw/cp2_restart_proof.txt`: ID контейнера
до и после одинаков, `pg_postmaster_start_time` увеличился, схема осталась
`b8_accept_b804r3pylance`, после restart seed не выполнялся.

Pylance-проверка записана в `raw/pylance_path_check.txt`. Изменённый локальный
`.vscode/settings.json` намеренно отсутствует в integration patch и ZIP.

Git-состояние рабочей папки: каталог не содержит `.git` и не является Git-
репозиторием. Read-only `git ls-remote` для указанного GitHub URL показал HEAD
`bbd31ef6e5856e6ec31442ee91061f8c18f89088`. Pull/checkout/push не выполнялись.

