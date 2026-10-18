# Команды и коды выхода

Все команды выполнялись из корня выданного пакета. Секретный DSN передавался
только через переменную процесса `B8_TEST_DSN`; его значение не записывалось.
Так как системный `py -3.12` в этой среде отсутствовал, использован тот же
CPython 3.12.13 из изолированного `.cp2-venv`.

| Проверка | Выполненная команда | Код |
|---|---|---:|
| Собственные тесты без DSN | `$env:PYTHONUTF8='1'; $env:PYTHONPATH='solution'; .\.cp2-venv\Scripts\python.exe -B -m unittest discover -s tests/first_materials_runtime -p "test_*.py" -v` | 0 |
| CP0 | `.\.cp2-venv\Scripts\python.exe -B ACCEPTANCE_RUNNER.py cp0` | 0 |
| CP1 | `$env:PYTHONPATH='solution'; .\.cp2-venv\Scripts\python.exe -B ACCEPTANCE_RUNNER.py cp1 --module first_materials_runtime.api` | 0 |
| CP2 process tests | `$env:B8_TEST_SCHEMA='b8_accept_b804r4finaltests'; .\.cp2-venv\Scripts\python.exe -B -m unittest tests.first_materials_runtime.test_acceptance_pg -v` | 0 |
| PG_GATE before | `.\.cp2-venv\Scripts\python.exe -B PG_GATE.py --module first_materials_runtime --schema b8_accept_b804r4gate --phase before_restart` | 0 |
| PostgreSQL restart | `docker restart b8-04-r4-cp2` | 0 |
| PG_GATE after | `.\.cp2-venv\Scripts\python.exe -B PG_GATE.py --module first_materials_runtime --schema b8_accept_b804r4gate --phase after_restart` | 0 |
| Patch apply check | `git -C .patch-verify-r4 apply --check integration_proposal/B8-04.patch` | 0 |
| Patch apply | `git -C .patch-verify-r4 apply integration_proposal/B8-04.patch` | 0 |
| Тесты чистого patch | `python -B -m unittest discover -s tests/first_materials_runtime -p "test_*.py" -v` | 0 |
| CP1 чистого patch | `python -B ACCEPTANCE_RUNNER.py cp1 --module first_materials_runtime.api` | 0 |

Итоги: 57 discovered, 50 выполнено и 7 PostgreSQL-тестов ожидаемо skipped без
DSN; CP1 — 19/19; отдельный прогон PostgreSQL — 7/7; manifest — 140/140.

Сырые stdout/stderr находятся в `evidence/raw/`. До исправления специально
запущены новые dispatch-тесты: код 1 и 8 несовпадений. После исправления тот же
набор: код 0, 5 тестовых методов OK.
