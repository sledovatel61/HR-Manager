#!/usr/bin/env bash
# Evidence-харнесс (Phase 14 rework, Агент 1): дословный replay docker-НЕзависимой
# стадии 4 pilot-drill.sh — «Публикация подписанного канала (fixture-ключ)».
#
# Контекст: независимая проверка нашла, что drill готовил ОДИН снимок с
# release.json version=0.14.0 и переиспользовал его для артефакта 0.15.0 —
# publish_channel.py корректно отвергал публикацию (bad_release_json), и drill
# умирал с exit 2 ещё ДО запуска Docker Compose. После фикса каждый релизный
# артефакт строится из СВОЕГО снимка (0.14.0 декларирует 0.14.0; 0.15.0 —
# 0.15.0), проверка publish_channel.py не ослаблена.
#
# Что делает харнесс:
#   1) извлекает из infra/scripts/pilot-drill.sh ДОСЛОВНО функции
#      make_snapshot/publish_channel и блок их вызова (стадия 4);
#   2) исполняет извлечённый код без изменений (Docker не нужен и не used);
#   3) проверяет артефакты: имя пакета ↔ release.json ВНУТРИ пакета ↔ manifest;
#      независимая Ed25519-проверка подписи публичным ключом клиента;
#      детерминированность сборки; подготовку повреждённой подписи (стадия 16);
#   4) негативный контроль: исходный сценарий бага (снимок 0.14.0 публикуется
#      как 0.15.0) ОБЯЗАН отвергаться (rc=2, bad_release_json) —
#      security-проверка не превратилась в pass/skip;
#   5) пишет stage4-replay.json + stage4-replay.md (без секретов/PII).
#
# Требования: bash, python3 (+cryptography), git (для blob-SHA drill).
# Запуск из любого каталога:
#
#   review-artifacts/evidence/2026-09-14-agent1/replay-stage4.sh
#
# Выход: 0 — все шаги pass; 1 — есть провалы; 2 — ошибка харнесса.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
DRILL="$REPO_ROOT/infra/scripts/pilot-drill.sh"
WORKDIR="$(mktemp -d /tmp/hrm-replay-stage4-XXXXXX)"
EVIDENCE_OUT="${HRM_EVIDENCE_OUT:-$SCRIPT_DIR}"
TESTDATA="$REPO_ROOT/infra/release/testdata"
CHANNEL_DIR="$WORKDIR/channel-srv"
CHANNEL_SHA="$(printf '2%.0s' $(seq 1 40))"
CHANNEL_VERSION="0.14.0"
NEXT_SHA="$(printf '4%.0s' $(seq 1 40))"
NEXT_VERSION="0.15.0"
PASS=0
FAIL=0
STEPS="$WORKDIR/steps.txt"
: > "$STEPS"

die() { printf '[replay] ОШИБКА: %s\n' "$*" >&2; exit 2; }
stage_pass() {
  PASS=$((PASS + 1))
  printf '%s|pass|%s\n' "$1" "$2" >> "$STEPS"
  printf '[replay] [PASS] %s (%s)\n' "$1" "$2"
}
stage_fail() {
  FAIL=$((FAIL + 1))
  printf '%s|fail|%s\n' "$1" "$2" >> "$STEPS"
  printf '[replay] [FAIL] %s (%s)\n' "$1" "$2"
}
cleanup() { rm -rf "$WORKDIR"; }
trap cleanup EXIT

[ -f "$DRILL" ] || die "drill не найден: $DRILL"
command -v python3 >/dev/null || die "python3 не найден"
mkdir -p "$CHANNEL_DIR"
cd "$REPO_ROOT"

# --- 1. Дословное извлечение стадии 4 (функции + блок вызова) --------------------
EXTRACTED="$WORKDIR/stage4-extracted.sh"
python3 - "$DRILL" "$EXTRACTED" <<'PY'
import sys
from pathlib import Path

src = Path(sys.argv[1]).read_text(encoding="utf-8")


def cut(start_marker: str, end_marker: str) -> str:
    i = src.index(start_marker)
    j = src.index(end_marker, i) + len(end_marker)
    return src[i:j]


parts = [
    # make_snapshot: от сигнатуры до конца функции с heredoc ('\nPY\n}\n').
    cut("make_snapshot() { # make_snapshot", "\nPY\n}\n"),
    # publish_channel: чистая bash-функция — до первого '\n}\n'.
    cut("publish_channel() { # publish_channel", "\n}\n"),
    # Блок вызовов стадии 4: от комментария до итогового stage_pass.
    cut("# Каждый релизный артефакт строится", 'stage_pass "channel_published" "fixture-ed25519"'),
]
Path(sys.argv[2]).write_text("\n\n".join(parts) + "\n", encoding="utf-8")
PY
stage_pass "stage4_extracted_verbatim" "make_snapshot+publish_channel+invocation"

# --- 2. Исполнение извлечённого кода drill (без модификаций) ---------------------
# shellcheck disable=SC1090
source "$EXTRACTED"

# --- 3. Проверки артефактов -------------------------------------------------------
VERIFY_RC=0
python3 - "$WORKDIR" "$CHANNEL_VERSION" "$CHANNEL_SHA" "$NEXT_VERSION" "$NEXT_SHA" \
  "$TESTDATA" "$REPO_ROOT" <<'PY' || VERIFY_RC=$?
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

workdir = Path(sys.argv[1])
ch_ver, ch_sha, nx_ver, nx_sha = sys.argv[2:6]
testdata = Path(sys.argv[6])
repo = Path(sys.argv[7])
steps_file = workdir / "steps.txt"
ok_all = True


def record(name: str, ok: bool, detail: str) -> None:
    global ok_all
    ok_all = ok_all and ok
    with steps_file.open("a", encoding="utf-8") as fh:
        fh.write(f"{name}|{'pass' if ok else 'fail'}|{detail}\n")
    print(f"[replay] [{'PASS' if ok else 'FAIL'}] {name} ({detail})")


for channel, version, sha in (
    ("channel-good", ch_ver, ch_sha),
    ("channel-next", nx_ver, nx_sha),
    ("channel-redirect", nx_ver, nx_sha),
):
    out = workdir / channel
    package = out / f"hr-manager-windows-{version}.zip"
    manifest_path = out / "update-channel.json"
    present = package.is_file() and manifest_path.is_file()
    record(f"artifacts_present_{channel}", present, package.name)
    if not present:
        continue
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    record(
        f"manifest_fields_{channel}",
        manifest["version"] == version and manifest["release_sha"] == sha,
        f"version={manifest['version']} sha={sha[:12]}",
    )
    with zipfile.ZipFile(package) as archive:
        inner = json.loads(archive.read("release.json"))
    record(
        f"inner_release_json_{channel}",
        inner["version"] == version and inner["release_sha"] == sha,
        f"release.json в пакете декларирует {inner['version']}",
    )
    verification = subprocess.run(
        [
            sys.executable,
            str(repo / "infra" / "release" / "verify_channel.py"),
            "--manifest",
            str(manifest_path),
            "--public-key",
            str(testdata / "test_key.pub"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    record(f"manifest_signature_{channel}", verification.returncode == 0, "ed25519-verified")

# Детерминированность: оба канала 0.15.0 собраны из одного снимка —
# байты пакета обязаны совпадать (закреплённая toolchain сборки zip).
nx_next = (workdir / "channel-next" / f"hr-manager-windows-{nx_ver}.zip").read_bytes()
nx_redirect = (workdir / "channel-redirect" / f"hr-manager-windows-{nx_ver}.zip").read_bytes()
record(
    "deterministic_package_0.15.0",
    hashlib.sha256(nx_next).hexdigest() == hashlib.sha256(nx_redirect).hexdigest(),
    "same-bytes",
)

# Повреждённая подпись подготовлена (вход стадии 16 drill).
tampered = workdir / "channel-tampered" / "update-channel.json"
good = workdir / "channel-good" / "update-channel.json"
tampered_ok = tampered.is_file()
if tampered_ok:
    tampered_sig = json.loads(tampered.read_text(encoding="utf-8"))["signature"]["sig"]
    good_sig = json.loads(good.read_text(encoding="utf-8"))["signature"]["sig"]
    tampered_ok = tampered_sig != good_sig
record("tampered_manifest_prepared", tampered_ok, "sig-flipped")

# Обслуживаемая директория канала наполнена валидным каналом.
record(
    "channel_dir_populated",
    (workdir / "channel-srv" / "update-channel.json").is_file()
    and (workdir / "channel-srv" / f"hr-manager-windows-{ch_ver}.zip").is_file(),
    "good-channel",
)

sys.exit(0 if ok_all else 1)
PY

# --- 4. Негативный контроль: исходный сценарий бага обязан отвергаться -----------
# Снимок 0.14.0 публикуется как 0.15.0 (ровно как в баге ревью): извлечённый
# publish_channel обязан умереть через die (rc=2), publish_channel.py — rc=1
# с bad_release_json; артефакты не создаются. Проверка НЕ ослаблена.
NEG_ERR="$WORKDIR/negative.stderr"
set +e
(
  publish_channel "$WORKDIR/snapshot-$CHANNEL_VERSION" "$NEXT_VERSION" "$NEXT_SHA" \
    "https://channel:8443/hr-manager-windows-$NEXT_VERSION.zip" \
    "$WORKDIR/channel-negative"
) 2>"$NEG_ERR"
NEG_RC=$?
set -e
if [ "$NEG_RC" -eq 2 ] \
   && grep -q "bad_release_json" "$NEG_ERR" \
   && grep -q "publish_channel failed" "$NEG_ERR" \
   && [ ! -e "$WORKDIR/channel-negative/hr-manager-windows-$NEXT_VERSION.zip" ]; then
  stage_pass "negative_mismatch_rejected" "rc=2 bad_release_json (сценарий бага ревью)"
else
  stage_fail "negative_mismatch_rejected" "rc=$NEG_RC (ожидался отказ 2 + bad_release_json)"
fi
if [ "$VERIFY_RC" -ne 0 ]; then
  stage_fail "artifact_checks" "verifier rc=$VERIFY_RC"
else
  stage_pass "artifact_checks" "all-verifications-passed"
fi

# --- 5. Evidence (JSON + MD) ------------------------------------------------------
mkdir -p "$EVIDENCE_OUT"
BASE_SHA="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
DRILL_BLOB="$(git -C "$REPO_ROOT" hash-object "$DRILL")"
python3 - "$STEPS" "$EVIDENCE_OUT/stage4-replay.json" "$EVIDENCE_OUT/stage4-replay.md" \
  "$BASE_SHA" "$DRILL_BLOB" <<'PY'
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

steps_path, json_out, md_out, base_sha, drill_blob = sys.argv[1:6]
stages = []
for line in Path(steps_path).read_text(encoding="utf-8").splitlines():
    if line.strip():
        name, status, detail = line.split("|", 2)
        stages.append({"name": name, "status": status, "detail": detail})
passed = sum(1 for s in stages if s["status"] == "pass")
failed = len(stages) - passed
verdict = "pass" if failed == 0 else "fail"
payload = {
    "harness": (
        "replay-stage4.sh — дословный replay стадии 4 pilot-drill.sh "
        "(публикация подписанного канала), без Docker"
    ),
    "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "generated_at_commit": base_sha,
    "drill_file_git_blob": drill_blob,
    "docker_used": False,
    "keys": "fixture (infra/release/testdata), NOT production",
    "stages_replayed": (
        "make_snapshot(0.14.0) + make_snapshot(0.15.0) + publish_channel x3 "
        "(good 0.14.0, next 0.15.0, redirect 0.15.0) + tampered-signature prepare"
    ),
    "negative_control": (
        "snapshot 0.14.0 published as 0.15.0 (исходный баг ревью) — обязан "
        "отвергнуться: rc=2, bad_release_json"
    ),
    "verdict": verdict,
    "passed": passed,
    "failed": failed,
    "stages": stages,
}
Path(json_out).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
lines = [
    "# Stage-4 replay: публикация подписанного канала (Phase 14 rework)",
    "",
    f"- Вердикт: **{verdict}** (pass {passed}, fail {failed})",
    f"- Сгенерирован: {payload['generated_at']} (commit {base_sha[:12]})",
    "- Код стадии извлечён из infra/scripts/pilot-drill.sh ДОСЛОВНО и исполнен без изменений.",
    "- Docker не использовался; ключи — fixture (infra/release/testdata), не production.",
    "",
    "| Шаг | Статус | Деталь |",
    "| --- | --- | --- |",
]
for stage in stages:
    lines.append(f"| {stage['name']} | {stage['status']} | {stage['detail']} |")
Path(md_out).write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"[replay] evidence: {json_out}")
sys.exit(0 if verdict == "pass" else 1)
PY
