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

[Tasks]
; Пользователь сам решает, устанавливать ли Docker Desktop. По умолчанию
; галочка стоит — обычному пользователю достаточно нажать «Установить»; при
; этом лицензию Docker и запрос UAC принимает человек, а не программа.
Name: "dockerinstall"; Description: "{cm:DockerTaskInstall}"; GroupDescription: "{cm:DockerTaskGroup}"

[CustomMessages]
russian.RolePageCaption=Роль владельца
russian.RolePageDescription=Выберите режим работы единственной учётной записи пилота. Роль можно будет изменить позже в настройках приложения.
russian.RolePageSubCaption=Данные остаются на этом компьютере. Доступ по сети выключен, пока вы не включите его отдельно.
russian.RoleHR=HR — работа с кандидатами и документами
russian.RoleManager=Руководитель — согласования и обзор
russian.RoleAdmin=Администратор — полное администрирование
russian.SurnamePageCaption=Владелец
russian.SurnamePageDescription=Введите фамилию владельца. Техническое имя пользователя будет создано автоматически, а пароль вы зададите в браузере на странице первого запуска.
russian.TimezonePageCaption=Часовой пояс
russian.TimezonePageDescription=Часовой пояс для уведомлений и расписаний (по умолчанию Europe/Moscow). Изменить можно в настройках приложения.
russian.DockerNote=HR Manager работает в контейнерах, поэтому нужен Docker Desktop.%n%nЕсли Docker Desktop ещё не установлен, оставьте галочку ниже: HR Manager скачает ОФИЦИАЛЬНЫЙ установщик с сайта docker.com. Windows запросит разрешение (UAC), а лицензионное соглашение Docker принимаете вы сами — за вас его никто не принимает.%n%nЕсли Docker Desktop уже установлен и запущен — просто снимите галочку.
russian.DockerTaskGroup=Docker для HR Manager:
russian.DockerTaskInstall=Установить Docker Desktop сейчас (официальный установщик Docker)
russian.FinishTitle=HR Manager установлен
russian.FinishText=Готово! HR Manager уже запускается — это занимает 2–5 минут.%n%nВ правом нижнем углу появится значок HR Manager (в трее): он показывает состояние «Запускается», «Готово», «Ошибка».%nКогда всё будет готово, откроется браузер со страницей первого запуска — задайте пароль администратора.%nЗначок HR Manager на рабочем столе открывает приложение; меню значка позволяет перезапустить приложение и создать отчёт для поддержки.%n%nЕсли что-то пойдёт не так — откройте значок HR Manager в трее и нажмите «Создать отчёт для поддержки».

[Files]
; Снимок приложения собирает installer/build.ps1 в staging/app:
; backend/, frontend/ (исходники, без node_modules), infra/ и release.json.
Source: "staging\app\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
; Каталог состояния создаёт движок; здесь — только пустой маркер структуры не нужен.

[Run]
; Порядок после копирования файлов:
;   1) значок HR Manager в трее (управляющий компонент) — пользователь видит
;      состояние «Запускается / Готово / Ошибка» и кнопки действий;
;   2) движок выполняется СКРЫТО и пишет журнал в каталог состояния: проверка
;      Windows/WSL2/виртуализации/места/порта, при необходимости установка
;      Docker Desktop официальным установщиком (UAC и лицензию принимает
;      пользователь), ожидание Docker Engine, сборка и запуск контейнеров,
;      первый запуск в браузере.
; После перезагрузки Windows (если её потребует установка Docker) движок
; продолжит работу сам — см. действие resume.
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\infra\windows\hrm-tray.ps1"" -InstallDir ""{app}"""; \
  WorkingDir: "{app}"; \
  Flags: postinstall nowait runhidden skipifsilent

Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\infra\windows\hr-manager.ps1"" -Action install -SourceDir ""{app}"" -NonInteractive -OpenBrowser {code:GetEngineDockerArgs}"; \
  WorkingDir: "{app}"; \
  StatusMsg: "HR Manager устанавливается (это может занять несколько минут)…"; \
  Flags: postinstall nowait runhidden skipifsilent

; Наблюдатель канала обновлений больше НЕ запускается из установщика: его
; запускает supervisor один раз — повторные установки не плодят процессов.

[UninstallRun]
; Контейнеры останавливаются; данные Postgres и зашифрованные бэкапы
; СОХРАНЯЮТСЯ (тома pilot_pgdata/pilot_backups). Docker Desktop и WSL2
; не удаляются никогда.
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\infra\windows\hr-manager.ps1"" -Action uninstall -NonInteractive"; \
  WorkingDir: "{app}"; \
  Flags: runhidden; \
  RunOnceId: "HRMUninstallStop"

[Icons]
; Ярлыки пилота (B3): рабочий стол + меню Пуск — «HR Manager» открывает
; приложение в браузере (скрытое окно PowerShell). Только в меню Пуск:
; «отчёт для разработчика», «перезапуск», «доступ по сети». Автозапуск
; после перезагрузки — через папку автозагрузки пользователя (если запущен
; Docker Desktop) или вручную по ярлыку «HR Manager».
Name: "{userdesktop}\HR Manager"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\infra\windows\hr-manager.ps1"" -Action open"; WorkingDir: "{app}"; IconFilename: "powershell.exe"; Comment: "Открыть HR Manager в браузере"
Name: "{group}\HR Manager"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\infra\windows\hr-manager.ps1"" -Action open"; WorkingDir: "{app}"; IconFilename: "powershell.exe"; Comment: "Открыть HR Manager в браузере"
Name: "{group}\HR Manager — отчёт для разработчика"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\infra\windows\hr-manager.ps1"" -Action support-bundle"; WorkingDir: "{app}"; IconFilename: "powershell.exe"; Comment: "Создать архив диагностики для отправки разработчику"
Name: "{group}\HR Manager — перезапуск"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\infra\windows\hr-manager.ps1"" -Action restart"; WorkingDir: "{app}"; IconFilename: "powershell.exe"; Comment: "Перезапустить контейнеры HR Manager"
Name: "{group}\HR Manager — значок в трее"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\infra\windows\hrm-tray.ps1"" -InstallDir ""{app}"""; WorkingDir: "{app}"; IconFilename: "powershell.exe"; Comment: "Запустить значок HR Manager в системном трее"
Name: "{group}\HR Manager — доступ по сети"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\infra\windows\hr-manager.ps1"" -Action lan-access"; WorkingDir: "{app}"; IconFilename: "powershell.exe"; Comment: "Показать адрес для доступа коллеги по локальной сети"
Name: "{userstartup}\HR Manager (трей)"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\infra\windows\hrm-tray.ps1"" -InstallDir ""{app}"""; WorkingDir: "{app}"; IconFilename: "powershell.exe"; Comment: "Автозапуск HR Manager после входа в Windows (значок в трее)"

[UninstallDelete]
; Ярлык автозапуска supervisor'а: без него значок в трее больше не поднимался бы.
Type: files; Name: "{userstartup}\HR Manager (трей).lnk"
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
var
  WelcomeNote: String;
begin
  RolePage := CreateInputOptionPage(
    wpWelcome,
    CustomMessage('RolePageCaption'),
    CustomMessage('RolePageDescription'),
    CustomMessage('RolePageSubCaption'),
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

  // Первый экран сразу объясняет, что потребуется от человека: Docker Desktop,
  // официальный установщик с docker.com, запрос UAC и лицензия Docker, которую
  // принимает сам пользователь. %n из [CustomMessages] превращаем в переводы
  // строк сами: CustomMessage() не обязан раскрывать эти последовательности.
  WelcomeNote := CustomMessage('DockerNote');
  StringChange(WelcomeNote, '%n', #13#10);
  WizardForm.WelcomeLabel2.Caption :=
    WizardForm.WelcomeLabel2.Caption + #13#10 + #13#10 + WelcomeNote;
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

function GetEngineDockerArgs(Param: String): String;
begin
  // Галочка «Установить Docker Desktop» передаёт движку разрешение предложить
  // официальный установщик Docker. Отмена UAC или отказ от лицензии Docker —
  // штатный результат: движок покажет понятное сообщение, а не «упадёт».
  if WizardIsTaskSelected('dockerinstall') then
    Result := '-InstallDocker'
  else
    Result := '';
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpFinished then
  begin
    WizardForm.FinishedLabel.Caption :=
      ExpandConstant('{cm:FinishText}');
    WizardForm.FinishedHeadingLabel.Caption :=
      ExpandConstant('{cm:FinishTitle}');
  end;
end;
