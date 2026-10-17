# Повторная проверка 17.10.2026

На Python 3.12, в распакованном assignment/B8-04_R3.zip с наложенными solution/tests финального возврата:

```bash
PYTHONPATH=solution:reference_source python -B -m unittest discover -s tests/first_materials_runtime -p 'test_*.py' -q
python -B ACCEPTANCE_RUNNER.py cp0
PYTHONPATH=solution:reference_source python -B ACCEPTANCE_RUNNER.py cp1 --module first_materials_runtime.api
```

49 тестов, OK (skipped=4); CP0 PASS; CP1 19/19 PASS. Все команды завершились с exit 0. PostgreSQL в этом проходе не запускался. Оригинальные Windows-логи COMMANDS_AND_EXIT_CODES.md относятся к исходной рабочей папке; её стенд и reference_source хранятся в assignment/B8-04_R3.zip, а не в корне git-клона.
