# Подключение, проверка и откат

## Подключение

1. Проверить `SHA256SUMS.txt`.
2. Из корня чистой выданной базы применить patch с отключённым преобразованием
   окончаний строк:

   ```text
   git -c core.autocrlf=false apply --check integration_proposal/B8-04.patch
   git -c core.autocrlf=false apply integration_proposal/B8-04.patch
   ```

3. Интегратор переносит SQL только в выделенный namespace B8-04 и подключает
   принятые owner-адаптеры F129/F130/materials/F115. Этот возврат не изменяет
   shared runtime и не заменяет владельцев зависимостей.
4. Для CP2 задать DSN только в `B8_TEST_DSN`, выбрать новую схему
   `b8_accept_<unique>`, выполнить `before_restart`, перезапустить только
   одноразовый PostgreSQL, затем выполнить `after_restart` на той же схеме без
   повторного seed.

## Откат

До интеграции patch можно обратить:

```text
git -c core.autocrlf=false apply -R integration_proposal/B8-04.patch
```

При установленной интеграции сначала остановить новые вызовы B8-04, сделать
резервную копию и выполнить штатный rollback интегратора. Собственные CP2-
объекты находятся только в выделенной схеме `b8_accept_*`; тестовую схему или
одноразовый контейнер удаляют только после сохранения нужных логов. Не удалять
чужие схемы, контейнеры или owner-таблицы.

## Ограничения

- `acceptance_pg.py` — компонентный адаптер CP2, не production-замена owner-
  компонентов.
- Реальные owner bytes, роли, shared runtime, backup/restore/reinstall и native
  gates относятся к CP3.
- Installed audit относится к CP4.

