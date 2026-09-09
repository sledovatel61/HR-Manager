; ============================================================================
; HR Manager — локальный пилот (Windows), фаза 12: графический Setup.exe.
;
; Инварианты контракта:
;  * В git нет бинарников: Setup.exe собирается в CI (job installer-windows),
;    версия Inno Setup закреплена в CI (6.4.1) с проверкой SHA-256 архива.
;  * Секретов/паролей установщик не содержит и не спрашивает: пароли генерит
;    движок; пароль пилота пользователь выбирает сам в защищённой веб-странице.
;  * Фамилия (данные, чувствительные к PII) передаётся движку только через
;    временный JSON-файл {tmp}, никогда — через аргументы командной строки.
;  * Вся установка/обновление/ремонт — у идемпотентного движка
;    (bundle\infra\windows\hr-manager.ps1); GUI показывает этапы и ошибки.
;  * Повторный Setup.exe: тот же AppId → переустановка/обновление, второй
;    пилот не создаётся (гарантия одна-разового pairing на backend).
; ============================================================================

#define MyAppName "HR Manager (локальный пилот)"
#ifndef MyAppVersion
  #define MyAppVersion "12.0.0-dev"
#endif
#ifndef BundleDir
  #define BundleDir "..\..\..\build\release-bundle"
#endif

[Setup]
AppId={{8F0C2D4E-7A55-4C3B-9E21-52D0B6F1C3AA}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher="HR Manager"
AppPublisherURL="https://github.com/sledovatel61/HR-Manager"
DefaultDirName={autopf}\HRManagerPilot
DisableProgramGroupPage=yes
DisableWelcomePage=no
DisableDirPage=auto
; Per-user без администратора ({autopf} при PrivilegesRequired=lowest = {userpf}).
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\..\build\installer
OutputBaseFilename=HRManager-Setup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\uninstall.exe

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Messages]
russian.WelcomeLabel2=Мастер установит «HR Manager» — локальный пилот для отдела.%n%nНажмите «Далее». Docker Desktop будет использован как локальная среда выполнения; если его нет, установщик предложит официальный установщик.

[Files]
; Релизный комплект с манифестом и SHA256SUMS.txt (проверяет целостность сам
; движок перед любыми изменениями на машине).
Source: "{#BundleDir}\*"; DestDir: "{app}\bundle"; Flags: ignoreversion recursesubdirs createallsubdirs

[Code]
var
  SurnamePage: TInputQueryWizardPage;
  RolePage: TInputOptionWizardPage;
  SmokeMode: Boolean;

function EscapeJson(const S: String): String;
var
  R: String;
  I: Integer;
  C: Char;
begin
  R := '';
  for I := 1 to Length(S) do
  begin
    C := S[I];
    if C = '"' then R := R + '\"'
    else if C = '\' then R := R + '\\'
    else if C = #13 then { skip }
    else if C = #10 then R := R + ' '
    else R := R + C;
  end;
  Result := R;
end;

function JsonField(const Lines: TArrayOfString; const Field: String): String;
var
  I, P1, P2: Integer;
  Line: String;
begin
  Result := '';
  for I := 0 to GetArrayLength(Lines) - 1 do
  begin
    Line := Lines[I];
    P1 := Pos('"' + Field + '"', Line);
    if P1 = 0 then Continue;
    P1 := PosEx(':', Line, P1 + Length(Field) + 2);
    if P1 = 0 then Continue;
    P1 := PosEx('"', Line, P1 + 1);
    if P1 = 0 then Continue;
    P2 := PosEx('"', Line, P1 + 1);
    if P2 = 0 then Continue;
    Result := Copy(Line, P1 + 1, P2 - P1 - 1);
    Exit;
  end;
end;

procedure InitializeWizard;
begin
  // PreviousPageID = 0: страница встаёт последней перед подтверждением установки.
  SurnamePage := CreateInputQueryPage(0,
    'Первый вход руководителя пилота',
    'Как вас записать?',
    'Нужна только фамилия — логин «HR Manager» система придумает сама и покажет на следующем шаге веб-настройки. ' +
    'Пароль вы зададите позже на защищённой локальной странице. Никаких email и телефонов здесь не нужно.');
  SurnamePage.Add('Фамилия', False);

  RolePage := CreateInputOptionPage(SurnamePage.ID,
    'Рабочая роль в пилоте',
    'Откуда вы будете работать?',
    'Роль влияет только на удобный порядок разделов и подсказки интерфейса (UX-настройка). ' +
    'Права доступа у всех участников пилота одинаковые и общие — пилот проверяет процесс, а не разграничение отделов.',
    False, False);
  RolePage.Add('HR — ведение кадров и документов');
  RolePage.Add('Руководитель — согласование и статусы');
  RolePage.Add('Администратор — настройка и сервисы');
  RolePage.Values[0] := True;

  if GetEnv('HRMGR_PILOT_PORT') <> '' then
    Log('port override: ' + GetEnv('HRMGR_PILOT_PORT'));
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Surname: String;
begin
  Result := True;
  if CurPageID = SurnamePage.ID then
  begin
    Surname := Trim(SurnamePage.Values[0]);
    if (Length(Surname) < 2) or (Length(Surname) > 60) then
    begin
      MsgBox('Фамилия должна содержать от 2 до 60 символов.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
  end;
end;

procedure WriteSetupInput;
var
  RoleKey, Json: String;
  Surname: String;
begin
  Surname := Trim(SurnamePage.Values[0]);
  if RolePage.Values[0] then RoleKey := 'hr'
  else if RolePage.Values[1] then RoleKey := 'manager'
  else RoleKey := 'admin';
  Json := '{"surname":"' + EscapeJson(Surname) + '","workRole":"' + RoleKey + '"}';
  SaveStringToFile(ExpandConstant('{tmp}\hrmgr-setup-input.json'), Json, False, ENC_UTF8);
  // {tmp} живёт до конца работы установщика и защищён Windows для текущего
  // пользователя (ACL каталога Temp); движок стирает файл после успешной выдачи.
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if not SmokeMode then WriteSetupInput;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Lines: TArrayOfString;
  Msg, Hint, OkFlag: String;
begin
  if CurStep <> ssPostInstall then Exit;
  if SmokeMode then
  begin
    SaveStringToFile(ExpandConstant('{app}\smoke-ok.txt'), 'ok', False);
    Exit;
  end;
  // Итог работы движка: [Run] уже отработал (waituntilterminated) до ssPostInstall.
  if LoadStringToFile(ExpandConstant('{tmp}\hrmgr-engine-outcome.txt'), OkFlag) then
  begin
    if OkFlag <> 'OK' then
    begin
      Msg := 'Установка не завершена на шаге «' + OkFlag + '».';
      Hint := 'Повторите запуск Setup.exe — безопасный шаг продолжит с того же места. ' +
               'Данные при этом не теряются. Подробности: «Диагностика HR Manager».';
      if LoadStringsFromFile(ExpandConstant('{tmp}\hrmgr-engine-message.txt'), Lines) then
      begin
        Msg := Msg + #13#10#13#10 + Trim(Lines[0]);
        if GetArrayLength(Lines) > 1 then
          Hint := Trim(Lines[1]);
      end;
      MsgBox(Msg + #13#10#13#10 + Hint, mbError, MB_OK);
    end;
  end;
end;

function InitializeSetup: Boolean;
begin
  SmokeMode := GetEnv('HRMGR_SMOKE_NO_ENGINE') = '1';
  Result := True;
end;

function EngineShouldRun: Boolean;
begin
  Result := not SmokeMode;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Choice: Integer;
  Phrase, Args, Cmd: String;
  Code: Integer;
  Engine: String;
begin
  if CurUninstallStep <> usUninstall then Exit;
  if SmokeMode then Exit;
  Choice := MsgBox(
    'Данные пилота (база, резервные копии и локальные пароли) находятся в ' +
    '%LOCALAPPDATA%\HR Manager и ПО ОСТАТУТСЯ ЦЕЛЫМИ, если не выбрать полное удаление.' + #13#10#13#10 +
    'Удалить ВСЕ ДАННЫЕ без возможности возврата?',
    mbConfirmation, MB_YESNO);
  Args := '-Action uninstall';
  if Choice = IDYES then
  begin
    if InputQuery('Полное удаление данных',
         'Для подтверждения введите ТОЧНО (заглавными): УДАЛИТЬ ДАННЫЕ HR MANAGER', Phrase) then
    begin
      if Phrase = 'УДАЛИТЬ ДАННЫЕ HR MANAGER' then
        Args := Args + ' -RemoveData -ConfirmPhrase "' + Phrase + '" -ExportBackupTo "' +
                ExpandConstant('{localappdata}\HR Manager\final-backup') + '"'
      else
        MsgBox('Фраза не совпала — данные НЕ будут удалены (движок тоже откажет).', mbInformation, MB_OK);
    end
    else
      MsgBox('Фраза не введена — данные НЕ будут удалены.', mbInformation, MB_OK);
  end;
  Engine := ExpandConstant('{app}\bundle\infra\windows\hr-manager.ps1');
  Cmd := '-NoProfile -ExecutionPolicy Bypass -File "' + Engine + '" ' + Args;
  Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Cmd, '', SW_HIDE,
       ewWaitUntilTerminated, Code);
end;

[Run]
; Единственный тяжёлый шаг — движок. Все сообщения русские, этапы идемпотентны,
; коды ошибок движок транслирует в понятный текст в этом же диалоге.
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\bundle\infra\windows\hr-manager.ps1"" -Action install -AppDir ""{app}\bundle"" -InputFile ""{tmp}\hrmgr-setup-input.json"" -JsonOut ""{tmp}\hrmgr-engine-result.json"""; \
  WorkingDir: "{app}"; \
  StatusMsg: "Установка HR Manager: проверка системы, сборка локального контура и подготовка первого входа…"; \
  Check: EngineShouldRun; \
  Flags: runhidden waituntilterminated
; Код возврата движка Inno не показывает — просим движок оставить итог сам
; (hr-manager.ps1 пишет hrmgr-engine-outcome/message через обёртку ниже).
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""$r = Get-Content -Raw '{tmp}\hrmgr-engine-result.json' | ConvertFrom-Json; if ($r.ok) {{ 'OK' | Out-File -Encoding utf8 '{tmp}\hrmgr-engine-outcome.txt' }} else {{ $r.error.message + [Environment]::NewLine + $r.error.hint | Out-File -Encoding utf8 '{tmp}\hrmgr-engine-message.txt'; $r.action | Out-File -Encoding utf8 '{tmp}\hrmgr-engine-outcome.txt' }}"""; \
  Check: EngineShouldRun; \
  Flags: runhidden waituntilterminated
; Обновление вместо установки: если на машине уже стоит другая версия,
; движок сам решит внутри install-идемпотентности; отдельный вход обновления —
; повторный запуск Setup.exe (тот же идемпотентный путь, см. документацию).

[UninstallDelete]
; Явно ничего не удаляем сверх Inno: состояние и тома — только через движок.
