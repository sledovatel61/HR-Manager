#!/usr/bin/env bash
# Phase 14: автоматизированный e2e pilot drill (CI-контур, Linux + Docker).
#
# Полный прогон серверного контракта пилота на ЖИВОМ Compose-стеке — только
# синтетические данные, эфемерные тестовые ключи/сертификаты и секреты,
# сгенерированные на месте. НИКАКИХ production secrets, ключей и сертификатов.
#
# Стек запускается в ИЗОЛИРОВАННОМ Compose-проекте с уникальным именем
# ($PROJECT_NAME, hrm-pilot-drill-<timestamp>-<pid>): тома/сети/контейнеры
# прогона никогда не пересекаются ни с реальным пилотом (hr-manager-pilot),
# ни с параллельным прогоном drill.
#
# Доказывает (CI-часть; Windows-специфика — ручная приёмка по
# docs/phase-14-runbook.md, production Authenticode — отдельный контур):
#   1. чистая установка + first-run (одноразовый exchange token → владелец);
#   2. readiness ОБЕИХ публичных точек: backend API (/api/health) и frontend
#      (SPA отдаётся, титул приложения) — polling с дедлайном;
#   3. синтетические данные через публичный контракт API + ОБЯЗАТЕЛЬНОЕ
#      чтение назад и сверка полей (не только счётчик);
#   4. зашифрованный бэкап + ФАКТИЧЕСКИЕ байты бэкапа (docker compose cp):
#      ненулевой размер и SHA-256, сверен со sidecar-контрольной суммой;
#   5. restore drill в отдельную изолированную БД + проверка, что
#      синтетическая запись ДЕЙСТВИТЕЛЬНО восстановлена (маркер по email);
#   6. предпусковая readiness-проверка (read-only, без секретов в ответе);
#   7. подписанный канал → check → download (staging, SHA256+размер против
#      manifest) → явный install → resume (повторная выдача команды);
#   8. сохранение синтетических данных после «обновления»;
#   9. tamper- suite — ОБЯЗАТЕЛЬНЫЕ отказы ДО изменения установки:
#      повреждённая подпись manifest, изменённое содержимое manifest при
#      старой подписи, redirect на запрещённый хост, redirect на
#      незащищённую схему (http), protocol-relative redirect (//evil),
#      traversal-URL (dot-segments в пути), подмена пакета (SHA256 не
#      совпал, размер тот же), повреждённый/обрезанный пакет (размер не
#      совпал);
#  10. restart сервисов (stop + up без -v) → синтетические данные читаются
#      снова И сверяются по полям, бэкап существует и НЕ ИЗМЕНИЛСЯ (SHA-256
#      до/после совпадает);
#  11. cleanup: down -v + проверка ОТСУТСТВИЯ остаточных контейнеров, томов
#      и сетей прогона (по label и по имени проекта).
#
# Вердикт и exit-код:
#   pass        — все обязательные стадии выполнены и пройдены (exit 0);
#   fail        — есть проваленные обязательные стадии (exit 1);
#   incomplete  — обязательные стадии не выполнялись (skip: нет docker,
#                 аномальный выход и т.п.) (exit 1).
# НИКАКАЯ обязательная стадия не считается пройденной по одному /health,
# наличию сервиса, имени файла или без проверки восстановленных данных.
# Отчёты pilot-drill.json / pilot-drill.md пишутся ВСЕГДА, включая провал и
# аномальный выход (trap EXIT) — без секретов, PII и URL с кредентами:
# только коды/счётчики/стадии/контрольные суммы.
#
# Требования: docker (Compose v2.24+), openssl, python3, curl, GNU
# coreutils (stat/truncate); jq НЕ нужен (JSON разбирается python3).
# Запуск из корня репозитория:
#
#   infra/scripts/pilot-drill.sh [--workdir /tmp/hrm-drill]

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORKDIR="${TMPDIR:-/tmp}/hrm-pilot-drill-$$"
REPORT_JSON="${REPORT_JSON:-$WORKDIR/pilot-drill.json}"
REPORT_MD="${REPORT_MD:-$WORKDIR/pilot-drill.md}"
BASE_URL="http://127.0.0.1:18080"
# Уникальное имя Compose-проекта прогона: изоляция томов/сетей/контейнеров
# от реального пилота и от параллельных прогонов. -p перекрывает `name:`
# в compose-файлах для КАЖДОЙ compose-команды (helper dc()).
PROJECT_NAME="hrm-pilot-drill-$(date -u +%Y%m%d%H%M%S)-$$"
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
# Синтетическая запись: уникальный email прогона — маркер, который обязан
# пережить backup → restore и restart (проверяется по полям, не по счётчику).
SYNTH_EMAIL="drill-synthetic-$(date -u +%Y%m%d%H%M%S)-$$@example.com"
SYNTH_NAME="Синтетический Кандидат Дрилла"
OWNER_PASSWORD=""
ENGINE_TOKEN=""
PASS=0
FAIL=0
SKIP=0
STAGES_JSON=""
FINISHED=0
CLEANUP_RECORDED=0
REPORTS_WRITTEN=0
VERDICT_OVERRIDE=""

# Обязательные стадии в порядке выполнения. При досрочном завершении
# невыполненные обязательные стадии честно помечаются skipped (вердикт
# incomplete), а не pass.
MANDATORY_PENDING=(
  prerequisites
  ephemeral_secrets
  tls_certificates
  channel_published
  compose_config
  stack_up
  backend_ready
  frontend_ready
  first_run_claim
  first_run_redeem
  synthetic_data_created
  synthetic_data_verified
  encrypted_backup
  backup_bytes_verified
  restore_drill
  backup_health_api
  readiness_report
  channel_check
  channel_download
  install_requested
  engine_resume_delivery
  update_completed
  data_preserved_after_update
  tampered_signature_rejected
  tampered_manifest_rejected
  forbidden_redirect_rejected
  unsafe_scheme_redirect_rejected
  protocol_relative_redirect_rejected
  traversal_url_rejected
  corrupted_package_rejected
  truncated_package_rejected
  install_blocked_after_tamper
  services_restarted
  synthetic_data_after_restart
  backup_unchanged_after_restart
  cleanup_down_v
  no_residual_resources
)

log() { printf '[drill] %s\n' "$*"; }

# --- JSON-помощники (без jq) ----------------------------------------------------
jget() { # jget <json> <python-expr over d> — fail closed: '' при любом отказе
  local json="$1" expr="$2"
  printf '%s' "$json" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    d = {}
try:
    print($expr)
except Exception:
    print('')
"
}

api() { # api <method> <path> [json-body] [extra-curl-args...]
  local method="$1"; shift
  local path="$1"; shift
  local body=""
  if [ $# -gt 0 ] && [ "${1:0:1}" != "-" ]; then body="$1"; shift; fi
  local args=(-sS -X "$method" "$BASE_URL$path" -c "$COOKIE_JAR" -b "$COOKIE_JAR"
    -H 'Content-Type: application/json' --max-time 60)
  [ -n "$body" ] && args+=(-d "$body")
  curl "${args[@]}" "$@" || true
}

dc() { # каждая compose-команда — с уникальным проектом прогона
  docker compose -p "$PROJECT_NAME" --env-file "$ENV_FILE" "${COMPOSE_FILES[@]}" "$@"
}

# --- Счётчики стадий -------------------------------------------------------------
_record() { # удалить выполненную стадию из списка ожидаемых
  local name="$1" item
  local rest=()
  for item in ${MANDATORY_PENDING[@]+"${MANDATORY_PENDING[@]}"}; do
    [ "$item" = "$name" ] || rest+=("$item")
  done
  MANDATORY_PENDING=(${rest[@]+"${rest[@]}"})
}

stage_pass() {
  PASS=$((PASS + 1))
  STAGES_JSON+="{\"name\":\"$1\",\"status\":\"pass\",\"detail\":\"$2\"},"
  _record "$1"; log "  [PASS] $1 ($2)"
}
stage_fail() {
  FAIL=$((FAIL + 1))
  STAGES_JSON+="{\"name\":\"$1\",\"status\":\"fail\",\"detail\":\"$2\"},"
  _record "$1"; log "  [FAIL] $1 ($2)"
}
stage_skip() {
  SKIP=$((SKIP + 1))
  STAGES_JSON+="{\"name\":\"$1\",\"status\":\"skip\",\"detail\":\"$2\"},"
  _record "$1"; log "  [SKIP] $1 ($2)"
}

# --- Evidence --------------------------------------------------------------------
write_reports() { # write_reports [verdict-override]
  local verdict="${1:-}"
  if [ -z "$verdict" ]; then
    if [ "$FAIL" -gt 0 ]; then verdict="fail"
    elif [ "$SKIP" -gt 0 ]; then verdict="incomplete"
    else verdict="pass"; fi
  fi
  # Явный override (например, недоступен docker — обязательные стадии не
  # выполнялись) имеет приоритет: вердикт не может стать pass.
  [ -n "$VERDICT_OVERRIDE" ] && verdict="$VERDICT_OVERRIDE"
  local stages="${STAGES_JSON%,}"
  python3 - "$REPORT_JSON" "$verdict" "$PASS" "$FAIL" "$SKIP" "$stages" "$PROJECT_NAME" <<'PY'
import json
import sys
from datetime import UTC, datetime

path, verdict, passed, failed, skipped, stages, project = sys.argv[1:8]
try:
    stage_list = json.loads("[" + stages + "]") if stages else []
except json.JSONDecodeError:
    stage_list = []
    verdict = "incomplete"
payload = {
    "drill": "pilot-drill",
    "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "compose_project": project,
    "verdict": verdict,
    "passed": int(passed),
    "failed": int(failed),
    "skipped": int(skipped),
    "stages": stage_list,
}
with open(path, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, ensure_ascii=False, indent=2)
lines = [
    "# Pilot drill (Phase 14, live Compose E2E)",
    "",
    f"- Вердикт: **{verdict}** (пройдено {passed}, провалено {failed}, пропущено {skipped})",
    f"- Compose-проект: `{project}`",
    f"- Сгенерирован: {payload['generated_at']}",
    "- Данные: синтетические; ключи/сертификаты: эфемерные (не production).",
    "",
    "| Стадия | Статус | Деталь |",
    "| --- | --- | --- |",
]
for stage in stage_list:
    lines.append(f"| {stage['name']} | {stage['status']} | {stage['detail']} |")
with open(path.replace(".json", ".md"), "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines) + "\n")
PY
  REPORTS_WRITTEN=1
}

_count_lines() { printf '%s' "$1" | grep -c . || true; }

# --- Cleanup + остаточные ресурсы (обязательные стадии) ---------------------------
_record_cleanup_stages() { # down -v + проверка отсутствия остатков
  CLEANUP_RECORDED=1
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1 \
     && [ -f "$ENV_FILE" ]; then
    if dc down -v --remove-orphans >/dev/null 2>&1; then
      stage_pass "cleanup_down_v" "down-v-remove-orphans"
    else
      stage_fail "cleanup_down_v" "down-v-failed"
    fi
    local c_list="" v_list="" n_list="" by_name="" containers=0 volumes=0 networks=0
    if ! c_list="$(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT_NAME" 2>/dev/null)"; then
      stage_fail "no_residual_resources" "docker-ps-failed"
    elif ! v_list="$(docker volume ls -q --filter "label=com.docker.compose.project=$PROJECT_NAME" 2>/dev/null)"; then
      stage_fail "no_residual_resources" "docker-volume-ls-failed"
    elif ! n_list="$(docker network ls -q --filter "label=com.docker.compose.project=$PROJECT_NAME" 2>/dev/null)"; then
      stage_fail "no_residual_resources" "docker-network-ls-failed"
    else
      by_name="$({ docker ps -a --format '{{.Names}}' 2>/dev/null || true
                   docker volume ls -q 2>/dev/null || true
                   docker network ls --format '{{.Name}}' 2>/dev/null || true
                  } | grep -c "^${PROJECT_NAME}[-_]" || true)"
      containers="$(_count_lines "$c_list")"
      volumes="$(_count_lines "$v_list")"
      networks="$(_count_lines "$n_list")"
      if [ "$containers" -eq 0 ] && [ "$volumes" -eq 0 ] && [ "$networks" -eq 0 ] \
         && [ "$by_name" -eq 0 ]; then
        stage_pass "no_residual_resources" "containers=0 volumes=0 networks=0"
      else
        stage_fail "no_residual_resources" \
          "containers=$containers volumes=$volumes networks=$networks by-name=$by_name"
      fi
    fi
  else
    # Невозможно выполнить (нет docker) — честный skip, вердикт не станет pass.
    stage_skip "cleanup_down_v" "docker-unavailable"
    stage_skip "no_residual_resources" "docker-unavailable"
  fi
}

finish_drill() { # единственный нормальный выход: cleanup + evidence + exit
  [ "$FINISHED" -eq 1 ] && return 0
  FINISHED=1
  _record_cleanup_stages
  if [ "${#MANDATORY_PENDING[@]}" -gt 0 ]; then
    local name
    for name in ${MANDATORY_PENDING[@]+"${MANDATORY_PENDING[@]}"}; do
      stage_skip "$name" "not-reached"
    done
  fi
  write_reports ""
  if [ "$FAIL" -gt 0 ] || [ "$SKIP" -gt 0 ]; then
    log "ДРИЛЛ НЕ ПРОЙДЕН: passed=$PASS failed=$FAIL skipped=$SKIP (детали: $REPORT_JSON)"
    exit 1
  fi
  log "ВСЕ ОБЯЗАТЕЛЬНЫЕ СТАДИИ ПРОЙДЕНЫ ($PASS)"
  exit 0
}

bail() { # bail <stage> <detail>: фатальный провал → cleanup+evidence+exit 1
  stage_fail "$1" "$2"
  finish_drill
}

on_exit() { # trap EXIT: evidence обязателен даже при аномальном выходе
  local code=$?
  trap - EXIT
  if [ "$REPORTS_WRITTEN" -eq 0 ]; then
    FINISHED=1
    if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1 \
       && [ -f "$ENV_FILE" ]; then
      dc down -v --remove-orphans >/dev/null 2>&1 || true
    fi
    [ "$CLEANUP_RECORDED" -eq 1 ] || _record_cleanup_stages
    if [ "${#MANDATORY_PENDING[@]}" -gt 0 ]; then
      local name
      for name in ${MANDATORY_PENDING[@]+"${MANDATORY_PENDING[@]}"}; do
        stage_skip "$name" "not-reached"
      done
    fi
    write_reports "incomplete"
  fi
  # Секреты и ключи прогона не переживают прогон.
  rm -rf "$CERTS_DIR" "$WORKDIR/pilot.env" "$WORKDIR"/snapshot* "$COOKIE_JAR" 2>/dev/null || true
  exit "$code"
}
trap on_exit EXIT

# --- 1. Предусловия ---------------------------------------------------------------
command -v docker >/dev/null 2>&1 || { stage_fail "prerequisites" "docker-not-found"; VERDICT_OVERRIDE="incomplete"; finish_drill; }
command -v openssl >/dev/null 2>&1 || { stage_fail "prerequisites" "openssl-not-found"; VERDICT_OVERRIDE="incomplete"; finish_drill; }
command -v python3 >/dev/null 2>&1 || { stage_fail "prerequisites" "python3-not-found"; VERDICT_OVERRIDE="incomplete"; finish_drill; }
command -v curl >/dev/null 2>&1 || { stage_fail "prerequisites" "curl-not-found"; VERDICT_OVERRIDE="incomplete"; finish_drill; }
docker compose version >/dev/null 2>&1 || { stage_fail "prerequisites" "docker-compose-v2-unavailable"; VERDICT_OVERRIDE="incomplete"; finish_drill; }
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
  -addext "subjectAltName=DNS:channel" >/dev/null 2>&1 || bail "tls_certificates" "openssl-failed"
cp "$CERTS_DIR/channel.crt" "$CERTS_DIR/drill-ca.crt"
stage_pass "tls_certificates" "ephemeral-self-signed"

# --- 4. Публикация подписанного канала (fixture-ключ, НЕ production) ---------------
make_snapshot() { # make_snapshot <dir> <version> <release-sha>
  # Снимок ОБЯЗАН декларировать СВОИ версию и sha в release.json: артефакт
  # 0.14.0 — 0.14.0, артефакт 0.15.0 — 0.15.0. publish_channel.py отвергает
  # несогласованный снимок (bad_release_json) — этот барьер не ослабляется.
  local dir="$1" version="$2" sha="$3"
  rm -rf "$dir"; mkdir -p "$dir"
  cp -R backend "$dir/backend"
  cp -R frontend "$dir/frontend"
  cp -R infra "$dir/infra"
  # Локальные артефакты разработки не входят в снимок релиза.
  rm -rf "$dir/frontend/node_modules" "$dir/frontend/dist" \
    "$dir/infra/release/testdata/__pycache__"
  python3 - "$dir/release.json" "$version" "$sha" <<'PY'
import json, sys
from datetime import UTC, datetime
json.dump({
    "version": sys.argv[2],
    "release_sha": sys.argv[3],
    "built_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
}, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False)
PY
}

publish_channel() { # publish_channel <snapshot> <version> <sha> <package-url> <out-dir>
  local snapshot="$1" version="$2" sha="$3" url="$4" out="$5"
  python3 infra/release/publish_channel.py \
    --snapshot "$snapshot" --version "$version" \
    --release-sha "$sha" --package-url "$url" \
    --minimum-supported-version 0.13.0 \
    --notes-ru "Pilot drill (fixture key, NOT production)" \
    --private-key "$TESTDATA/test_key.priv" --key-id pilot-test-key \
    --public-keys-json "$TESTDATA/trusted_keys.json" \
    --out-dir "$out" >/dev/null || bail "channel_published" "publish_channel-failed-for-$version"
}

# Каждый релизный артефакт строится из СВОЕГО снимка: release.json внутри
# пакета декларирует именно его версию и sha, имя пакета — его версию.
# (Регрессия: ранее канал 0.15.0 переиспользовал снимок 0.14.0, и публикация
# падала с bad_release_json — drill умирал с exit 2 ещё до Docker Compose.)
make_snapshot "$WORKDIR/snapshot-$CHANNEL_VERSION" "$CHANNEL_VERSION" "$CHANNEL_SHA"
make_snapshot "$WORKDIR/snapshot-$NEXT_VERSION" "$NEXT_VERSION" "$NEXT_SHA"
publish_channel "$WORKDIR/snapshot-$CHANNEL_VERSION" "$CHANNEL_VERSION" "$CHANNEL_SHA" \
  "https://channel:8443/hr-manager-windows-$CHANNEL_VERSION.zip" "$WORKDIR/channel-good"
publish_channel "$WORKDIR/snapshot-$NEXT_VERSION" "$NEXT_VERSION" "$NEXT_SHA" \
  "https://channel:8443/hr-manager-windows-$NEXT_VERSION.zip" "$WORKDIR/channel-next"
publish_channel "$WORKDIR/snapshot-$NEXT_VERSION" "$NEXT_VERSION" "$NEXT_SHA" \
  "https://channel:8443/redirect/pkg.zip" "$WORKDIR/channel-redirect"
# Traversal-URL: dot-segments в пути подписанного manifest — клиент обязан
# отвергнуть его ДО сетевого обращения (политика каждого hop).
publish_channel "$WORKDIR/snapshot-$NEXT_VERSION" "$NEXT_VERSION" "$NEXT_SHA" \
  "https://channel:8443/../../../etc/hostname" "$WORKDIR/channel-traversal"
# Повреждённая подпись: инвертируем первый hex-символ подписи.
python3 - "$WORKDIR/channel-next/update-channel.json" "$WORKDIR/channel-tampered/update-channel.json" <<'PY'
import json, sys, pathlib
src = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
sig = src["signature"]["sig"]
src["signature"]["sig"] = ("0" if sig[0] != "0" else "1") + sig[1:]
out = pathlib.Path(sys.argv[2]); out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(src, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
# Изменённое содержимое manifest при СТАРОЙ подписи (подмена полей без
# переподписания) — верификация подписи обязана провалиться.
python3 - "$WORKDIR/channel-next/update-channel.json" "$WORKDIR/channel-manifest-tampered/update-channel.json" <<'PY'
import json, sys, pathlib
src = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
src["version"] = "0.16.0"
src["notes_ru"] = "tampered content, old signature"
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
if dc config -q; then
  stage_pass "compose_config" "render-ok"
else
  bail "compose_config" "render-failed"
fi
if dc up -d --build --wait --wait-timeout 600 >/dev/null 2>&1; then
  stage_pass "stack_up" "healthy"
else
  dc ps || true
  bail "stack_up" "unhealthy"
fi

# --- 6. Readiness: backend И frontend (polling с дедлайном, не один /health) -------
backend_ok=""
deadline=$((SECONDS + 300))
while [ "$SECONDS" -lt "$deadline" ]; do
  health="$(api GET /api/health)"
  [ "$(jget "$health" 'd.get("status","")')" = "ok" ] && { backend_ok=1; break; }
  sleep 3
done
if [ -n "$backend_ok" ]; then
  stage_pass "backend_ready" "api-health-status-ok"
else
  bail "backend_ready" "no-ok-status-within-300s"
fi

frontend_ok=""
deadline=$((SECONDS + 300))
while [ "$SECONDS" -lt "$deadline" ]; do
  page="$(curl -sS --max-time 10 "$BASE_URL/" 2>/dev/null || true)"
  if printf '%s' "$page" | grep -q '<title>HR Manager</title>'; then frontend_ok=1; break; fi
  sleep 3
done
if [ -n "$frontend_ok" ]; then
  stage_pass "frontend_ready" "spa-served-title-ok"
else
  bail "frontend_ready" "spa-not-served-within-300s"
fi

# --- 7. First-run: exchange token → билет → владелец ---------------------------------
claim="$(api POST /api/setup/owner/claim "{\"exchange_token\":\"$EXCHANGE_TOKEN\",\"surname\":\"Дриллов\",\"working_mode\":\"admin\",\"timezone\":\"Europe/Moscow\"}")"
ticket="$(jget "$claim" 'd.get("ticket","")')"
[ -n "$ticket" ] && stage_pass "first_run_claim" "ticket-issued" || bail "first_run_claim" "no-ticket"
redeem="$(api POST /api/setup/owner/redeem "{\"ticket\":\"$ticket\",\"timezone\":\"Europe/Moscow\",\"workdays\":[1,2,3,4,5],\"quiet_hours_start\":\"22:00\",\"quiet_hours_end\":\"08:00\",\"password\":\"$OWNER_PASSWORD\"}")"
CSRF="$(jget "$redeem" 'd.get("csrf_token","")')"
[ -n "$CSRF" ] && stage_pass "first_run_redeem" "owner-session" || bail "first_run_redeem" "no-session"

# --- 8. Синтетические данные через публичный контракт + чтение назад ------------------
cand="$(api POST /api/candidates "{\"full_name\":\"$SYNTH_NAME\",\"email\":\"$SYNTH_EMAIL\",\"source\":\"site\",\"position\":\"Синтетическая позиция\"}" -H "X-CSRF-Token: $CSRF")"
cand_id="$(jget "$cand" 'd.get("id","")')"
[ -n "$cand_id" ] && stage_pass "synthetic_data_created" "candidate-via-api" || bail "synthetic_data_created" "api-rejected"
readback="$(api GET "/api/candidates/$cand_id")"
if [ "$(jget "$readback" 'd.get("email","")')" = "$SYNTH_EMAIL" ] \
   && [ "$(jget "$readback" 'd.get("full_name","")')" = "$SYNTH_NAME" ]; then
  stage_pass "synthetic_data_verified" "fields-match"
else
  bail "synthetic_data_verified" "readback-mismatch"
fi

# --- 9. Зашифрованный бэкап + ФАКТИЧЕСКИЕ байты + restore drill -----------------------
if dc run --rm backup python -m app.cli backup-now --reason "pilot drill" --as-scheduler >/dev/null 2>&1; then
  stage_pass "encrypted_backup" "pgdump-enc"
else
  bail "encrypted_backup" "backup-failed"
fi
# Реальные байты бэкапа: забираем из тома контейнера и сверяем.
BACKUP_PATH_IN_CONTAINER=""
BACKUP_PATH_IN_CONTAINER="$(dc exec -T backup sh -lc 'ls -t /var/backups/hr-manager/*.pgdump.enc 2>/dev/null | head -1' | tr -d '[:space:]' || true)"
BACKUP_SIZE=0
BACKUP_SHA256=""
if [ -n "$BACKUP_PATH_IN_CONTAINER" ] \
   && dc cp "backup:$BACKUP_PATH_IN_CONTAINER" "$WORKDIR/backup-downloaded.enc" >/dev/null 2>&1; then
  BACKUP_SIZE="$(stat -c %s "$WORKDIR/backup-downloaded.enc")"
  BACKUP_SHA256="$(sha256sum "$WORKDIR/backup-downloaded.enc" | cut -d' ' -f1)"
  # Sidecar-контрольная сумма рядом с бэкапом обязана совпасть с байтами.
  SIDECAR_SHA=""
  if dc cp "backup:$BACKUP_PATH_IN_CONTAINER.sha256" "$WORKDIR/backup-downloaded.sha256" >/dev/null 2>&1; then
    SIDECAR_SHA="$(awk 'NR==1{print $1}' "$WORKDIR/backup-downloaded.sha256")"
  fi
  if [ "$BACKUP_SIZE" -gt 0 ] && [ -n "$BACKUP_SHA256" ] && [ "$BACKUP_SHA256" = "$SIDECAR_SHA" ]; then
    stage_pass "backup_bytes_verified" "size=$BACKUP_SIZE sha256-verified"
  else
    bail "backup_bytes_verified" "size=$BACKUP_SIZE sidecar-match=$([ "$BACKUP_SHA256" = "$SIDECAR_SHA" ] && echo yes || echo no)"
  fi
else
  bail "backup_bytes_verified" "bytes-not-fetchable"
fi
# Restore в изолированную БД: drill обязан найти в восстановленной базе
# синтетическую запись прогона (маркер по email), а не только схему/пользователей.
if dc run --rm -e BACKUP_DRILL_EXPECT_CANDIDATE_EMAIL="$SYNTH_EMAIL" backup \
     python -m app.cli backup-drill --as-scheduler >/dev/null 2>&1; then
  stage_pass "restore_drill" "isolated-db+synthetic-verified"
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
  stage_pass "channel_check" "available-$CHANNEL_VERSION"
else
  stage_fail "channel_check" "$(jget "$check" 'd.get("error_code", d.get("state","?"))')"
fi

# --- 12. Download → staging + сверка SHA256 И размера с manifest ------------------------
download="$(api POST /api/updates/download -H "X-CSRF-Token: $CSRF")"
if [ "$(jget "$download" 'd.get("state","")')" = "ready" ]; then
  expected_sha="$(python3 -c "
import json
m = json.load(open('$WORKDIR/channel-good/update-channel.json'))
print(m['package_sha256'])")"
  expected_size="$(python3 -c "
import json
m = json.load(open('$WORKDIR/channel-good/update-channel.json'))
print(m['package_size'])")"
  # Имя staging-файла клиент строит по ПЕРВЫМ 12 символам release_sha
  # (app.channel.download_package) — не по полному sha.
  staged="$STAGING_DIR/release-${CHANNEL_SHA:0:12}.zip"
  actual_sha="$(sha256sum "$staged" | cut -d' ' -f1)"
  actual_size="$(stat -c %s "$staged")"
  if [ "$expected_sha" = "$actual_sha" ] && [ "$expected_size" = "$actual_size" ]; then
    stage_pass "channel_download" "staging-sha+size-verified"
  else
    stage_fail "channel_download" "sha-or-size-mismatch"
  fi
else
  stage_fail "channel_download" "$(jget "$download" 'd.get("error_code","?")')"
fi

# --- 13. Явная установка + resume (повторная выдача команды) ----------------------------
install="$(api POST /api/updates/install -H "X-CSRF-Token: $CSRF")"
job_id="$(jget "$install" 'd.get("job_id","")')"
[ -n "$job_id" ] && stage_pass "install_requested" "job-queued" || stage_fail "install_requested" "$(jget "$install" 'd.get("message","?")')"

engine_headers=(-H "X-Engine-Token: $ENGINE_TOKEN" -H "X-Installed-Version: 0.13.0" -H "X-Installed-Sha: $DRILL_INSTALLED_SHA")
poll1="$(curl -sS "${engine_headers[@]}" "$BASE_URL/api/updates/engine-state" || true)"
poll2="$(curl -sS "${engine_headers[@]}" "$BASE_URL/api/updates/engine-state" || true)"
if [ "$(jget "$poll1" '",".join(d.get("actions",[]))')" = "install" ] \
   && [ "$(jget "$poll2" 'd.get("job_id","")')" = "$job_id" ]; then
  stage_pass "engine_resume_delivery" "same-job-redelivered"
else
  stage_fail "engine_resume_delivery" "no-install-action"
fi

# --- 14. Отчёт движка: installed → статус up_to_date -------------------------------------
report="$(curl -sS -X POST "$BASE_URL/api/updates/engine-report" \
  -H "X-Engine-Token: $ENGINE_TOKEN" -H 'Content-Type: application/json' \
  -d "{\"state\":\"installed\",\"installed_version\":\"$CHANNEL_VERSION\",\"installed_release_sha\":\"$CHANNEL_SHA\",\"job_id\":\"$job_id\",\"error_code\":null,\"error_detail\":null}" || true)"
status="$(api GET /api/updates/status)"
if [ "$(jget "$status" 'd.get("state","")')" = "up_to_date" ] && [ "$(jget "$status" 'd.get("installed_version","")')" = "$CHANNEL_VERSION" ]; then
  stage_pass "update_completed" "$CHANNEL_VERSION-installed"
else
  stage_fail "update_completed" "$(jget "$status" 'd.get("state","?")')"
fi

# --- 15. Данные сохранены после обновления (чтение по id, сверка полей) -------------------
preserved="$(api GET "/api/candidates/$cand_id")"
if [ "$(jget "$preserved" 'd.get("email","")')" = "$SYNTH_EMAIL" ] \
   && [ "$(jget "$preserved" 'd.get("full_name","")')" = "$SYNTH_NAME" ]; then
  stage_pass "data_preserved_after_update" "synthetic-row-intact"
else
  stage_fail "data_preserved_after_update" "data-lost"
fi

# --- 16. Tamper suite: повреждённая ПОДПИСЬ manifest → отказ ДО установки ----------------
cp "$WORKDIR/channel-tampered/update-channel.json" "$CHANNEL_DIR/update-channel.json"
check_bad="$(api POST /api/updates/check -H "X-CSRF-Token: $CSRF")"
if [ "$(jget "$check_bad" 'd.get("error_code","")')" = "manifest_bad_signature" ]; then
  stage_pass "tampered_signature_rejected" "manifest_bad_signature"
else
  stage_fail "tampered_signature_rejected" "$(jget "$check_bad" 'd.get("error_code", d.get("state","?"))')"
fi

# --- 17. Tamper suite: изменённое СОДЕРЖИМОЕ manifest при старой подписи → отказ ----------
cp "$WORKDIR/channel-manifest-tampered/update-channel.json" "$CHANNEL_DIR/update-channel.json"
check_tampered_manifest="$(api POST /api/updates/check -H "X-CSRF-Token: $CSRF")"
if [ "$(jget "$check_tampered_manifest" 'd.get("error_code","")')" = "manifest_bad_signature" ]; then
  stage_pass "tampered_manifest_rejected" "content-modified-sig-stale"
else
  stage_fail "tampered_manifest_rejected" "$(jget "$check_tampered_manifest" 'd.get("error_code", d.get("state","?"))')"
fi

# --- 18. Redirect-стадии: каждый хоп проверяется ДО обращения ----------------------------
redirect_case() { # redirect_case <имя-стадии> <target-url> <detail>
  local stage="$1" target="$2" detail="$3"
  printf '%s' "$target" > "$CHANNEL_DIR/redirect-target.txt"
  cp "$WORKDIR/channel-redirect/update-channel.json" "$CHANNEL_DIR/update-channel.json"
  local check_dl dl
  check_dl="$(api POST /api/updates/check -H "X-CSRF-Token: $CSRF")"
  dl="$(api POST /api/updates/download -H "X-CSRF-Token: $CSRF")"
  if [ "$(jget "$check_dl" 'd.get("state","")')" = "available" ] \
     && [ "$(jget "$dl" 'd.get("error_code","")')" = "bad_url" ]; then
    stage_pass "$stage" "$detail"
  else
    stage_fail "$stage" "$(jget "$dl" 'd.get("error_code", d.get("state","?"))')"
  fi
}
redirect_case "forbidden_redirect_rejected" \
  "https://evil.example.com/hr-manager-windows-$NEXT_VERSION.zip" "bad_url-before-request"
redirect_case "unsafe_scheme_redirect_rejected" \
  "http://evil.example.com/hr-manager-windows-$NEXT_VERSION.zip" "bad_url-scheme-http"
redirect_case "protocol_relative_redirect_rejected" \
  "//evil.example.com/hr-manager-windows-$NEXT_VERSION.zip" "bad_url-protocol-relative"

# --- 19. Traversal-URL в подписанном manifest → отказ до обращения ------------------------
cp "$WORKDIR/channel-traversal/update-channel.json" "$CHANNEL_DIR/update-channel.json"
check_trav="$(api POST /api/updates/check -H "X-CSRF-Token: $CSRF")"
dl_trav="$(api POST /api/updates/download -H "X-CSRF-Token: $CSRF")"
if [ "$(jget "$check_trav" 'd.get("error_code","")')" = "bad_url" ]; then
  stage_pass "traversal_url_rejected" "bad_url-at-check"
elif [ "$(jget "$check_trav" 'd.get("state","")')" = "available" ] \
     && [ "$(jget "$dl_trav" 'd.get("error_code","")')" = "bad_url" ]; then
  stage_pass "traversal_url_rejected" "bad_url-before-request"
else
  stage_fail "traversal_url_rejected" "$(jget "$dl_trav" 'd.get("error_code", d.get("state","?"))')"
fi

# --- 20. Подмена пакета: байт перевёрнут (размер тот же) → SHA256 не совпал ---------------
cp "$WORKDIR/channel-next/update-channel.json" "$CHANNEL_DIR/update-channel.json"
cp "$WORKDIR/channel-next/hr-manager-windows-$NEXT_VERSION.zip" "$CHANNEL_DIR/"
python3 - "$CHANNEL_DIR/hr-manager-windows-$NEXT_VERSION.zip" <<'PY'
import sys, pathlib
p = pathlib.Path(sys.argv[1])
blob = bytearray(p.read_bytes())
blob[len(blob) // 2] ^= 0xFF
p.write_bytes(bytes(blob))
PY
check_corrupt="$(api POST /api/updates/check -H "X-CSRF-Token: $CSRF")"
dl_corrupt="$(api POST /api/updates/download -H "X-CSRF-Token: $CSRF")"
if [ "$(jget "$dl_corrupt" 'd.get("error_code","")')" = "package_hash_mismatch" ]; then
  stage_pass "corrupted_package_rejected" "package_hash_mismatch"
else
  stage_fail "corrupted_package_rejected" "$(jget "$dl_corrupt" 'd.get("error_code", d.get("state","?"))')"
fi

# --- 21. Повреждённый/обрезанный пакет: размер не совпал с manifest -----------------------
truncate -s -64 "$CHANNEL_DIR/hr-manager-windows-$NEXT_VERSION.zip"
check_trunc="$(api POST /api/updates/check -H "X-CSRF-Token: $CSRF")"
dl_trunc="$(api POST /api/updates/download -H "X-CSRF-Token: $CSRF")"
if [ "$(jget "$dl_trunc" 'd.get("error_code","")')" = "download_failed" ]; then
  stage_pass "truncated_package_rejected" "size-mismatch-download_failed"
else
  stage_fail "truncated_package_rejected" "$(jget "$dl_trunc" 'd.get("error_code", d.get("state","?"))')"
fi

# --- 22. Установка невозможна после tamper-отказов ----------------------------------------
install_blocked="$(api POST /api/updates/install -H "X-CSRF-Token: $CSRF")"
[ "$(jget "$install_blocked" 'd.get("state","")')" != "installing" ] && stage_pass "install_blocked_after_tamper" "no-install" || stage_fail "install_blocked_after_tamper" "install-allowed"

# --- 23. Restart сервисов (без -v) → синтетические данные и бэкап на месте ----------------
dc stop backend frontend worker >/dev/null 2>&1 || true
if dc up -d --wait --wait-timeout 300 >/dev/null 2>&1; then
  stage_pass "services_restarted" "stop+up-wait-ok"
else
  stage_fail "services_restarted" "restart-failed"
fi
login="$(api POST /api/auth/login "{\"username\":\"$(jget "$redeem" 'd["user"]["username"]')\",\"password\":\"$OWNER_PASSWORD\"}")"
CSRF="$(jget "$login" 'd.get("csrf_token","")')"
after_restart="$(api GET "/api/candidates/$cand_id")"
if [ -n "$CSRF" ] \
   && [ "$(jget "$after_restart" 'd.get("email","")')" = "$SYNTH_EMAIL" ] \
   && [ "$(jget "$after_restart" 'd.get("full_name","")')" = "$SYNTH_NAME" ]; then
  stage_pass "synthetic_data_after_restart" "fields-match"
else
  stage_fail "synthetic_data_after_restart" "data-lost-or-login-failed"
fi
# Бэкап существует и НЕ изменился: SHA-256 в контейнере против загруженных байтов.
sha_after_restart="$(dc exec -T backup sh -lc "sha256sum '$BACKUP_PATH_IN_CONTAINER' 2>/dev/null" | awk 'NR==1{print $1}')"
if [ -n "$sha_after_restart" ] && [ "$sha_after_restart" = "$BACKUP_SHA256" ]; then
  stage_pass "backup_unchanged_after_restart" "sha256-same"
else
  stage_fail "backup_unchanged_after_restart" "sha-changed-or-missing"
fi

# --- 24. Финал: cleanup (down -v) + отсутствие остатков + evidence --------------------------
finish_drill
