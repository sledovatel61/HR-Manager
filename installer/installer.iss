; HR Manager — локальный пилот для Windows (phase 12).
; Мастер установки Inno Setup 6.7.3 (закреплено в installer/README.md):
;   роль (HR | Руководитель | Администратор) → фамилия владельца →
;   часовой пояс (по умолчанию Europe/Moscow) → Установить.
; Вся автоматизация после копирования файлов — infra/windows/hr-manager.ps1
; (сборка образов, секреты, Docker, первый запуск). Мастер не трогает
; Docker и не принимает лицензии за пользователя: Docker Desktop ставится
; только самим пользователем с официального сайта docker.com.
;
; Сборка: installer/build.ps1 (закрепляет версию Inno Setup и хеши).

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{3F5A9C71-8E2B-4D6A-9B0F-2C7D1E5A4B36}
AppName=HR Manager
AppVersion={#AppVersion}
AppPublisher=HR Manager
AppPublisherURL=https://github.com/sledovatel61/HR-Manager
DefaultDirName={localappdata}\Programs\HRManager
DefaultGroupName=HR Manager
; Установка только текущему пользователю — без UAC; данные и Docker не требуют прав администратора.
PrivilegesRequired=lowest
OutputDir=output
OutputBaseFilename=HR-Manager-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Повторный запуск установщика новой версии = обновление (движок распознаёт
; существующую установку и сохраняет данные/секреты).
DisableProgramGroupPage=yes
DisableDirPage=yes
SetupLogging=yes
UninstallDisplayName=HR Manager
UninstallDisplayIcon={app}\infra\windows\hr-manager.ps1
; Каталог установки показываем только как информацию.
UsePreviousAppDir=yes

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[CustomMessages]
russian.RolePageCaption=Роль владельца
russian.RolePageDescription=Выберите режим работы единственной учётной записи пилота. Роль можно будет изменить позже в настройках приложения.
russian.RoleHR=HR — работа с кандидатами и документами
russian.RoleManager=Руководитель — согласования и обзор
russian.RoleAdmin=Администратор — полное администрирование
russian.SurnamePageCaption=Владелец
russian.SurnamePageDescription=Введите фамилию владельца. Техническое имя пользователя будет создано автоматически, а пароль вы зададите в браузере на странице первого запуска.
russian.TimezonePageCaption=Часовой пояс
russian.TimezonePageDescription=Часовой пояс для уведомлений и расписаний (по умолчанию Europe/Moscow). Изменить можно в настройках приложения.
russian.DockerNote=Перед установкой убедитесь, что Docker Desktop установлен с официального сайта docker.com и запущен. Установщик не скачивает и не принимает лицензию Docker автоматически.

[Files]
; Снимок приложения собирает installer/build.ps1 в staging/app:
; backend/, frontend/ (исходники, без node_modules), infra/ и release.json.
Source: "staging\app\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
; Каталог состояния создаёт движок; здесь — только пустой маркер структуры не нужен.

[Run]
; Запуск движка после копирования файлов: сборка образов, генерация
; секретов, подъём контейнеров, одноразовый loopback-обмен и браузер
; со страницей первого запуска. Окно консоли остаётся видимым — виден
; прогресс сборки; флаг -NonInteractive отключает запросы (все ответы уже
; даны мастером через first-run-input.json).
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\infra\windows\hr-manager.ps1"" -Action install -SourceDir ""{app}"" -NonInteractive -OpenBrowser"; \
  WorkingDir: "{app}"; \
  StatusMsg: "Установка HR Manager (сборка образов может занять несколько минут)…"; \
  Flags: postinstall nowait skipifsilent

; Наблюдатель канала обновлений (Phase 13): фоновый опрос и выполнение
; только явно поставленной команды установки. Фоновая установка не
; запускается никогда.
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\infra\windows\hr-manager.ps1"" -Action channel -Watch -InstallDir ""{app}"""; \
  WorkingDir: "{app}"; \
  Flags: postinstall nowait runhidden skipifsilent

[UninstallRun]
; Контейнеры останавливаются; данные Postgres и зашифрованные бэкапы
; СОХРАНЯЮТСЯ (тома pilot_pgdata/pilot_backups). Docker Desktop и WSL2
; не удаляются никогда.
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\infra\windows\hr-manager.ps1"" -Action uninstall -NonInteractive"; \
  WorkingDir: "{app}"; \
  Flags: runhidden; \
  RunOnceId: "HRMUninstallStop"

[UninstallDelete]
; Штатное обновление заменяет файлы снимка уже после установки, поэтому Inno
; не считает их исходными файлами пакета. После остановки стека удаляем только
; каталог программы; StateDir и именованные Docker volumes намеренно вне {app}.
Type: filesandordirs; Name: "{app}"

[Code]
var
  RolePage: TInputOptionWizardPage;
  SurnamePage: TInputQueryWizardPage;
  TimezonePage: TInputQueryWizardPage;

procedure InitializeWizard();
begin
  RolePage := CreateInputOptionPage(
    wpWelcome,
    CustomMessage('RolePageCaption'),
    CustomMessage('RolePageDescription'),
    CustomMessage('DockerNote') + #13#10 + #13#10 + 'Роль:',
    True, False);
  RolePage.Add(CustomMessage('RoleHR'));
  RolePage.Add(CustomMessage('RoleManager'));
  RolePage.Add(CustomMessage('RoleAdmin'));
  RolePage.Values[0] := True;

  SurnamePage := CreateInputQueryPage(
    RolePage.ID,
    CustomMessage('SurnamePageCaption'),
    CustomMessage('SurnamePageDescription'),
    'Фамилия:');
  SurnamePage.Add('Фамилия:', False);
  SurnamePage.Values[0] := '';

  TimezonePage := CreateInputQueryPage(
    SurnamePage.ID,
    CustomMessage('TimezonePageCaption'),
    CustomMessage('TimezonePageDescription'),
    'Часовой пояс (IANA):');
  TimezonePage.Add('Часовой пояс (IANA):', False);
  TimezonePage.Values[0] := 'Europe/Moscow';
end;

function GetWorkingMode(): string;
begin
  if RolePage.Values[1] then
    Result := 'manager'
  else if RolePage.Values[2] then
    Result := 'admin'
  else
    Result := 'hr';
end;

function EscapeJson(const S: string): string;
begin
  Result := S;
  StringChange(Result, '\', '\\');
  StringChange(Result, '"', '\"');
end;

procedure WriteFirstRunInput();
var
  StateDir, InputFile, Surname, Json: string;
begin
  StateDir := ExpandConstant('{localappdata}\HRManager');
  ForceDirectories(StateDir);
  Surname := Trim(SurnamePage.Values[0]);
  if Surname = '' then
    Surname := 'Owner';
  Json := '{"surname": "' + EscapeJson(Surname) + '", "working_mode": "' +
          GetWorkingMode() + '", "timezone": "' +
          EscapeJson(Trim(TimezonePage.Values[0])) + '"}';
  InputFile := StateDir + '\first-run-input.json';
  SaveStringToFile(InputFile, Json, False);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    WriteFirstRunInput();
end;
