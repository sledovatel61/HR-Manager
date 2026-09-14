# Stage-4 replay: публикация подписанного канала (Phase 14 rework)

- Вердикт: **pass** (pass 19, fail 0)
- Сгенерирован: 2026-09-14T07:10:05Z (commit a89cb342f6d3)
- Код стадии извлечён из infra/scripts/pilot-drill.sh ДОСЛОВНО и исполнен без изменений.
- Docker не использовался; ключи — fixture (infra/release/testdata), не production.

| Шаг | Статус | Деталь |
| --- | --- | --- |
| stage4_extracted_verbatim | pass | make_snapshot+publish_channel+invocation |
| channel_published | pass | fixture-ed25519 |
| artifacts_present_channel-good | pass | hr-manager-windows-0.14.0.zip |
| manifest_fields_channel-good | pass | version=0.14.0 sha=222222222222 |
| inner_release_json_channel-good | pass | release.json в пакете декларирует 0.14.0 |
| manifest_signature_channel-good | pass | ed25519-verified |
| artifacts_present_channel-next | pass | hr-manager-windows-0.15.0.zip |
| manifest_fields_channel-next | pass | version=0.15.0 sha=444444444444 |
| inner_release_json_channel-next | pass | release.json в пакете декларирует 0.15.0 |
| manifest_signature_channel-next | pass | ed25519-verified |
| artifacts_present_channel-redirect | pass | hr-manager-windows-0.15.0.zip |
| manifest_fields_channel-redirect | pass | version=0.15.0 sha=444444444444 |
| inner_release_json_channel-redirect | pass | release.json в пакете декларирует 0.15.0 |
| manifest_signature_channel-redirect | pass | ed25519-verified |
| deterministic_package_0.15.0 | pass | same-bytes |
| tampered_manifest_prepared | pass | sig-flipped |
| channel_dir_populated | pass | good-channel |
| negative_mismatch_rejected | pass | rc=2 bad_release_json (сценарий бага ревью) |
| artifact_checks | pass | all-verifications-passed |
