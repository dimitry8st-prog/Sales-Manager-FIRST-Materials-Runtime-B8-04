# FIRST Materials Runtime · B8-04 R3

**Текущий возврат исполнителя — R5. Полная приёмка FIRST-системы не завершена: CP3 и CP4 — NOT_RUN.**

Компонент проверяет запрос и права, получает утверждённые материалы, сохраняет результат и предлагает действие через переданные зависимости. Сам не отправляет письма.

## Текущий пакет

[Скачать B8-04_RETURN_FINAL_R5.zip](return/B8-04_RETURN_FINAL_R5.zip).
[Итог проверок](return/B8-04_RETURN_FINAL_R5/RESULT.json), [карта требований](return/B8-04_RETURN_FINAL_R5/REQUIREMENT_MAP.md), [логи](return/B8-04_RETURN_FINAL_R5/evidence/COMMANDS_AND_EXIT_CODES.md).

Проверки R5 выполнены дважды, оба раза с кодом выхода 0.

- Собственные тесты без DSN: 59 обнаружено, 50 выполнено, 9 пропущено.
- CP0: PASS. CP1: 19/19 PASS.
- CP2: 9/9 PostgreSQL-тестов, включая оба конкурентных сценария worker/STOP, и PG_GATE до и после перезапуска того же одноразового контейнера без повторного seed.
- CP3: NOT_RUN, этап интегратора.
- CP4: NOT_RUN, этап независимого аудитора.

R3 и R4 остаются в `return/` как история. Их цифры не описывают текущий возврат.

## Воспроизведение из чистого клона

Python 3.12. Сначала распакуйте `assignment/B8-04_R3.zip` во временную папку. Внутри появится `B8-04_R3` с неизменёнными контрактами, `ACCEPTANCE_RUNNER.py` и `reference_source`.

Скопируйте каталоги `solution` и `tests` из пакета R5 в эту папку `B8-04_R3`, заменяя одноимённые файлы. Команды запускаются из распакованной `B8-04_R3`.

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

`reference_source` — выданная защищённая база для проверок. Для CP2 нужен отдельный одноразовый PostgreSQL и `B8_TEST_DSN`; команды и сырые логи есть в пакете R5. Реальные отправки и production deploy не выполнялись.

## Целостность

ZIP использует пути `/`. `SHA256SUMS.txt` проверяет файлы внутри корневой папки `B8-04_RETURN_FINAL_R5`. Хеш архива записан в [validation.txt](return/B8-04_RETURN_FINAL_R5.zip.validation.txt). Секреты, DSN, кэши и окружения в архив не включены.
