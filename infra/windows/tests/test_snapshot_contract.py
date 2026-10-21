"""Source wiring contracts only; these do NOT replace either PowerShell runtime.

HRM_SNAPSHOT_CONTRACT_ROOT can point to a read-only baseline export to demonstrate
red/green for diagnostic/build wiring, without changing the checked-out branch.
"""

import os
import re
from pathlib import Path

ROOT = Path(
    os.environ.get("HRM_SNAPSHOT_CONTRACT_ROOT", Path(__file__).resolve().parents[3])
)
SNAPSHOT = (ROOT / "infra/windows/engine/Snapshot.psm1").read_text(encoding="utf-8-sig")
BUILD = (ROOT / "installer/build.ps1").read_text(encoding="utf-8-sig")
INNO = (ROOT / "installer/installer.iss").read_text(encoding="utf-8-sig")


def test_diagnostic_fields_reach_helper_and_inno_json():
    for field in (
        "file_sha",
        "new_release_sha",
        "record_sha",
        "record_install_dir",
        "target_install_dir",
        "record_pilot_created",
        "message",
        "guard_branch",
    ):
        assert f"{field} =" in SNAPSHOT
        assert f"'{field}'" in INNO
    assert "EscapeJson(SnapshotDetailValues[I])" in INNO
    assert "UTF8Encode(Json)" in INNO


def test_first_run_input_written_as_utf8():
    # Движок читает first-run-input.json как UTF-8 (Common.psm1 → Get-HrmJsonFile →
    # Get-Content -Encoding UTF8). SaveStringToFile без UTF8Encode пишет в системной
    # ANSI-кодировке, поэтому кириллица в фамилии портилась ещё до регистрации.
    # Проверяем именно тело WriteFirstRunInput: наличие UTF8Encode у другого вызова
    # (диагностика снимка) дефект не ловит.
    body = INNO.split("procedure WriteFirstRunInput()", 1)[1].split("\nend;", 1)[0]
    assert "SaveStringToFile(InputFile, UTF8Encode(Json), False)" in body


def test_master_never_writes_ansi_json_for_engine():
    # Движок читает JSON-файлы мастера как UTF-8, а SaveStringToFile без UTF8Encode
    # пишет в системной ANSI-кодировке. Значит русский текст внутри JSON-литерала
    # обязан быть экранирован как \uXXXX — так поступает MarkHrmSetupEngineFailed.
    # Литерал и вызов SaveStringToFile лежат на разных строках, поэтому проверяется
    # именно литерал Marker. Сообщения RaiseException в проверку не входят: они
    # показываются в UI мастера и в файл не пишутся.
    body = INNO.split("procedure WriteSetupMarker()", 1)[1].split("\nend;", 1)[0]
    code = "\n".join(line.split("//", 1)[0] for line in body.splitlines())
    markers = [line for line in code.splitlines() if "Marker :=" in line]
    assert markers, "в WriteSetupMarker не найден литерал Marker :="
    bad = sorted({c for line in markers for c in line if ord(c) > 127})
    assert not bad, f"WriteSetupMarker: русский текст записан сырыми байтами: {bad}"


def test_first_run_input_and_marker_written_before_run_section():
    # Секция [Run] выполняется РАНЬШЕ события CurStepChanged(ssPostInstall): в
    # компиляторе Inno сначала ProcessRunEntries, и только потом
    # SetStep(ssPostInstall, True). Значит first-run-input.json и отметку
    # setup-run.json мастер обязан написать в ветке ssInstall. Пока они жили в
    # ssPostInstall, порядок был обратным ожидаемому: движок из [Run] читал файл
    # ввода ДО его записи, не находил фамилию и молча подставлял "Owner"
    # (Get-HrmFirstRunInput в Bootstrap.psm1), а WriteSetupMarker затирал честный
    # итог движка словом "running" — мастер отдавал код 10 при живом приложении.
    # Живой дефект приёмки 21.10.2026: фамилия "Перепечай" в файле верна, а
    # владелец создан как "Owner".
    match = re.search(
        r"(?ms)^procedure CurStepChanged\(CurStep: TSetupStep\);(.*?)^end;", INNO
    )
    assert match, "в installer.iss нет процедуры CurStepChanged"
    body = match.group(1)
    for proc in ("WriteFirstRunInput()", "WriteSetupMarker()"):
        assert proc in body, f"CurStepChanged не вызывает {proc}"
        before = body[: body.index(proc)]
        post = before.rfind("if CurStep = ssPostInstall then")
        install = before.rfind("if CurStep = ssInstall then")
        assert post <= install, (
            f"{proc} вызывается в ветке ssPostInstall, а секция [Run] выполняется "
            "раньше этого события: движок прочитает first-run-input.json до записи "
            "и молча подставит фамилию \"Owner\", а отметка затрёт итог движка "
            "словом \"running\" — мастер отдаст код 10 при живом приложении"
        )
