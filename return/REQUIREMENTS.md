# Требование → файл → тест → лог

Все пути логов относительны return/evidence/.

| Требование | Код | Тест | Лог |
|---|---|---|---|
| Входные поля и доступ | solution/first_materials_runtime/api.py | test_unknown_operation, test_extra_field_before_authentication, test_unauthenticated, test_missing_capability, test_foreign_client | unit.log; cp1.log |
| STOP/HUMAN, каталог и событие | solution/first_materials_runtime/api.py | test_terminal_states_block_without_effects, test_unapproved_catalog_blocked, test_event_scope_mismatch, test_unsuitable_events_blocked_without_materials | unit.log; cp1.log |
| Descriptor и допустимые факты | solution/first_materials_runtime/api.py | MaterialDescriptorTests; test_material_with_foreign_facts_rejected | unit.log; cp1.log |
| Сохранение и откат | solution/first_materials_runtime/api.py | test_materials_save_exact_request_then_dispatch, test_materials_save_ref_mismatch_rolls_back, test_foreign_facts_after_staging_fully_roll_back, test_invalid_descriptor_after_staging_fully_rolls_back | unit.log; cp1.log |
| Доменные и транспортные ошибки | solution/first_materials_runtime/api.py | test_source_domain_error_becomes_invalid_and_rolls_back, test_transport_error_with_confirmed_rollback_is_not_retried, test_uncertain_commit_returns_unknown_without_retrying_effects | unit.log |
| Повтор и конфликт | solution/first_materials_runtime/api.py | test_same_request_replays_without_new_effects, test_changed_request_conflicts_without_new_effects | unit.log; cp1.log |
| Первый контакт | solution/first_materials_runtime/api.py | test_initial_qualification_saves_and_queues, test_initial_needs_review_does_not_save, test_initial_invalid_descriptor_after_staging_rolls_back; CP1 initial_not_qualified | unit.log; cp1.log |

CP0 подтверждает выданную базу и целостность, не полноту реализации. CP2–CP4 остаются NOT_RUN; карта не заявляет полного покрытия всех требований.
