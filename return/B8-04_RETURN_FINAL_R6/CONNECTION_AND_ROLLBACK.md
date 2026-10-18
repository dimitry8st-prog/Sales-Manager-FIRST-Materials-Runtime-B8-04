# Connection, installation and rollback boundaries

Only the candidate-owned acceptance adapter and its SQL namespace are exercised.
Public create_service/execute ABI is retained. The component never performs outbound delivery.
Apply integration_proposal/B8-04.patch to an untouched issued B8-04_R3 package and run CP0/CP1.
The runner separately verifies a clean patch apply and exact candidate bytes.
Real production connection roles, dependency hashes/native gates, migration installation,
backup/restore, rollback/reinstall and shared-runtime wiring require the CP3 integrator.
This return does not authorize production installation or modification of owner components.
For this disposable test environment rollback means remove only the container created by the runner.
For candidate-file review rollback means restore the clean issued copy; no production DDL is run.
UNKNOWN means commit or rollback was not confirmed; the caller must reconcile through durable identity,
without automatically repeating effects. A cleanup failure after confirmed commit cannot undo it.
