# FIRST Materials Runtime · B8-04 R3

**Возврат исполнителя подготовлен и опубликован. Полная приёмка FIRST-системы не завершена: CP3 и CP4 — NOT_RUN.**

Компонент проверяет запрос и права, получает утверждённые материалы, сохраняет результат и предлагает действие через переданные зависимости. Сам не отправляет письма.

## Финальный пакет

[Скачать B8-04_RETURN_FINAL_R3.zip](return/B8-04_RETURN_FINAL_R3.zip).
[Итог проверок](return/B8-04_RETURN_FINAL_R3/RESULT.json), [карта требований](return/B8-04_RETURN_FINAL_R3/REQUIREMENT_MAP.md), [логи](return/B8-04_RETURN_FINAL_R3/evidence/COMMANDS_AND_EXIT_CODES.md).

- Собственные тесты: 49, из них 45 PASS, 4 PostgreSQL-теста SKIP без DSN.
- CP0: PASS; CP1: 19/19 PASS. Независимо повторены на базе исходного задания.
- CP2: PASS по приложенным логам одноразового PostgreSQL, включая перезапуск без повторного seed. Независимая повторная проверка CP2 не выполнялась.
- CP3: NOT_RUN, интегратор; нужны принятые owner-адаптеры, точные версии зависимостей, shared runtime, роли и backup/rollback.
- CP4: NOT_RUN, независимый аудит установленной системы.

## Воспроизведение из чистого клона

Python 3.12. Сначала распакуйте `assignment/B8-04_R3.zip` во временную папку. Внутри появится `B8-04_R3` с неизменёнными контрактами, `ACCEPTANCE_RUNNER.py` и `reference_source`.

Скопируйте каталоги `solution` и `tests` из финального пакета в эту папку `B8-04_R3`, объединяя каталоги и заменяя только одноимённые файлы реализации/тестов. Все следующие команды запускаются **из распакованной B8-04_R3**, а не из корня клона.

Linux/macOS:

```bash
export PYTHONPATH=solution:reference_source
python -B -m unittest discover -s tests/first_materials_runtime -p 'test_*.py' -v
python -B ACCEPTANCE_RUNNER.py cp0
python -B ACCEPTANCE_RUNNER.py cp1 --module first_materials_runtime.api
```

Windows PowerShell:

```powershell
$env:PYTHONPATH = 'solution;reference_source'
python -B -m unittest discover -s tests/first_materials_runtime -p 'test_*.py' -v
python -B ACCEPTANCE_RUNNER.py cp0
python -B ACCEPTANCE_RUNNER.py cp1 --module first_materials_runtime.api
```

`reference_source` — выданная защищённая база для проверок, не подтверждение готовности настоящих owner-адаптеров. Для CP2 нужен отдельный одноразовый PostgreSQL и `B8_TEST_DSN`; инструкции и исторические команды см. в финальном пакете. Реальные отправки и production deploy не выполнялись.

## Целостность и история

ZIP использует стандартные пути `/`; `SHA256SUMS.txt` проверяет каждый файл внутри корневой папки пакета. Хеш ZIP указан в [validation.txt](return/B8-04_RETURN_FINAL_R3.zip.validation.txt).

Исходный архив задания сохранён без изменений. Промежуточный CP1-only возврат и старые отчёты оставлены как история; актуальный возврат — `B8-04_RETURN_FINAL_R3`. Секреты, DSN, кэши и окружения в него не включены.
