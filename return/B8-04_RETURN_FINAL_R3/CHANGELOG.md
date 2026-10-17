# CHANGELOG — B8-04 R3

## Исправления orchestration

- Известные исключения исходных компонентов преобразуются в `INVALID` с
  исходным `code` только после проверки точного класса и модуля. Произвольное
  исключение с полем `code` доверенным не считается.
- Транспортный сбой после подтверждённого rollback даёт
  `DEPENDENCY_UNAVAILABLE`. Неопределённый результат commit даёт `UNKNOWN` с
  устойчивым `request_id`; выполняется только диагностическое чтение replay,
  без автоматического повтора эффектов.
- Отказы после materialization выходят из transaction context исключением.
  Поэтому staging, save, dispatch и replay откатываются вместе.
- Перед `materials.save` и перед `dispatch.propose` повторно проверяются
  authority и catalog. STOP/HUMAN приводит к `BLOCKED`, смена состояния или
  версии — к `STALE`, с rollback текущей транзакции.
- Усилена строгая проверка revisions и ответов зависимостей.

## PostgreSQL CP2

- Добавлен `acceptance_pg.py` с точным интерфейсом `PG_GATE.py`.
- Добавлена миграция `migrations/0001.sql` для scoped lock и JSONB state.
- Каждая операция использует отдельное реальное соединение и одну транзакцию.
- Конкуренция одного scope сериализуется через `SELECT ... FOR UPDATE` при
  `READ COMMITTED`: ожидающий запрос после commit видит replay и не повторяет
  save/dispatch.
- Записи синтетических dependency ports стенда переносятся в ту же SQL-
  транзакцию; rollback удаляет их вместе с replay.

## Тесты

- Добавлены regressions для domain/transport errors, rollback staging,
  descriptor/fact scope, отсутствия save/dispatch/replay после отказа,
  STOP/config races и uncertain commit.
- Добавлены реальные PostgreSQL-тесты rollback, constraints, scope isolation и
  двух независимых конкурентных сервисов.

## Локальная среда редактора

- В локальном `.vscode/settings.json` сохранён `./solution` и добавлен
  `./reference_source` в `python.analysis.extraPaths`, чтобы Pylance находил
  выданный `sales_template_materialization`.
- Настройка локальная: она не входит ни в integration patch, ни в ZIP.

