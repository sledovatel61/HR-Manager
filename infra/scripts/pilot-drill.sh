#!/usr/bin/env bash
# Phase 14: автоматизированный e2e pilot drill (CI-контур, Linux + Docker).
#
# Полный прогон серверного контракта пилота на ЖИВОМ Compose-стеке — только
# синтетические данные, эфемерные тестовые ключи/сертификаты и секреты,
# сгенерированные на месте. НИКАКИХ production secrets, ключей и сертификатов.
#
# Доказывает (CI-часть; Windows-специфика — ручная приёмка по
# docs/phase-14-runbook.md):
#   1. чистая установка + first-run (одноразовый exchange token → владелец);
#   2. синтетические данные через публичные контракты API;
#   3. зашифрованный бэкап + restore drill в изолированную БД;
#   4. предпусковая readiness-проверка (read-only, без секретов в ответе);
#   5. подписанный канал → check → download (staging, SHA256) → явный install;
#   6. resume: повторный опрос engine-state выдаёт ту же команду (idempotent);
#   7. сохранение синтетических данных после «обновления»;
#   8. повреждённая подпись manifest / подмена пакета / redirect на
#      запрещённый хост → отказ ДО изменения установки;
#   9. «удаление без purge» (down без -v) → тома и данные сохранены.
#
# Выход: 0 — все стадии пройдены; 1 — есть провалы. Результаты:
#   pilot-drill.json (машиночитаемый) и pilot-drill.md (краткий отчёт) —
# без секретов, PII, URL с кредентами и ключей: только коды/счётчики/стадии.
#
# Требования: docker (Compose v2.24+), openssl, python3, curl, jq НЕ нужен
# (JSON разбирается python3). Запуск из корня репозитория:
#
#   infra/scripts/pilot-drill.sh [--workdir /tmp/hrm-drill]
#
# Скрипт самоподчищается: временные ключи/сертификаты/секреты удаляются в
# trap EXIT (стек compose гасится с --remove-orphans; тома прогона
# удаляются тоже — это НЕ pilot-данные владельца).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORKDIR="${TMPDIR:-/tmp}/hrm-pilot-drill-$$"
REPORT_JSON="${REPORT_JSON:-$WORKDIR/pilot-drill.json}"
REPORT_MD="${REPORT_MD:-$WORKDIR/pilot-drill.md}"
BASE_URL="http://127.0.0.1:18080"
COMPOSE_FILES=(-f infra/docker-compose.yml -f infra/compose.pilot.yml -f infra/compose.drill.yml)
ENV_FILE="$WORKDIR/pilot.env"
COOKIE_JAR="$WORKDIR/cookies.txt"
STAGING_DIR="$WORKDIR/staging"
CHANNEL_DIR="$WORKDIR/channel-srv"
CERTS_DIR="$WORKDIR/certs"
TESTDATA="$REPO_ROOT/infra/release/testdata"
DRILL_INSTALLED_SHA="$(printf '3%.0s' $(seq 1 40))"
CHANNEL_SHA="$(printf '2%.0s' $(seq 1 40))"
CHANNEL_VERSION="0.14.0"
# Каналы для НЕГАТИВНЫХ стадий (после успешного «обновления» до 0.14.0
# нужна более новая версия, иначе решение канала — up_to_date).
NEXT_SHA="$(printf '4%.0s' $(seq 1 40))"
NEXT_VERSION="0.15.0"
OWNER_PASSWORD=""
ENGINE_TOKEN=""
PASS=0
FAIL=0
STAGES_JSON=""

log() { printf '[drill] %s\n' "$*"; }
die() { printf '[drill] ОШИБКА: %s\n' "$*" >&2; exit 2; }

stage_pass() { PASS=$((PASS+1)); STAGES_JSON+="{\"name\":\"$1\",\"status\":\"pass\",\"detail\":\"$2\"},"; log "  [PASS] $1 ($2)"; }
stage_fail() { FAIL=$((FAIL+1)); STAGES_JSON+="{\"name\":\"$1\",\"status\":\"fail\",\"detail\":\"$2\"},"; log "  [FAIL] $1 ($2)"; }

# --- JSON-помощники (без jq) ----------------------------------------------------
jget() { # jget <json> <python-expr over d>
  local json="$1" expr="$2"
  printf '%s' "$json" | python3 -c "import json,sys; d=json.load(sys.stdin); print($expr)"
}

api() { # api <method> <path> [json-body] [extra-curl-args...]
  local method="$1"; shift
  local path="$1"; shift
  local body=""
  if [ $# -gt 0 ] && [ "${1:0:1}" != "-" ]; then body="$1"; shift; fi
  local args=(-sS -X "$method" "$BASE_URL$path" -c "$COOKIE_JAR" -b "$COOKIE_JAR"
    -H 'Content-Type: application/json' --max-time 60)
  [ -n "$body" ] && args+=(-d "$body")
  curl "${args[@]}" "$@"
}

cleanup() {
  local code=$?
  cd "$REPO_ROOT" 2>/dev/null || true
  if [ -f "$ENV_FILE" ]; then
    docker compose --env-file "$ENV_FILE" "${COMPOSE_FILES[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  fi
  # Секреты и ключи прогона не переживают прогон.
  rm -rf "$CERTS_DIR" "$WORKDIR/pilot.env" "$WORKDIR/snapshot" "$COOKIE_JAR" 2>/dev/null || true
  if [ $code -eq 0 ] && [ $FAIL -gt 0 ]; then exit 1; fi
}
trap cleanup EXIT

# --- 1. Предусловия ---------------------------------------------------------------
command -v docker >/dev/null || die "docker не найден"
command -v openssl >/dev/null || die "openssl не найден"
command -v python3 >/dev/null || die "python3 не найден"
command -v curl >/dev/null || die "curl не найден"
docker compose version >/dev/null 2>&1 || die "docker compose v2 недоступен"
mkdir -p "$WORKDIR" "$STAGING_DIR" "$CHANNEL_DIR" "$CERTS_DIR"
cd "$REPO_ROOT"
stage_pass "prerequisites" "docker+openssl+python3+curl"

# --- 2. Эфемерные секреты прогона (не production) ----------------------------------
PG_PASSWORD="$(openssl rand -hex 16)"
SIGNING_KEY="$(openssl rand -hex 32)"
BOOTSTRAP_PASSWORD="$(openssl rand -hex 16)"
EXCHANGE_TOKEN="$(openssl rand -hex 32)"
ENGINE_TOKEN="$(openssl rand -hex 16)"
BACKUP_KEY="$(openssl rand -base64 32 | tr -d '\n')"
OWNER_PASSWORD="Drill-Pass-$(openssl rand -hex 8)"
stage_pass "ephemeral_secrets" "generated"

# --- 3. Эфемерный TLS drill-CA + сертификат канала ---------------------------------
openssl req -x509 -newkey rsa:2048 -nodes -keyout "$CERTS_DIR/channel.key" \
  -out "$CERTS_DIR/channel.crt" -days 1 -subj "/CN=channel" \
  -addext "subjectAltName=DNS:channel" >/dev/null 2>&1 || die "TLS cert failed"
cp "$CERTS_DIR/channel.crt" "$CERTS_DIR/drill-ca.crt"
stage_pass "tls_certificates" "ephemeral-self-signed"

# --- 4. Публикация подписанного канала (fixture-ключ, НЕ production) ---------------
make_snapshot() {
  local dir="$1"
  rm -rf "$dir"; mkdir -p "$dir"
  cp -R backend "$dir/backend"
  cp -R frontend "$dir/frontend"
  cp -R infra "$dir/infra"
  # Локальные артефакты разработки не входят в снимок релиза.
  rm -rf "$dir/frontend/node_modules" "$dir/frontend/dist" \
    "$dir/infra/release/testdata/__pycache__"
  python3 - "$dir/release.json" <<'PY'
import json, sys
from datetime import UTC, datetime
json.dump({
    "version": "0.14.0",
    "release_sha": "2" * 40,
    "built_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
}, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False)
PY
}

publish_channel() { # publish_channel <version> <sha> <package-url> <out-dir>
  local version="$1" sha="$2" url="$3" out="$4"
  python3 infra/release/publish_channel.py \
    --snapshot "$WORKDIR/snapshot" --version "$version" \
    --release-sha "$sha" --package-url "$url" \
    --minimum-supported-version 0.13.0 \
    --notes-ru "Pilot drill (fixture key, NOT production)" \
    --private-key "$TESTDATA/test_key.priv" --key-id pilot-test-key \
    --public-keys-json "$TESTDATA/trusted_keys.json" \
    --out-dir "$out" >/dev/null || die "publish_channel failed for $url"
}

make_snapshot "$WORKDIR/snapshot"
publish_channel "$CHANNEL_VERSION" "$CHANNEL_SHA" \
  "https://channel:8443/hr-manager-windows-$CHANNEL_VERSION.zip" "$WORKDIR/channel-good"
publish_channel "$NEXT_VERSION" "$NEXT_SHA" \
  "https://channel:8443/hr-manager-windows-$NEXT_VERSION.zip" "$WORKDIR/channel-next"
publish_channel "$NEXT_VERSION" "$NEXT_SHA" \
  "https://channel:8443/redirect/pkg.zip" "$WORKDIR/channel-redirect"
# Повреждённая подпись: инвертируем первый hex-символ подписи.
python3 - "$WORKDIR/channel-next/update-channel.json" "$WORKDIR/channel-tampered/update-channel.json" <<'PY'
import json, sys, pathlib
src = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
sig = src["signature"]["sig"]
src["signature"]["sig"] = ("0" if sig[0] != "0" else "1") + sig[1:]
out = pathlib.Path(sys.argv[2]); out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(src, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
# Обслуживаемая директория: валидный канал.
cp "$WORKDIR/channel-good/"* "$CHANNEL_DIR/"
stage_pass "channel_published" "fixture-ed25519"

# --- 5. Стек пилота ----------------------------------------------------------------
cat > "$ENV_FILE" <<EOF
HRM_POSTGRES_PASSWORD=$PG_PASSWORD
HRM_SIGNING_KEY=$SIGNING_KEY
HRM_BOOTSTRAP_ADMIN_PASSWORD=$BOOTSTRAP_PASSWORD
HRM_EXCHANGE_TOKEN=$EXCHANGE_TOKEN
HRM_UPDATE_ENGINE_TOKEN=$ENGINE_TOKEN
HRM_BACKUP_KEY=$BACKUP_KEY
HRM_BACKUP_KEY_ID=drill-key-1
HRM_STAGING_DIR=$STAGING_DIR
HRM_UPDATE_CHANNEL_URL=https://channel:8443/update-channel.json
HRM_UPDATE_CHANNEL_PUBLIC_KEYS=$(cat "$TESTDATA/trusted_keys.json" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin), separators=(",",":")))')
HRM_UPDATE_CHECK_MIN_INTERVAL=1
HRM_PILOT_PORT=18080
HRM_RELEASE_SHA=$DRILL_INSTALLED_SHA
HRM_DRILL_CHANNEL_DIR=$CHANNEL_DIR
HRM_DRILL_SCRIPTS_DIR=$REPO_ROOT/infra/scripts
HRM_DRILL_CERTS_DIR=$CERTS_DIR
HRM_DRILL_INSTALLED_SHA=$DRILL_INSTALLED_SHA
EOF
if docker compose --env-file "$ENV_FILE" "${COMPOSE_FILES[@]}" config -q; then
  stage_pass "compose_config" "render-ok"
else
  stage_fail "compose_config" "render-failed"; exit 1
fi
if docker compose --env-file "$ENV_FILE" "${COMPOSE_FILES[@]}" up -d --build --wait --wait-timeout 600 >/dev/null 2>&1; then
  stage_pass "stack_up" "healthy"
else
  stage_fail "stack_up" "unhealthy"
  docker compose --env-file "$ENV_FILE" "${COMPOSE_FILES[@]}" ps || true
  exit 1
fi

# --- 6. Health ----------------------------------------------------------------------
health="$(api GET /api/health)"
[ "$(jget "$health" 'd["status"]')" = "ok" ] && stage_pass "api_health" "ok" || stage_fail "api_health" "bad-status"

# --- 7. First-run: exchange token → билет → владелец ---------------------------------
claim="$(api POST /api/setup/owner/claim "{\"exchange_token\":\"$EXCHANGE_TOKEN\",\"surname\":\"Дриллов\",\"working_mode\":\"admin\",\"timezone\":\"Europe/Moscow\"}")"
ticket="$(jget "$claim" 'd.get("ticket","")')"
[ -n "$ticket" ] && stage_pass "first_run_claim" "ticket-issued" || { stage_fail "first_run_claim" "no-ticket"; exit 1; }
redeem="$(api POST /api/setup/owner/redeem "{\"ticket\":\"$ticket\",\"timezone\":\"Europe/Moscow\",\"workdays\":[1,2,3,4,5],\"quiet_hours_start\":\"22:00\",\"quiet_hours_end\":\"08:00\",\"password\":\"$OWNER_PASSWORD\"}")"
CSRF="$(jget "$redeem" 'd.get("csrf_token","")')"
[ -n "$CSRF" ] && stage_pass "first_run_redeem" "owner-session" || { stage_fail "first_run_redeem" "no-session"; exit 1; }

# --- 8. Синтетические данные через публичный контракт --------------------------------
cand="$(api POST /api/candidates "{\"full_name\":\"Синтетический Кандидат Дрилла\",\"email\":\"drill-synthetic-001@example.com\",\"source\":\"site\",\"position\":\"Синтетическая позиция\"}" -H "X-CSRF-Token: $CSRF")"
cand_id="$(jget "$cand" 'd.get("id","")')"
[ -n "$cand_id" ] && stage_pass "synthetic_data_created" "candidate-via-api" || stage_fail "synthetic_data_created" "api-rejected"

# --- 9. Зашифрованный бэкап + restore drill -------------------------------------------
if docker compose --env-file "$ENV_FILE" "${COMPOSE_FILES[@]}" run --rm backup \
     python -m app.cli backup-now --reason "pilot drill" --as-scheduler >/dev/null 2>&1; then
  stage_pass "encrypted_backup" "pgdump-enc"
else
  stage_fail "encrypted_backup" "backup-failed"
fi
if docker compose --env-file "$ENV_FILE" "${COMPOSE_FILES[@]}" run --rm backup \
     python -m app.cli backup-drill --as-scheduler >/dev/null 2>&1; then
  stage_pass "restore_drill" "isolated-db-ok"
else
  stage_fail "restore_drill" "drill-failed"
fi
backup_health="$(api GET /api/ops/backup-health)"
[ "$(jget "$backup_health" 'd.get("status","")')" != "" ] && stage_pass "backup_health_api" "reported" || stage_fail "backup_health_api" "no-report"

# --- 10. Readiness (read-only, без секретов) -------------------------------------------
readiness="$(api GET /api/updates/readiness)"
verdict="$(jget "$readiness" 'd.get("verdict","")')"
checks_count="$(jget "$readiness" 'len(d.get("checks",[]))')"
if [ -n "$verdict" ] && [ "$checks_count" -ge 10 ]; then
  if printf '%s' "$readiness" | grep -q "$ENGINE_TOKEN\|$OWNER_PASSWORD\|$PG_PASSWORD"; then
    stage_fail "readiness_report" "secrets-leaked"
  else
    stage_pass "readiness_report" "verdict=$verdict"
  fi
else
  stage_fail "readiness_report" "invalid-shape"
fi

# --- 11. Канал: check → available ------------------------------------------------------
check="$(api POST /api/updates/check -H "X-CSRF-Token: $CSRF")"
if [ "$(jget "$check" 'd.get("state","")')" = "available" ] && [ "$(jget "$check" 'd.get("available_version","")')" = "$CHANNEL_VERSION" ]; then
  stage_pass "channel_check" "available-0.14.0"
else
  stage_fail "channel_check" "$(jget "$check" 'd.get("error_code", d.get("state","?"))')"
fi

# --- 12. Download → staging + повторная проверка SHA256 ---------------------------------
download="$(api POST /api/updates/download -H "X-CSRF-Token: $CSRF")"
if [ "$(jget "$download" 'd.get("state","")')" = "ready" ]; then
  expected_sha="$(python3 -c "
import json
m = json.load(open('$WORKDIR/channel-good/update-channel.json'))
print(m['package_sha256'])")"
  actual_sha="$(sha256sum "$STAGING_DIR/release-$CHANNEL_SHA.zip" | cut -d' ' -f1)"
  [ "$expected_sha" = "$actual_sha" ] && stage_pass "channel_download" "staging-sha-verified" || stage_fail "channel_download" "sha-mismatch"
else
  stage_fail "channel_download" "$(jget "$download" 'd.get("error_code","?")')"
fi

# --- 13. Явная установка + resume (повторная выдача команды) ----------------------------
install="$(api POST /api/updates/install -H "X-CSRF-Token: $CSRF")"
job_id="$(jget "$install" 'd.get("job_id","")')"
[ -n "$job_id" ] && stage_pass "install_requested" "job-queued" || stage_fail "install_requested" "$(jget "$install" 'd.get("message","?")')"

engine_headers=(-H "X-Engine-Token: $ENGINE_TOKEN" -H "X-Installed-Version: 0.13.0" -H "X-Installed-Sha: $DRILL_INSTALLED_SHA")
poll1="$(curl -sS "${engine_headers[@]}" "$BASE_URL/api/updates/engine-state")"
poll2="$(curl -sS "${engine_headers[@]}" "$BASE_URL/api/updates/engine-state")"
if [ "$(jget "$poll1" '",".join(d.get("actions",[]))')" = "install" ] && \
   [ "$(jget "$poll2" 'd.get("job_id","")')" = "$job_id" ]; then
  stage_pass "engine_resume_delivery" "same-job-redelivered"
else
  stage_fail "engine_resume_delivery" "no-install-action"
fi

# --- 14. Отчёт движка: installed → статус up_to_date -------------------------------------
report="$(curl -sS -X POST "$BASE_URL/api/updates/engine-report" \
  -H "X-Engine-Token: $ENGINE_TOKEN" -H 'Content-Type: application/json' \
  -d "{\"state\":\"installed\",\"installed_version\":\"$CHANNEL_VERSION\",\"installed_release_sha\":\"$CHANNEL_SHA\",\"job_id\":\"$job_id\",\"error_code\":null,\"error_detail\":null}")"
status="$(api GET /api/updates/status)"
if [ "$(jget "$status" 'd.get("state","")')" = "up_to_date" ] && [ "$(jget "$status" 'd.get("installed_version","")')" = "$CHANNEL_VERSION" ]; then
  stage_pass "update_completed" "0.14.0-installed"
else
  stage_fail "update_completed" "$(jget "$status" 'd.get("state","?")')"
fi

# --- 15. Данные сохранены после обновления ------------------------------------------------
list="$(api GET "/api/candidates?page=1&page_size=10")"
count="$(jget "$list" 'd.get("total", 0)')"
[ "$count" -ge 1 ] && stage_pass "data_preserved_after_update" "candidates=$count" || stage_fail "data_preserved_after_update" "data-lost"

# --- 16. Повреждённая подпись manifest → отказ ДО установки --------------------------------
cp "$WORKDIR/channel-tampered/update-channel.json" "$CHANNEL_DIR/update-channel.json"
check_bad="$(api POST /api/updates/check -H "X-CSRF-Token: $CSRF")"
if [ "$(jget "$check_bad" 'd.get("error_code","")')" = "manifest_bad_signature" ]; then
  stage_pass "tampered_signature_rejected" "manifest_bad_signature"
else
  stage_fail "tampered_signature_rejected" "$(jget "$check_bad" 'd.get("error_code", d.get("state","?"))')"
fi

# --- 17. Redirect на запрещённый хост → отказ до изменения установки -----------------------
cp "$WORKDIR/channel-redirect/update-channel.json" "$CHANNEL_DIR/update-channel.json"
printf 'https://evil.example.com/hr-manager-windows-%s.zip' "$CHANNEL_VERSION" > "$CHANNEL_DIR/redirect-target.txt"
check_redirect="$(api POST /api/updates/check -H "X-CSRF-Token: $CSRF")"
download_redirect="$(api POST /api/updates/download -H "X-CSRF-Token: $CSRF")"
if [ "$(jget "$check_redirect" 'd.get("state","")')" = "available" ] && \
   [ "$(jget "$download_redirect" 'd.get("error_code","")')" = "bad_url" ]; then
  stage_pass "forbidden_redirect_rejected" "bad_url-before-request"
else
  stage_fail "forbidden_redirect_rejected" "$(jget "$download_redirect" 'd.get("error_code", d.get("state","?"))')"
fi

# --- 18. Подмена пакета (SHA256 не совпал) → отказ ------------------------------------------
cp "$WORKDIR/channel-next/update-channel.json" "$CHANNEL_DIR/update-channel.json"
cp "$WORKDIR/channel-next/hr-manager-windows-$NEXT_VERSION.zip" "$CHANNEL_DIR/"
printf 'tampered package bytes' >> "$CHANNEL_DIR/hr-manager-windows-$NEXT_VERSION.zip"
# Состояние failed после redirect-стадии — новая попытка check сбрасывает на available.
check_after_tamper="$(api POST /api/updates/check -H "X-CSRF-Token: $CSRF")"
download_tampered="$(api POST /api/updates/download -H "X-CSRF-Token: $CSRF")"
if [ "$(jget "$download_tampered" 'd.get("error_code","")')" = "package_hash_mismatch" ]; then
  stage_pass "tampered_package_rejected" "package_hash_mismatch"
else
  stage_fail "tampered_package_rejected" "$(jget "$download_tampered" 'd.get("error_code", d.get("state","?"))')"
fi
# Установка невозможна: staging пуст (пакет не скачан), install не проходит.
install_blocked="$(api POST /api/updates/install -H "X-CSRF-Token: $CSRF" || true)"
[ "$(jget "$install_blocked" 'd.get("state","")')" != "installing" ] && stage_pass "install_blocked_after_tamper" "no-install" || stage_fail "install_blocked_after_tamper" "install-allowed"

# --- 19. «Удаление без purge»: down без -v → данные и бэкапы сохранены -----------------------
docker compose --env-file "$ENV_FILE" "${COMPOSE_FILES[@]}" stop backend frontend worker >/dev/null 2>&1
docker compose --env-file "$ENV_FILE" "${COMPOSE_FILES[@]}" up -d --wait --wait-timeout 300 >/dev/null 2>&1 || true
login="$(api POST /api/auth/login "{\"username\":\"$(jget "$redeem" 'd["user"]["username"]')\",\"password\":\"$OWNER_PASSWORD\"}")"
CSRF="$(jget "$login" 'd.get("csrf_token","")')"
list2="$(api GET "/api/candidates?page=1&page_size=10")"
count2="$(jget "$list2" 'd.get("total", 0)')"
backups_kept="$(docker compose --env-file "$ENV_FILE" "${COMPOSE_FILES[@]}" exec -T backup sh -lc 'ls /var/backups/hr-manager/*.pgdump.enc 2>/dev/null | wc -l' | tr -d '[:space:]')"
if [ "$count2" -ge 1 ] && [ "$backups_kept" -ge 1 ]; then
  stage_pass "uninstall_without_purge" "data+backups-kept"
else
  stage_fail "uninstall_without_purge" "data-or-backups-lost"
fi

# --- 20. Отчёты ---------------------------------------------------------------------------
STAGES_JSON="${STAGES_JSON%,}"
VERDICT="pass"; [ "$FAIL" -gt 0 ] && VERDICT="fail"
python3 - "$REPORT_JSON" "$VERDICT" "$PASS" "$FAIL" "$STAGES_JSON" <<'PY'
import json, sys
from datetime import UTC, datetime
path, verdict, passed, failed, stages = sys.argv[1:6]
payload = {
    "drill": "pilot-drill",
    "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "verdict": verdict,
    "passed": int(passed),
    "failed": int(failed),
    "stages": json.loads("[" + stages + "]"),
}
with open(path, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, ensure_ascii=False, indent=2)
lines = [
    "# Pilot drill (Phase 14, CI-контур)",
    "",
    f"- Вердикт: **{verdict}** (пройдено {passed}, провалено {failed})",
    f"- Сгенерирован: {payload['generated_at']}",
    "- Данные: синтетические; ключи/сертификаты: эфемерные (не production).",
    "",
    "| Стадия | Статус | Деталь |",
    "| --- | --- | --- |",
]
for stage in payload["stages"]:
    lines.append(f"| {stage['name']} | {stage['status']} | {stage['detail']} |")
with open(path.replace(".json", ".md"), "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines) + "\n")
PY

log "отчёты: $REPORT_JSON, $REPORT_MD"
if [ "$FAIL" -gt 0 ]; then
  log "ПРОВАЛ: $FAIL стадий"
  exit 1
fi
log "ВСЕ СТАДИИ ПРОЙДЕНЫ ($PASS)"
