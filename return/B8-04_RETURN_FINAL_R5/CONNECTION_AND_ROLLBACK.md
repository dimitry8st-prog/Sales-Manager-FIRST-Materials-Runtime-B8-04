# Подключение и откат

## Быстрые проверки

Из корня выданного пакета, Python 3.12:

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONPATH='solution'
py -3.12 -B -m unittest discover -s tests/first_materials_runtime -p "test_*.py" -v
py -3.12 -B ACCEPTANCE_RUNNER.py cp0
py -3.12 -B ACCEPTANCE_RUNNER.py cp1 --module first_materials_runtime.api
```

## CP2

Только одноразовый PostgreSQL и схемы `b8_accept_*`. DSN не записывать в файлы.

```powershell
$env:B8_TEST_DSN='<dsn одноразового PostgreSQL>'
$env:B8_TEST_SCHEMA='b8_accept_b804r5tests'
py -3.12 -B -m unittest discover -s tests/first_materials_runtime -p "test_acceptance_pg.py" -v
py -3.12 -B PG_GATE.py --module first_materials_runtime --schema b8_accept_b804r5gate --phase before_restart
docker restart <тот же одноразовый контейнер>
py -3.12 -B PG_GATE.py --module first_materials_runtime --schema b8_accept_b804r5gate --phase after_restart
```

Между фазами схему не пересоздавать и seed не повторять.

## Патч

```powershell
git -c core.autocrlf=false apply --check integration_proposal/B8-04.patch
git -c core.autocrlf=false apply integration_proposal/B8-04.patch
```

## Откат

```powershell
git -c core.autocrlf=false apply --check -R integration_proposal/B8-04.patch
git -c core.autocrlf=false apply -R integration_proposal/B8-04.patch
```

Если те же файлы уже менял интегратор, reverse не применять: вернуть семь файлов из своей копии. Схему `b8_accept_*` удалять только после проверки точного имени.
