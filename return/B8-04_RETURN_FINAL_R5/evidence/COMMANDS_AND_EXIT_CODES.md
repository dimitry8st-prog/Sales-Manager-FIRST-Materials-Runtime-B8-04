# Команды и коды выхода R5

Команды запускались из корня выданного пакета. DSN передавался только в окружение процесса и в логи не попадал. Контейнер одноразовый: `b8-04-r5-cp2`, порт только localhost, аутентификация trust, пароль не задавался.

Каждая строка выполнена два раза. Оба раза код выхода 0.

| Проверка | Команда | pass1 | pass2 |
|---|---|---:|---:|
| Тесты без DSN | `py -3.12 -B -m unittest discover -s tests/first_materials_runtime -p test_*.py -v` | 0 | 0 |
| CP0 | `py -3.12 -B ACCEPTANCE_RUNNER.py cp0` | 0 | 0 |
| CP1 | `py -3.12 -B ACCEPTANCE_RUNNER.py cp1 --module first_materials_runtime.api` | 0 | 0 |
| CP2 process | `py -3.12 -B -m unittest discover -s tests/first_materials_runtime -p test_acceptance_pg.py -v` | 0 | 0 |
| PG_GATE before | `py -3.12 -B PG_GATE.py --module first_materials_runtime --schema b8_accept_b804r5g1 или g2 --phase before_restart` | 0 | 0 |
| Restart | `docker restart b8-04-r5-cp2` | 0 | 0 |
| PG_GATE after | та же схема, `--phase after_restart`, без seed | 0 | 0 |

Без DSN: 59 обнаружено, 50 выполнено, 9 пропущено. CP1: 19 PASS. PostgreSQL-тесты: 9/9. Схемы process-прогонов: `b8_accept_b804r5pass1` и `b8_accept_b804r5pass2`. Схемы PG_GATE: `b8_accept_b804r5g1` и `b8_accept_b804r5g2`.

Сырые логи: `evidence/raw/pass1` и `evidence/raw/pass2`. Проверка патча на чистой копии: `evidence/raw/clean_patch`.
