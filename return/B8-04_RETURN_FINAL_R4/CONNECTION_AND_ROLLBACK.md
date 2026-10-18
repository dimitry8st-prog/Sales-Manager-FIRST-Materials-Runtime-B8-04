# Воспроизведение, подключение и откат

## Быстрые проверки

Из корня выданного пакета с Python 3.12:

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONPATH='solution'
py -3.12 -B -m unittest discover -s tests/first_materials_runtime -p "test_*.py" -v
py -3.12 -B ACCEPTANCE_RUNNER.py cp0
py -3.12 -B ACCEPTANCE_RUNNER.py cp1 --module first_materials_runtime.api
```

## CP2

Использовать только одноразовый PostgreSQL и выделенные схемы `b8_accept_*`.
DSN хранить лишь в окружении:

```powershell
$env:B8_TEST_DSN='<secret DSN for disposable PostgreSQL>'
$env:B8_TEST_SCHEMA='b8_accept_b804r4tests'
py -3.12 -B -m unittest tests.first_materials_runtime.test_acceptance_pg -v
py -3.12 -B PG_GATE.py --module first_materials_runtime --schema b8_accept_b804r4gate --phase before_restart
# настоящий restart того же одноразового PostgreSQL
py -3.12 -B PG_GATE.py --module first_materials_runtime --schema b8_accept_b804r4gate --phase after_restart
```

Между фазами нельзя пересоздавать схему или повторять seed. `after_restart`
сам проверяет сохранённый marker, изменившееся postmaster start time и replay.

## Применение patch

```powershell
git apply --check integration_proposal/B8-04.patch
git apply integration_proposal/B8-04.patch
```

## Откат

До интеграции сохранить резервную копию затрагиваемых путей. Для чистого
применения этого patch можно выполнить:

```powershell
git apply --check -R integration_proposal/B8-04.patch
git apply -R integration_proposal/B8-04.patch
```

Если интегратор уже менял те же файлы, автоматический reverse применять
нельзя: восстановить только семь файлов из своей резервной копии и проверить
diff. Миграция создаёт лишь таблицы в выделенной схеме; удалять схему можно
только после проверки её точного имени и при отсутствии нужных данных.
