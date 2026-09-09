; HR Manager Setup.exe — графический мастер установки локального Windows-пилота.
;
; Компилируется закреплённой версией Inno Setup 6.5.1 (см.
; .github/workflows/ci.yml, job windows-installer). Русский мастер собирает
; только роль и фамилию и передаёт их движку (infra/windows/hr-manager.ps1)
; через JSON input file — НЕ через командную строку процесса. Все технические
; шаги (Docker, Compose, миграции, секреты) скрыты от пользователя.
;
; Определения можно переопределить снаружи:
;   /DVersion=0.1.0 /DPayloadDir=C:\repo /DOutputDir=C:\dist /DReleaseSha=<40hex>
; см. infra/windows/scripts/build-installer.ps1.
#ifndef Version
  #define Version "0.1.0"
#endif
#ifndef PayloadDir
  #define PayloadDir "..\.."
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist\windows"
#endif
#ifndef ReleaseSha
  #define ReleaseSha "0000000000000000000000000000000000000000"
#endif

#define MyAppId "{{FCB1E3A1-2C6E-4E4F-9A2B-8E5D1A7C0B9E}"
#define MyAppName "HR Manager"
#define MyAppExe "HR Manager Setup.exe"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#Version}
AppVerName={#MyAppName} {#Version}
AppPublisher=HR Manager
AppCopyright=(c) HR Manager
VersionInfoVersion={#Version}
DefaultDirName={autopf}\HR Manager
DefaultGroupName=HR Manager
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=HR Manager Setup
Compression=lzma2/max
SolidCompression=yes
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExe}
SetupLogging=yes
WizardStyle=modern
; Русский мастер; технический язык скрыт от пользователя.
ShowLanguageDialog=no
; Дополнительный "технический" поток логов не показывается пользователю.

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Files]
; Публикуемый payload: compose-файлы, движок, backend/frontend для сборки
; образов в Docker Desktop и документация. Без .git, node_modules, dist и пр.
Source: "{#PayloadDir}\infra\*"; DestDir: "{app}\infra"; Flags: recursesubdirs createallsubdirs ignoreversion; Excludes: "*.pyc,__pycache__"
Source: "{#PayloadDir}\backend\*"; DestDir: "{app}\backend"; Flags: recursesubdirs createallsubdirs ignoreversion; Excludes: "*.pyc,__pycache__,.mypy_cache,.pytest_cache,.ruff_cache,.venv,*.egg-info,tests"
Source: "{#PayloadDir}\frontend\*"; DestDir: "{app}\frontend"; Flags: recursesubdirs createallsubdirs ignoreversion; Excludes: "node_modules,dist,.vite,coverage,*.log"
Source: "{#PayloadDir}\docs\*"; DestDir: "{app}\docs"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#PayloadDir}\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PayloadDir}\infra\windows\README.installer.ru.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PayloadDir}\release-manifest.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PayloadDir}\prompts\PHASE_12_PROMPT.md"; DestDir: "{app}\prompts"; Flags: ignoreversion

[Icons]
Name: "{group}\Диагностика HR Manager"; Filename: "{app}\{#MyAppExe}"; Parameters: "/DIAGNOSTICS"
Name: "{group}\Удалить HR Manager"; Filename: "{uninstallexe}"

[Run]
; Запустить движок установки после копирования файлов. Окно скрыто, вывод
; движка не показывается пользователю; прогресс — штатный прогресс мастера.
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; \
  Parameters: "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ""{app}\infra\windows\hr-manager.ps1"" -Action install -InputFile ""{code:GetInputFilePath}"" -PayloadDir ""{app}"" -LauncherExe ""{app}\{#MyAppExe}"""; \
  Flags: runhidden waituntilterminated runascurrentuser; \
  StatusMsg: "Установка HR Manager… Это может занять несколько минут."

[UninstallRun]
; Остановить и удалить контейнеры приложения, НЕ трогая базу и backup.
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; \
  Parameters: "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ""{app}\infra\windows\hr-manager.ps1"" -Action uninstall -PayloadDir ""{app}"""; \
  Flags: runhidden waituntilterminated runascurrentuser

[Code]
var
  PageRole: TInputOptionWizardPage;
  PageSurname: TInputQueryWizardPage;

function GetInputFilePath(Param: String): String;
begin
  Result := AddBackslash(ExpandConstant('{tmp}')) + 'hr-manager-install-input.json';
end;

function IsSurnameValid(const S: String): Boolean;
var
  i: Integer;
  c: Char;
  ok: Boolean;
begin
  Result := False;
  if (Length(Trim(S)) = 0) or (Length(Trim(S)) > 60) then
    Exit;
  for i := 1 to Length(S) do
  begin
    c := S[i];
    ok := ((c >= 'A') and (c <= 'Z')) or ((c >= 'a') and (c <= 'z'))
       or ((c >= 'А') and (c <= 'я')) or (c = 'Ё') or (c = 'ё')
       or (c = '-') or (c = ' ') or (c = '''');
    if not ok then
      Exit;
  end;
  Result := True;
end;

procedure WriteInputFile();
var
  Json: String;
begin
  Json := '{"surname":"' + Trim(PageSurname.Values[0]) + '","working_mode":"';
  case PageRole.SelectedValueIndex of
    0: Json := Json + 'hr';
    1: Json := Json + 'manager';
  else
    Json := Json + 'admin';
  end;
  Json := Json + '"}';
  SaveStringToFile(GetInputFilePath(''), Json, False);
end;

procedure InitializeWizard();
var
  RoleHint: String;
begin
  RoleHint :=
    'Выберите основной рабочий режим. ' +
    'Первый владелец установки получает полный доступ независимо от выбранного режима; ' +
    'обычным сотрудникам роли назначаются позже в разделе «Администрирование».';

  PageRole := CreateInputOptionPage(
    wpWelcome,
    'Рабочая роль',
    'Каким экраном вы будете пользоваться чаще всего?',
    RoleHint,
    False, False);
  PageRole.Add('HR — работа с кандидатами, календарём и документами');
  PageRole.Add('Руководитель — сводки по кандидатам и аналитика команды');
  PageRole.Add('Администратор — полное администрирование и настройка');
  PageRole.Values[0] := True;

  PageSurname := CreateInputQueryPage(
    PageRole.ID,
    'Фамилия владельца',
    'Введите фамилию. Она используется для вашей учётной записи в приложении.',
    'Только буквы, дефис и пробел, не более 60 символов.');
  PageSurname.Add('Фамилия:', False);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = PageSurname.ID then
  begin
    if not IsSurnameValid(PageSurname.Values[0]) then
    begin
      MsgBox('Введите фамилию: только буквы, дефис или пробел, до 60 символов.',
        mbError, MB_OK);
      Result := False;
    end;
  end;
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo,
  MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
var
  Mode: String;
begin
  case PageRole.SelectedValueIndex of
    0: Mode := 'HR';
    1: Mode := 'Руководитель';
  else
    Mode := 'Администратор';
  end;
  Result :=
    'Рабочая роль: ' + Mode + NewLine +
    'Фамилия владельца: ' + Trim(PageSurname.Values[0]) + NewLine +
    NewLine +
    'Этот компьютер станет локальным сервером HR Manager. ' +
    'Приложение будет доступно только с этого компьютера по адресу ' +
    'http://127.0.0.1:8080. Нажмите «Установить», чтобы продолжить.';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
    WriteInputFile();
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
end;
