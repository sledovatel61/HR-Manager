#!/usr/bin/env python3
"""Phase 14: автоматизированный end-to-end pilot drill.

Скрипт НЕ подменяет приёмку поиском строк: каждый шаг запускает реальные
компоненты проекта (release-политику на ephemeral сертификате, сетевую
политику канала с отказами на подделках, readiness API и — где доступно —
Windows-движок и PostgreSQL-интеграцию) и агрегирует честный результат.

Принципы:

* только синтетические данные и ephemeral test keys/certificate;
* production secrets не читаются и не печатаются; явно переданный DSN
  маскируется в выводе;
* шаг, который невозможно выполнить в текущем контуре, помечается
  ``skipped`` с причиной — никогда не ``passed``;
* fail → non-zero exit code + машиночитаемый JSON + краткий Markdown.

Примеры:

    python infra/scripts/pilot_drill.py --out-dir drill
    python infra/scripts/pilot_drill.py --steps windows-engine --out-dir drill
    python infra/scripts/pilot_drill.py --database-url "$POSTGRES_DSN" --out-dir drill
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

DRILL_SCHEMA = 1
SECRET_MARKERS = ("PRIVATE KEY-----", "BEGIN RSA PRIVATE KEY", "BEGIN OPENSSH PRIVATE KEY")

PYTEST_SUMMARY_RE = re.compile(r"^(?P<count>\d+) (?P<kind>passed|failed|skipped|error)", re.MULTILINE)


@dataclass
class Step:
    """Одно проверяемое звено drill."""

    id: str
    title: str
    kind: str  # pytest | powershell
    targets: list[str] = field(default_factory=list)
    marker: str = ""  # pytest -m
    requires: str = ""  # человекочитаемое требование контура


@dataclass
class StepResult:
    step: Step
    status: str
    summary: str
    duration_seconds: float
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "id": self.step.id,
            "title": self.step.title,
            "status": self.status,
            "summary": self.summary,
            "duration_seconds": round(self.duration_seconds, 1),
            "reason": self.reason,
        }


STEPS: dict[str, Step] = {
    "signature-policy": Step(
        id="signature-policy",
        title="Release: обе подписи и fail-closed production-политика",
        kind="pytest",
        targets=[
            "backend/tests/test_release_policy.py",
            "backend/tests/test_release_authenticode.py",
            "backend/tests/test_trust_store.py",
        ],
    ),
    "channel-tamper-refusal": Step(
        id="channel-tamper-refusal",
        title="Канал: подделка manifest/подписи/пакета и запрещённый хост",
        kind="pytest",
        targets=[
            "backend/tests/test_release_pipeline.py",
            "backend/tests/test_channel_network.py",
            "backend/tests/test_staging_recovery.py",
            "backend/tests/test_updates_api.py",
        ],
    ),
    "readiness-api": Step(
        id="readiness-api",
        title="Readiness API: права, redaction, вердикт (server-owned)",
        kind="pytest",
        targets=["backend/tests/test_readiness_api.py"],
    ),
    "backup-restore-isolated-db": Step(
        id="backup-restore-isolated-db",
        title="Шифрованный backup и restore в изолированную БД (PostgreSQL)",
        kind="pytest",
        targets=[
            "backend/tests/test_integration_backup.py",
            "backend/tests/test_integration_pilot_first_run.py",
        ],
        marker="integration",
        requires="PostgreSQL (--database-url или DATABASE_URL)",
    ),
    "windows-engine": Step(
        id="windows-engine",
        title="Windows-движок: install/update/rollback/resume/uninstall (данные сохраняются)",
        kind="powershell",
        requires="Windows PowerShell 5.1+/pwsh и infra/windows/tests/run-tests.ps1",
    ),
}


def _mask(text: str, secrets: list[str]) -> str:
    masked = text
    for secret in secrets:
        if secret:
            masked = masked.replace(secret, "***")
    return masked


def _powershell_command() -> list[str] | None:
    for candidate in ("powershell", "pwsh"):
        binary = shutil.which(candidate)
        if binary:
            return [binary, "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(REPO_ROOT / "infra" / "windows" / "tests" / "run-tests.ps1")]
    return None


def _run_pytest(step: Step, extra_env: dict[str, str], secrets: list[str]) -> StepResult:
    command = [sys.executable, "-m", "pytest", *step.targets]
    if step.marker:
        command += ["-m", step.marker]
    command += ["-q"]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command, cwd=REPO_ROOT, capture_output=True, text=True, timeout=1800, env=extra_env
        )
    except subprocess.TimeoutExpired:
        return StepResult(step, "failed", "timeout", time.monotonic() - started, "шаг не уложился в 30 минут")
    duration = time.monotonic() - started
    output = _mask((completed.stdout or "") + (completed.stderr or ""), secrets)
    for marker in SECRET_MARKERS:
        if marker in output:
            return StepResult(step, "failed", "secret material in output", duration,
                              "вывод шага содержит приватный материал")
    matches = PYTEST_SUMMARY_RE.findall(output)
    summary = ", ".join(f"{count} {kind}" for count, kind in matches[-4:]) or "no pytest summary"
    if completed.returncode == 0:
        return StepResult(step, "passed", summary, duration)
    return StepResult(step, "failed", summary, duration, "pytest вернул non-zero код")


def _run_powershell(step: Step, secrets: list[str]) -> StepResult:
    command = _powershell_command()
    if command is None:
        return StepResult(step, "skipped", "no PowerShell", 0.0, step.requires)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command, cwd=REPO_ROOT, capture_output=True, text=True, timeout=3600
        )
    except subprocess.TimeoutExpired:
        return StepResult(step, "failed", "timeout", time.monotonic() - started, "шаг не уложился в 60 минут")
    duration = time.monotonic() - started
    output = _mask((completed.stdout or "") + (completed.stderr or ""), secrets)
    match = re.search(r"ВСЕ ТЕСТЫ ПРОЙДЕНЫ \((\d+)\)", output)
    summary = f"{match.group(1)} tests passed" if match else "no verdict line"
    if completed.returncode == 0:
        return StepResult(step, "passed", summary, duration)
    return StepResult(step, "failed", summary, duration, "run-tests.ps1 вернул non-zero код")


def _environment(database_url: str | None) -> dict[str, str]:
    env = dict(os.environ)
    # CI не должен использовать production secrets; drill не читает их вовсе.
    env.setdefault("APP_ENV", "test")
    if database_url:
        env["DATABASE_URL"] = database_url
        env.setdefault("TEST_DATABASE_URL", database_url)
    return env


def _steps_for(names: list[str]) -> list[Step]:
    selected = names or [step.id for step in STEPS.values()]
    unknown = [name for name in selected if name not in STEPS]
    if unknown:
        raise SystemExit(f"error: unknown drill steps: {', '.join(unknown)}")
    return [STEPS[name] for name in selected]


def _verdict(results: list[StepResult]) -> str:
    if any(result.status == "failed" for result in results):
        return "failed"
    if any(result.status == "skipped" for result in results):
        return "incomplete"
    return "passed"


def _markdown(report: dict) -> str:
    lines = [
        "# Pilot drill (Phase 14)",
        "",
        f"* Сформирован: {report['generated_at']}",
        f"* Контур: {report['contour']}",
        f"* Вердикт автоматизированной части: **{report['verdict']}**",
        "",
        "| Шаг | Итог | Сводка | Комментарий |",
        "| --- | --- | --- | --- |",
    ]
    for item in report["steps"]:
        lines.append(
            f"| {item['title']} | {item['status']} | {item['summary']} | {item['reason'] or '—'} |"
        )
    lines += [
        "",
        "## Что остаётся ручной Windows-приёмкой (owner action)",
        "",
        "1. Чистая установка Setup.exe на Windows 10/11 и первый вход владельца.",
        "2. Проверка loopback: `Get-NetTCPConnection -State Listen` показывает только 127.0.0.1.",
        "3. Обновление подписанным релизом и наблюдаемый rollback на сломанном релизе.",
        "4. Uninstall без purge (StateDir/volumes/backups сохранены) и повторная установка.",
        "5. Заполнение go/no-go checklist в `docs/runbook-pilot-release.md` (дата, SHA, evidence).",
        "",
        "Секреты, ключи и PII в отчёт не попадают: вывод шагов маскируется, приватный",
        "материал в перехваченном выводе означает провал шага.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 14 automated pilot drill")
    parser.add_argument("--steps", default="", help="список шагов через запятую (по умолчанию все)")
    parser.add_argument("--database-url", default="", help="DSN PostgreSQL для интеграционного шага")
    parser.add_argument("--out-dir", default="drill", help="куда положить JSON и Markdown")
    parser.add_argument("--json-out", default="", help="явный путь JSON-отчёта")
    args = parser.parse_args(argv)

    steps = _steps_for([name.strip() for name in args.steps.split(",") if name.strip()])
    database_url = args.database_url or os.environ.get("DATABASE_URL", "")
    secrets = [database_url] if database_url else []
    env = _environment(database_url or None)

    results: list[StepResult] = []
    for step in steps:
        if step.marker == "integration" and not database_url:
            results.append(StepResult(step, "skipped", "no PostgreSQL DSN", 0.0, step.requires))
            continue
        if step.kind == "powershell":
            results.append(_run_powershell(step, secrets))
        else:
            results.append(_run_pytest(step, env, secrets))
        print(f"[{results[-1].status}] {step.id}: {results[-1].summary}", flush=True)

    report = {
        "schema": DRILL_SCHEMA,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "contour": f"{platform.system()} {platform.release()} / {platform.python_version()}",
        "verdict": _verdict(results),
        "steps": [result.as_dict() for result in results],
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = Path(args.json_out) if args.json_out else out_dir / "pilot-drill.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path = out_dir / "pilot-drill.md"
    markdown_path.write_text(_markdown(report), encoding="utf-8")
    print(f"drill verdict: {report['verdict']} → {json_path}")
    return 0 if report["verdict"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
