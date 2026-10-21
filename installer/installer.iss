; HR Manager — локальный пилот для Windows (phase 12).
; Мастер установки Inno Setup 6.7.3 (закреплено в installer/README.md):
;   роль (HR | Руководитель | Администратор) → фамилия владельца →
;   часовой пояс (по умолчанию Europe/Moscow) → Установить.
; После копирования файлов движок проверяет среду и запускает приложение.
; Docker Desktop не скачивается и не запускается автоматически: если он
; отсутствует, пользователь устанавливает его вручную с официального сайта.
; Необязательный DockerDesktopInstaller.exe рядом с Setup.exe может быть
; скопирован в {app}\docker, но никогда не запускается этим мастером.
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
russian.RolePageSubCaption=Данные остаются на этом компьютере. Доступ по сети выключен, пока вы не включите его отдельно.
russian.RoleHR=HR — работа с кандидатами и документами
russian.RoleManager=Руководитель — согласования и обзор
russian.RoleAdmin=Администратор — полное администрирование
russian.SurnamePageCaption=Владелец
russian.SurnamePageDescription=Введите фамилию владельца. Техническое имя пользователя будет создано автоматически, а пароль вы зададите в браузере на странице первого запуска.
russian.TimezonePageCaption=Часовой пояс
russian.TimezonePageDescription=Часовой пояс для уведомлений и расписаний (по умолчанию Europe/Moscow). Изменить можно в настройках приложения.
russian.DockerNote=HR Manager работает в контейнерах, поэтому необходимы Docker Desktop и готовый Docker Engine.%n%nЕсли Docker Desktop ещё не установлен, скачайте его только с официального сайта https://www.docker.com/products/docker-desktop/ и запустите установку самостоятельно. HR Manager не скачивает и не запускает установщик Docker Desktop и не принимает лицензию Docker за пользователя.%n%nПосле установки запустите Docker Desktop, дождитесь готовности Docker Engine и повторите запуск HR Manager. Если в дистрибутиве рядом с Setup.exe есть дополнительный DockerDesktopInstaller.exe, мастер скопирует его в папку docker приложения; этот файл никогда не запускается автоматически.
russian.SnapshotStop=Установка остановлена ДО замены файлов. Сверьте каталоги установки и состояния и конкретную причину в локальном setup-snapshot.json. Восстановите соответствующие записанной версии файлы или проверенный снимок; простой повтор Setup не исправляет несовпадение.
russian.SnapshotFailed=Не удалось сохранить копию предыдущей версии, поэтому обновление остановлено: так у вас останется возможность вернуться к работающей версии.
russian.SnapshotFilesReplaced=Нельзя подтвердить прежнюю версию файлов, а проверенного снимка нет. Проверьте InstallDir/StateDir и локальную диагностику. Нужны соответствующие записи исходные файлы или проверенный снимок, а не повтор установки без изменения состояния.
russian.SnapshotHelperFailed=Не удалось запустить подготовку к обновлению. Проверьте, что Windows PowerShell доступен, и запустите Setup.exe заново.
russian.FinishTitle=HR Manager установлен
russian.FinishText=Файлы HR Manager установлены, приложение запустилось и движок подтвердил готовность.%n%nВ правом нижнем углу появится значок HR Manager (в трее). Когда всё будет готово, откроется браузер со страницей первого запуска — задайте пароль администратора.%nЗначок HR Manager на рабочем столе открывает приложение; меню значка позволяет перезапустить приложение и создать отчёт для поддержки.%n%nЕсли что-то пойдёт не так — откройте значок HR Manager в трее и нажмите «Создать отчёт для поддержки».
russian.FinishIncompleteTitle=Файлы установлены, но HR Manager не готов
russian.FinishIncompleteText=Setup.exe установил файлы, однако движок не подтвердил готовность приложения. Код завершения мастера: 10 (установка файлов завершена, настройка приложения — нет).%n%nПроверьте значок HR Manager в системном трее и файл %LOCALAPPDATA%\HRManager\setup-run.json. Если Docker Desktop отсутствует, установите его вручную только с официального сайта https://www.docker.com/products/docker-desktop/, запустите Docker Desktop и дождитесь готовности Docker Engine. Затем повторите запуск HR Manager.%n%nЕсли Docker Desktop уже установлен, откройте его и дождитесь готовности Engine. Для поддержки создайте отчёт из меню значка HR Manager.

[Files]
; --- Снимок прежней версии: вспомогательные файлы идут ПЕРВЫМИ --------------
; При solid compression мастер распаковывает файл тем быстрее, чем он раньше в
; списке. Эти файлы НЕ устанавливаются в {app}: мастер распаковывает их во
; временный каталог и запускает до первой перезаписи {app}.
;
; Почему из пакета, а не у установленного движка: прежняя версия может не знать
; ни действия snapshot-previous, ни формата снимка — тогда обновление молча
; перезаписало бы файлы без возможности отката (дефект ревью итерации 15).
; MergeDuplicateFiles по умолчанию включён: тот же исходный файл хранится в
; пакете один раз, хотя перечислен и здесь, и в обычном снимке приложения ниже.
Source: "staging\app\infra\windows\hrm-snapshot.ps1"; Flags: dontcopy noencryption
Source: "staging\app\infra\windows\engine\Common.psm1"; Flags: dontcopy noencryption
Source: "staging\app\infra\windows\engine\Install.psm1"; Flags: dontcopy noencryption
Source: "staging\app\infra\windows\engine\Secrets.psm1"; Flags: dontcopy noencryption
Source: "staging\app\infra\windows\engine\Snapshot.psm1"; Flags: dontcopy noencryption
; release.json ЭТОГО пакета: по нему вспомогательный скрипт отличает «в {app} прежняя
; версия» от «мастер уже разложил файлы новой версии» (последнее — отказ, а не снимок).
Source: "staging\app\release.json"; Flags: dontcopy noencryption

; Снимок приложения собирает installer/build.ps1 в staging/app:
; backend/, frontend/ (исходники, без node_modules), infra/ и release.json.
Source: "staging\app\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
; Sidecar is external: it is read from beside Setup.exe at install time, not embedded
; in Setup.exe. Missing sidecar is allowed; the engine only links to the official site.
Source: "{src}\DockerDesktopInstaller.exe"; DestDir: "{app}\docker"; Flags: external skipifsourcedoesntexist ignoreversion
; Neither [Run] nor the engine launches this copied file.

[Run]
; Обязательный порядок после копирования файлов (эти записи намеренно НЕ имеют
; флага postinstall: пользователь не может отменить запуск приложения):
;   1) значок HR Manager в трее (управляющий компонент) — пользователь видит
;      состояние «Запускается / Готово / Ошибка» и кнопки действий;
;   2) движок выполняется СКРЫТО и пишет журнал в каталог состояния: проверяет
;      Windows/WSL2/виртуализацию/место/порт, проверяет и при необходимости
;      запускает уже установленный Docker Desktop, ждёт готовности Engine,
;      собирает и запускает контейнеры, затем открывает первый запуск в браузере.
; Docker Desktop движок не скачивает и не устанавливает. При его отсутствии
; установка приложения завершится честным отказом; мастер Finish и код выхода
; будут отличать копирование файлов от готовности HR Manager. Трей запускается
; отдельно и остаётся отзывчивым, пока установочный движок выполняется ниже.
;
; 64-БИТНЫЙ PowerShell обязателен: установщик 32-битный, и простой
; "powershell.exe" через WOW64-редирект запускает 32-битный PowerShell,
; а движок требует 64-разрядный (префлайт Test-HrmPowerShellVersion).
; SysNative — виртуальный каталог, из которого 32-битный процесс видит
; настоящий (64-битный) System32. Ярлыки [Icons] запускаются из 64-битного
; Explorer и в исправлении не нуждаются.
;
; -InstallDir и -StateDir передаются ЯВНО всем трём записям запуска (трей, движок,
; деинсталляция). Без них мастер и движок вычисляют пути независимо и расходятся:
; мастер пишет first-run-input.json/installed.json/setup-run.json по жёсткому
; HrmStateDir() = {localappdata}\HRManager, а движок берёт Get-HrmStateDir(),
; который уважает HRM_STATE_DIR, и Get-HrmDefaultInstallDir(), игнорируя /DIR.
; При расхождении фамилия не находится, Get-HrmFirstRunInput молча подставляет
; "Owner", и установка уходит с кодом 0, потеряв введённые данные. Живой дефект
; приёмки CI625: фамилия "Перепечай" в файле верна, а владелец создан как "Owner".
; Передача явного пути убирает возможность разойтись по построению, а не по договорённости.
Filename: "{win}\SysNative\WindowsPowerShell\v1.0\powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\infra\windows\hrm-tray.ps1"" -InstallDir ""{app}"" -StateDir ""{localappdata}\HRManager"""; \
  WorkingDir: "{app}"; \
  Flags: nowait runhidden

Filename: "{win}\SysNative\WindowsPowerShell\v1.0\powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\infra\windows\hr-manager.ps1"" -Action install -SourceDir ""{app}"" -InstallDir ""{app}"" -StateDir ""{localappdata}\HRManager"" -NonInteractive -OpenBrowser"; \
  WorkingDir: "{app}"; \
  StatusMsg: "HR Manager устанавливается (это может занять несколько минут)…"; \
  Flags: runhidden

; Наблюдатель канала обновлений НЕ запускается из установщика.
; Правдивое описание (ревизия стабильности 12.10.2026): supervisor его тоже не
; запускает — раньше в Common.psm1 жила функция-спавнер наблюдателя, которую не
; вызывал никто (мёртвый код, удалена), а этот комментарий утверждал обратное.
; Единственный путь запуска — явная команда человека или инструмента:
;   hr-manager.ps1 -Action channel -Watch
; Цикл наблюдателя ограничен (предел циклов/времени, файл-сигнал остановки
; channel-watch.stop, выход при замене pid-файла) — см. Start-HrmChannelWatch в
; engine\Channel.psm1 и раздел «Канал обновлений» в infra\windows\README.md.
; Одноразовая проверка канала без наблюдения: -Action channel (без -Watch).

[UninstallRun]
; Контейнеры останавливаются; данные Postgres и зашифрованные бэкапы
; СОХРАНЯЮТСЯ (тома pilot_pgdata/pilot_backups). Docker Desktop и WSL2
; не удаляются никогда. Тот же 64-битный PowerShell, что и в [Run] (см. выше).
Filename: "{win}\SysNative\WindowsPowerShell\v1.0\powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\infra\windows\hr-manager.ps1"" -Action uninstall -InstallDir ""{app}"" -StateDir ""{localappdata}\HRManager"" -NonInteractive"; \
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
  // Итог снимка прежней версии (см. PreservePreviousSnapshot): подтверждён
  // ли откат и что именно ответил вспомогательный скрипт — для журнала.
  SnapshotGuardPassed: Boolean;
  SnapshotStatus: String;
  SnapshotReason: String;
  SnapshotRunText: String;
  // Код возврата вспомогательного скрипта снимка: попадает в журнал мастера и в
  // файл диагностики (setup-snapshot.json) — по нему видно, почему установка
  // остановилась ДО перезаписи файлов.
  SnapshotExitCode: Integer;
  SnapshotDetailKeys: TArrayOfString;
  SnapshotDetailValues: TArrayOfString;

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
  StringChange(Result, #13, '\r');
  StringChange(Result, #10, '\n');
  StringChange(Result, #9, '\t');
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
  // UTF-8 без BOM: движок читает файл как UTF-8 (Get-HrmJsonFile ->
  // Get-Content -Encoding UTF8). SaveStringToFile без UTF8Encode пишет в
  // системной ANSI-кодировке, поэтому на русской Windows фамилия с кириллицей
  // приезжала в регистрацию кракозябрами, а исправить её потом было нельзя.
  SaveStringToFile(InputFile, UTF8Encode(Json), False);
end;

function HrmStateDir(): String;
begin
  Result := ExpandConstant('{localappdata}\HRManager');
end;

function HrmProgramFilesExist(): Boolean;
begin
  // Есть ли что СОХРАНЯТЬ: файлы программы, которые мастер сейчас перезапишет.
  // Запись установки (installed.json) для этого не годится: каталог состояния
  // движок намеренно оставляет после удаления программы, и запись может
  // существовать, когда файлов в {app} уже нет. Тогда снимок невозможен, а
  // установка обязана продолжаться — иначе переустановка после удаления была бы
  // невозможна, а «нулевой» снимок из пустого каталога ничего не защищает.
  Result := FileExists(ExpandConstant('{app}\release.json')) or
            FileExists(ExpandConstant('{app}\infra\compose.pilot.yml')) or
            FileExists(ExpandConstant('{app}\infra\windows\hr-manager.ps1'));
end;

function HrmSnapshotDiagnosticsFile(): String;
begin
  Result := HrmStateDir() + '\setup-snapshot.json';
end;

procedure HrmInitSnapshotDetails();
begin
  SetArrayLength(SnapshotDetailKeys, 9);
  SetArrayLength(SnapshotDetailValues, 9);
  SnapshotDetailKeys[0] := 'file_sha';
  SnapshotDetailValues[0] := '';
  SnapshotDetailKeys[1] := 'new_release_sha';
  SnapshotDetailValues[1] := '';
  SnapshotDetailKeys[2] := 'record_sha';
  SnapshotDetailValues[2] := '';
  SnapshotDetailKeys[3] := 'record_install_dir';
  SnapshotDetailValues[3] := '';
  SnapshotDetailKeys[4] := 'target_install_dir';
  SnapshotDetailValues[4] := '';
  SnapshotDetailKeys[5] := 'record_pilot_created';
  SnapshotDetailValues[5] := '';
  SnapshotDetailKeys[6] := 'message';
  SnapshotDetailValues[6] := '';
  SnapshotDetailKeys[7] := 'guard_branch';
  SnapshotDetailValues[7] := '';
  SnapshotDetailKeys[8] := 'new_build_digest';
  SnapshotDetailValues[8] := '';
  SnapshotDetailValues[4] := ExpandConstant('{app}');
  SnapshotDetailValues[5] := 'unknown';
end;

procedure HrmLogSnapshotDecision(const Needed: Boolean; const Status, Reason: String;
  const RunOk: Boolean; const RunExit: Integer; const Stop: Boolean);
// Решение мастера о снимке прежней версии одной строкой в журнале мастера
// (/LOG): журнал — единственный источник причины остановки, когда каталог
// состояния ещё не создан. Строка намеренно ASCII и со стабильными ключами,
// чтобы её можно было проверять автоматически.
var
  NeededText, RunOkText, StopText: String;
begin
  if Needed then NeededText := '1' else NeededText := '0';
  if RunOk then RunOkText := '1' else RunOkText := '0';
  if Stop then StopText := '1' else StopText := '0';
  Log('HRM: snapshot decision needed=' + NeededText + ' status=' + Status +
      ' reason=' + Reason + ' run_ok=' + RunOkText + ' exit=' + IntToStr(RunExit) +
      ' stop=' + StopText);
end;

procedure HrmWriteSnapshotDiagnostics(const Needed: Boolean; const Status, Reason: String;
  const RunOk: Boolean; const RunExit: Integer; const Stop: Boolean);
// Человекочитаемая диагностика для владельца и support bundle. Каталог
// состояния здесь НЕ создаётся: права на него выставляет движок, а мастер не
// имеет права создать каталог раньше с ослабленными правами. Если каталога ещё
// нет (чистая установка), решение всё равно видно в журнале мастера.
var
  FileName, Json, NeededText, RunOkText, StopText: String;
  I: Integer;
begin
  if GetArrayLength(SnapshotDetailKeys) = 0 then HrmInitSnapshotDetails();
  HrmLogSnapshotDecision(Needed, Status, Reason, RunOk, RunExit, Stop);
  for I := 0 to GetArrayLength(SnapshotDetailKeys) - 1 do
    Log('HRM: snapshot detail ' + SnapshotDetailKeys[I] + '=' + SnapshotDetailValues[I]);
  if not DirExists(HrmStateDir()) then Exit;
  if Needed then NeededText := 'true' else NeededText := 'false';
  if RunOk then RunOkText := 'true' else RunOkText := 'false';
  if Stop then StopText := 'true' else StopText := 'false';
  Json := '{"schema": "hrm-setup-snapshot-1", "at": "' +
          GetDateTimeString('yyyy/mm/dd hh:nn:ss', '-', ':') + '", "needed": ' +
          NeededText + ', "status": "' + Status + '", "reason": "' + Reason +
          '", "run_ok": ' + RunOkText + ', "run_exit": ' + IntToStr(RunExit) +
          ', "stop": ' + StopText;
  for I := 0 to GetArrayLength(SnapshotDetailKeys) - 1 do
  begin
    if (SnapshotDetailKeys[I] = 'record_pilot_created') and
       ((SnapshotDetailValues[I] = 'true') or (SnapshotDetailValues[I] = 'false')) then
      Json := Json + ', "' + SnapshotDetailKeys[I] + '": ' + SnapshotDetailValues[I]
    else
      Json := Json + ', "' + SnapshotDetailKeys[I] + '": "' +
              EscapeJson(SnapshotDetailValues[I]) + '"';
  end;
  Json := Json + '}';
  FileName := HrmSnapshotDiagnosticsFile();
  if not SaveStringToFile(FileName, UTF8Encode(Json), False) then
    Log('HRM: файл диагностики снимка не записан: ' + FileName);
end;

function HrmReadResultKey(const DataFile, Key: String): String;
// Читает строку key=value из файла результата вспомогательного скрипта.
// Содержимое — только коды, хеши и статусы (секретов там нет).
var
  Lines: TArrayOfString;
  I, P: Integer;
  Line, K, V: String;
begin
  Result := '';
  if not FileExists(DataFile) then Exit;
  if not LoadStringsFromFile(DataFile, Lines) then Exit;
  for I := 0 to GetArrayLength(Lines) - 1 do
  begin
    Line := Lines[I];
    if (Length(Line) > 0) and (Ord(Line[1]) = 65279) then
      Line := Copy(Line, 2, Length(Line) - 1);
    if (Length(Line) > 0) and (Line[Length(Line)] = #13) then
      Line := Copy(Line, 1, Length(Line) - 1);
    P := Pos('=', Line);
    if P > 1 then
    begin
      K := Copy(Line, 1, P - 1);
      V := Copy(Line, P + 1, Length(Line) - P);
      if K = Key then
      begin
        Result := V;
        Exit;
      end;
    end;
  end;
end;

function HrmReadResultKeySafe(const DataFile, Key: String): String;
// Чтение файла результата для путей БЕЗ обёртки try/except. Ошибка чтения не
// имеет права прерывать установку «техническим» сообщением: пустой статус
// означает «снимок не подтверждён», и мастер честно останавливается.
begin
  try
    Result := HrmReadResultKey(DataFile, Key);
  except
    Log('HRM: файл результата снимка не прочитан: ' + GetExceptionMessage);
    Result := '';
  end;
end;

function HrmExtractSnapshotHelper(): Boolean;
// Вспомогательные файлы снимка распаковываются из СВОЕГО пакета (флаг
// dontcopy), а не берутся у установленного движка: прежняя версия может не
// знать ни действия snapshot-previous, ни формата снимка — и тогда обновление
// молча перезаписало бы файлы без возможности отката (дефект ревью 15).
// При solid compression эти записи стоят первыми в [Files], поэтому распаковка
// не тянет за собой остальной пакет.
begin
  Result := False;
  try
    ExtractTemporaryFile('hrm-snapshot.ps1');
    ExtractTemporaryFile('Common.psm1');
    ExtractTemporaryFile('Install.psm1');
    ExtractTemporaryFile('Secrets.psm1');
    ExtractTemporaryFile('Snapshot.psm1');
    // release.json пакета: по нему CLI отличает «в {app} прежняя версия» от
    // «мастер уже разложил файлы новой версии».
    ExtractTemporaryFile('release.json');
    // Распаковка обязана быть проверена: CLI ждёт модули рядом с собой, и
    // «извлеклось что-то не туда» обязано остановить установку, а не выясниться
    // в момент, когда файлы {app} уже перезаписаны.
    Result := FileExists(ExpandConstant('{tmp}\hrm-snapshot.ps1')) and
              FileExists(ExpandConstant('{tmp}\Common.psm1')) and
              FileExists(ExpandConstant('{tmp}\Install.psm1')) and
              FileExists(ExpandConstant('{tmp}\Secrets.psm1')) and
              FileExists(ExpandConstant('{tmp}\Snapshot.psm1'));
    if not Result then
      Log('HRM: вспомогательные файлы снимка распакованы не полностью.');
  except
    Log('HRM: вспомогательные файлы снимка не распакованы: ' + GetExceptionMessage);
    Result := False;
  end;
end;

function HrmRunSnapshotHelper(const ResultFile: String): Boolean;
// Запускает hrm-snapshot.ps1 и читает КОД ВОЗВРАТА процесса: успех — только 0.
var
  Params: String;
  ResultCode: Integer;
begin
  Result := False;
  ResultCode := -1;
  Params := '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' +
    ExpandConstant('{tmp}\hrm-snapshot.ps1') + '" -InstallDir "' + ExpandConstant('{app}') +
    '" -StateDir "' + HrmStateDir() + '" -ResultFile "' + ResultFile + '"';
  try
    if Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Params,
        ExpandConstant('{tmp}'), SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    begin
      SnapshotExitCode := ResultCode;
      Result := (ResultCode = 0)
    end
    else
    begin
      SnapshotExitCode := ResultCode;
      Log('HRM: подготовка снимка не запустилась (код ' + IntToStr(ResultCode) + ').');
    end;
  except
    Log('HRM: запуск подготовки снимка прерван: ' + GetExceptionMessage);
    Result := False;
  end;
end;

function PreservePreviousSnapshot(): String;
// Снимок прежней версии ДО копирования файлов. Возвращает '' — можно
// продолжать; иначе текст ошибки, и установка останавливается (файлы {app}
// ещё не изменены). Причина отказа попадает в журнал мастера (/LOG) и, когда
// каталог состояния уже есть, в файл диагностики setup-snapshot.json.
var
  ResultFile, Status, Reason, Message: String;
  I: Integer;
  Needed, RunOk: Boolean;
begin
  Result := '';
  if SnapshotGuardPassed then Exit;
  HrmInitSnapshotDetails();
  Needed := HrmProgramFilesExist();
  ResultFile := ExpandConstant('{tmp}\hrm-snapshot-result.txt');
  SnapshotStatus := '';
  SnapshotReason := '';
  SnapshotExitCode := -1;
  if not Needed then
  begin
    // Файлов программы нет (первая установка или переустановка после удаления):
    // сохранять нечего, поэтому вспомогательный скрипт НЕ запускается вообще —
    // на чистой машине ничто не должно влиять на установку.
    SnapshotStatus := 'skipped';
    SnapshotReason := 'no_program_files';
    SnapshotGuardPassed := True;
    HrmWriteSnapshotDiagnostics(False, SnapshotStatus, SnapshotReason, False, -1, False);
    Exit;
  end;
  if not HrmExtractSnapshotHelper() then
  begin
    // Распаковка вспомогательных файлов не удалась, а прежняя версия ЕСТЬ:
    // продолжать нельзя — иначе файлы были бы перезаписаны без возможности
    // отката.
    SnapshotStatus := 'failed';
    SnapshotReason := 'helper_extract_failed';
    HrmWriteSnapshotDiagnostics(True, SnapshotStatus, SnapshotReason, False, -1, True);
    SuppressibleMsgBox(CustomMessage('SnapshotHelperFailed'), mbCriticalError, MB_OK, IDOK);
    Result := CustomMessage('SnapshotStop');
    Exit;
  end;
  DeleteFile(ResultFile);
  Log('HRM: сохраняем снимок предыдущей версии до копирования файлов.');
  RunOk := HrmRunSnapshotHelper(ResultFile);
  Status := HrmReadResultKeySafe(ResultFile, 'status');
  Reason := HrmReadResultKeySafe(ResultFile, 'reason');
  for I := 0 to GetArrayLength(SnapshotDetailKeys) - 1 do
    SnapshotDetailValues[I] := HrmReadResultKeySafe(ResultFile, SnapshotDetailKeys[I]);
  SnapshotStatus := Status;
  SnapshotReason := Reason;
  if RunOk then
    SnapshotRunText := 'успех'
  else
    SnapshotRunText := 'сбой';
  Log('HRM: снимок предыдущей версии: запуск=' + SnapshotRunText +
      ' status=' + Status + ' reason=' + Reason);
  // Продолжать можно ТОЛЬКО при нулевом коде возврата И подтверждённом
  // результате: молчание вспомогательного скрипта — не разрешение.
  if RunOk and ((Status = 'verified') or (Status = 'skipped')) then
  begin
    SnapshotGuardPassed := True;
    HrmWriteSnapshotDiagnostics(True, Status, Reason, True, SnapshotExitCode, False);
    Exit;
  end;
  if Reason = 'files_replaced' then
    Message := CustomMessage('SnapshotFilesReplaced')
  else if Status = '' then
    Message := CustomMessage('SnapshotHelperFailed')
  else
    Message := CustomMessage('SnapshotFailed') + #13#10 +
      'Причина: ' + Status + ' / ' + Reason;
  if SnapshotDetailValues[6] <> '' then
    Message := Message + #13#10 + SnapshotDetailValues[6];
  Log('HRM: установка остановлена ДО перезаписи файлов (' + Status + '/' + Reason + ').');
  HrmWriteSnapshotDiagnostics(True, Status, Reason, RunOk, SnapshotExitCode, True);
  SuppressibleMsgBox(Message, mbCriticalError, MB_OK, IDOK);
  Result := CustomMessage('SnapshotStop');
end;

function HrmVerifySnapshotGuard(): String;
// Последняя проверка перед самим копированием (PrepareToInstall и ssInstall):
// подтверждённый снимок прежней версии либо отсутствие того, что можно потерять.
var
  Status: String;
begin
  Result := '';
  if SnapshotGuardPassed then Exit;
  if not HrmProgramFilesExist() then
  begin
    SnapshotGuardPassed := True;
    Exit;
  end;
  Status := HrmReadResultKeySafe(ExpandConstant('{tmp}\hrm-snapshot-result.txt'), 'status');
  if (Status = 'verified') or (Status = 'skipped') then
  begin
    SnapshotGuardPassed := True;
    Exit;
  end;
  Log('HRM: snapshot guard status=' + Status + ' stop=1');
  HrmWriteSnapshotDiagnostics(True, Status, 'guard_not_confirmed', False, SnapshotExitCode, True);
  Result := CustomMessage('SnapshotStop');
end;

procedure WriteSetupMarker();
var
  StateDir, MarkerFile, Marker: String;
begin
  // Отметка «идёт установка»: значок в трее, который мастер запускает сразу
  // после копирования файлов, показывает ход установки и НЕ выходит с ошибкой
  // «HR Manager не установлен» (дефект P2 ревью). Движок обновляет отметку по
  // ходу установки и снимает её при успешном завершении.
  StateDir := ExpandConstant('{localappdata}\HRManager');
  if not ForceDirectories(StateDir) then
    RaiseException('Не удалось создать каталог состояния HR Manager для отметки установки.');
  MarkerFile := StateDir + '\setup-run.json';
  // JSON Unicode escapes, как и в MarkHrmSetupEngineFailed: файл остаётся
  // чистым ASCII на диске. SaveStringToFile без UTF8Encode писал бы русский
  // текст в системной ANSI-кодировке, а движок читает этот же файл как UTF-8
  // (Get-HrmSetupMarker -> Get-HrmJsonFile) и видел бы «Файл состояния
  // установки повреждён».
  Marker := '{"status": "running", "message": "\u0423\u0441\u0442\u0430\u043d\u0430\u0432\u043b\u0438\u0432\u0430\u0435\u043c HR Manager\u2026"}';
  if not SaveStringToFile(MarkerFile, Marker, False) then
    RaiseException('Не удалось записать отметку установки HR Manager.');
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  GuardError: String;
begin
  if CurStep = ssInstall then
  begin
    // Последняя проверка перед копированием файлов: без подтверждённого снимка
    // прежней версии обновление не начинается (исключение прерывает установку).
    GuardError := HrmVerifySnapshotGuard();
    if GuardError <> '' then
      RaiseException(GuardError);
    // Файл ввода и отметку установки пишем ЗДЕСЬ, в ssInstall. Секция [Run]
    // выполняется РАНЬШЕ события CurStepChanged(ssPostInstall): в компиляторе
    // Inno сначала ProcessRunEntries, и только потом SetStep(ssPostInstall, True).
    // Пока записи жили в ssPostInstall, порядок был обратным ожидаемому:
    //   - движок из [Run] читал first-run-input.json ДО его записи, не находил
    //     фамилию и молча подставлял "Owner" (Get-HrmFirstRunInput);
    //   - WriteSetupMarker затирал честный итог движка словом "running".
    // Мастер отдавал код 10 при живом приложении, а владелец создавался под
    // чужим именем. Живой дефект приёмки 21.10.2026: фамилия "Перепечай" в
    // файле верна, а владелец создан как "Owner".
    WriteFirstRunInput();
    WriteSetupMarker();
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  GuardError: String;
begin
  // Порядок обязателен и проверяется статикой тестов: СНАЧАЛА снимок прежней
  // версии, и только потом последняя проверка. Обратный порядок (проверка
  // раньше снимка) останавливал бы ЛЮБОЕ обновление существующей установки ещё
  // до того, как снимок вообще попытались создать.
  Result := PreservePreviousSnapshot();
  if Result <> '' then Exit;
  // Подготовка завершается ДО копирования файлов: если подтверждённого снимка
  // прежней версии нет, установка останавливается на этой странице — и в
  // обычном, и в неинтерактивном режиме (Inno завершает установку с отдельным
  // кодом возврата, поэтому «остановка» видна и автоматике).
  GuardError := HrmVerifySnapshotGuard();
  if GuardError <> '' then
    Result := GuardError;
end;

function GetHrmSetupEngineStatus(): String;
var
  MarkerFile, Line: String;
  Lines: TArrayOfString;
  I: Integer;
begin
  // Success is only the engine's successful removal of its marker. A remaining
  // failed/running/unreadable marker is incomplete, never a successful launch.
  Result := 'unknown';
  if not DirExists(HrmStateDir()) then Exit;
  MarkerFile := HrmStateDir() + '\setup-run.json';
  if not FileExists(MarkerFile) then
  begin
    Result := 'success';
    Exit;
  end;
  try
    if not LoadStringsFromFile(MarkerFile, Lines) then Exit;
    for I := 0 to GetArrayLength(Lines) - 1 do
    begin
      Line := Lowercase(Trim(Lines[I]));
      if Pos('"status"', Line) > 0 then
      begin
        if Pos('"failed"', Line) > 0 then
          Result := 'failed'
        else if Pos('"running"', Line) > 0 then
          Result := 'running'
        else
          Result := 'unknown';
        Exit;
      end;
    end;
  except
    Result := 'unknown';
  end;
end;

procedure MarkHrmSetupEngineFailed();
var
  MarkerFile, Marker: String;
begin
  // The Run section has finished by this hook. If the engine left its marker at
  // "running" (for example, its error handler could not replace Inno's initial
  // marker), do not leave the tray blocked for the 30-minute stale-marker window.
  // JSON Unicode escapes keep this marker ASCII on disk while preserving the
  // Russian user-facing message when the engine reads it as UTF-8.
  if not DirExists(HrmStateDir()) then Exit;
  MarkerFile := HrmStateDir() + '\setup-run.json';
  Marker := '{"status":"failed","message":"\u0414\u0432\u0438\u0436\u043e\u043a \u0443\u0441\u0442\u0430\u043d\u043e\u0432\u043a\u0438 \u0437\u0430\u0432\u0435\u0440\u0448\u0438\u043b\u0441\u044f \u0431\u0435\u0437 \u0438\u0442\u043e\u0433\u043e\u0432\u043e\u0433\u043e \u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442\u0430. \u041f\u0440\u0438\u0447\u0438\u043d\u0430 \u043d\u0435 \u0443\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d\u0430; \u043f\u0440\u043e\u0432\u0435\u0440\u044c\u0442\u0435 \u0436\u0443\u0440\u043d\u0430\u043b\u044b \u0432 \u043a\u0430\u0442\u0430\u043b\u043e\u0433\u0435 \u0441\u043e\u0441\u0442\u043e\u044f\u043d\u0438\u044f HRManager/logs \u0438 \u0441\u043e\u0437\u0434\u0430\u0439\u0442\u0435 \u043e\u0442\u0447\u0451\u0442 \u0434\u043b\u044f \u043f\u043e\u0434\u0434\u0435\u0440\u0436\u043a\u0438. \u0414\u0430\u043d\u043d\u044b\u0435 \u043d\u0435 \u0443\u0434\u0430\u043b\u044f\u043b\u0438\u0441\u044c."}';
  if SaveStringToFile(MarkerFile, Marker, False) then
    Log('HRM: incomplete setup marker normalized to failed for tray recovery')
  else
    Log('HRM: failed to normalize incomplete setup marker; engine status=' + GetHrmSetupEngineStatus());
end;

function GetCustomSetupExitCode(): Integer;
var
  EngineStatus: String;
begin
  // Supported Inno Setup hook: only called after Setup itself has succeeded.
  // 10 means files were copied but the HR Manager engine did not confirm ready.
  EngineStatus := GetHrmSetupEngineStatus();
  Result := 0;
  if EngineStatus <> 'success' then
  begin
    Result := 10;
    // The engine normally writes status=failed itself. This post-Run fallback
    // covers an orphaned status=running marker, so the tray exposes its retry
    // action as soon as Setup returns instead of treating a finished failure as
    // a still-running installation.
    if (EngineStatus = 'running') or (EngineStatus = 'unknown') then
    begin
      MarkHrmSetupEngineFailed();
      EngineStatus := GetHrmSetupEngineStatus();
    end;
  end;
  Log('HRM: setup engine result=' + EngineStatus + ' custom exit=' + IntToStr(Result));
end;

procedure CurPageChanged(CurPageID: Integer);
var
  EngineStatus: String;
begin
  if CurPageID = wpFinished then
  begin
    // [Run] уже завершён. Нормализуем ДО Finish, а не только при выходе:
    // оборванный/не загрузившийся движок не должен оставлять status=running.
    EngineStatus := GetHrmSetupEngineStatus();
    if (EngineStatus = 'running') or (EngineStatus = 'unknown') then
    begin
      MarkHrmSetupEngineFailed();
      EngineStatus := GetHrmSetupEngineStatus();
    end;
    if EngineStatus = 'success' then
    begin
      WizardForm.FinishedLabel.Caption := ExpandConstant('{cm:FinishText}');
      WizardForm.FinishedHeadingLabel.Caption := ExpandConstant('{cm:FinishTitle}');
    end
    else
    begin
      WizardForm.FinishedLabel.Caption := ExpandConstant('{cm:FinishIncompleteText}');
      WizardForm.FinishedHeadingLabel.Caption := ExpandConstant('{cm:FinishIncompleteTitle}');
      Log('HRM: Finish page reports incomplete engine status=' + EngineStatus);
    end;
  end;
end;
