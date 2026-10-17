# B8-04 R3 — окончательный возврат исполнителя

Возврат содержит собственную реализацию `first_materials_runtime`, компонентные
тесты, PostgreSQL-адаптер CP2, SQL своего namespace, integration patch и сырые
логи проверок.

## Итог

- CP0: PASS, код выхода 0; защищённые 140 файлов совпали с выданным manifest.
- Собственные тесты без DSN: PASS, 49 тестов, из них 4 PostgreSQL-теста ожидаемо
  пропущены; 45 быстрых тестов выполнены.
- CP1: PASS, код выхода 0; 19 из 19 synthetic cases.
- CP2: PASS на одноразовом PostgreSQL; `before_restart` и `after_restart`
  выполнены на одной схеме и одних данных без повторного seed. Настоящий
  restart контейнера подтверждён самим `PG_GATE.py`.
- Дополнительные CP2-тесты: PASS, 4 из 4 — rollback staging, scoped isolation,
  конкурентный одинарный effect chain и SQL constraint.
- Локальная диагностика Pylance: путь `./reference_source` добавлен к уже
  существующему `./solution`; исходный компонент и диагностика не изменялись.
  `.vscode/settings.json` в возврат не включён.
- CP3: NOT_RUN — подключение принятых owner-адаптеров и shared runtime выполняет
  интегратор.
- CP4: NOT_RUN — установленную систему проверяет независимый аудитор.

Это готовый возврат исполнителя, но не утверждение о готовности всей
установленной FIRST-системы.

## Состав

- `solution/first_materials_runtime/` — код, CP2-адаптер и SQL;
- `tests/first_materials_runtime/` — быстрые и реальные PostgreSQL-тесты;
- `integration_proposal/B8-04.patch` — полный patch относительно чистой
  защищённой базы;
- `CHANGELOG.md` — изменения;
- `CONNECTION_AND_ROLLBACK.md` — подключение, проверка и откат;
- `REQUIREMENT_MAP.md` — требование → файл → тест → сырой лог;
- `RESULT.json` — машинно-читаемый итог;
- `evidence/raw/` — stdout/stderr, версии и проверки patch;
- `SHA256SUMS.txt` — хеши файлов возврата, кроме самого manifest.

Пароли, DSN, виртуальное окружение, кэши и данные PostgreSQL в возврат не входят.


## Повторная проверка и переносимость — 17.10.2026

CP0, CP1 и 49 тестов повторно запущены на распакованном исходном задании. Для запуска необходим PYTHONPATH=solution:reference_source в Linux/macOS или solution;reference_source в Windows. Подробные команды — в корневом README репозитория. Четыре PG-теста без DSN пропущены, CP2 в этой повторной проверке не запускался. ZIP пересобран с путями `/`; содержимое runtime и защищённой базы не менялось. Исторические логи сохранены без переписывания.
