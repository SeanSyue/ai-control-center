; Inno Setup script for AI Control Center.
;
; Prerequisite: build the PyInstaller onedir bundle first (this script
; packages dist\AI Control Center\, it does not invoke PyInstaller itself):
;   .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --distpath dist --workpath build "packaging\AI Control Center.spec"
;
; Then compile this script (from the repo root):
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" "packaging\AI Control Center.iss"
;
; Output: packaging\Output\AI Control Center Setup.exe

#define MyAppName "AI Control Center"
#define MyAppVersion "0.9.0.1"
#define MyAppPublisher "Sean"
#define MyAppExeName "AI Control Center.exe"

[Setup]
; Keep this GUID stable across every future rebuild of this installer -
; Windows uses it (not the app name) to recognize "this is an upgrade of
; the same product" for Add/Remove Programs and silent uninstall/repair
; logic. Only change it if you deliberately want a new install to sit
; side-by-side with an old one instead of upgrading it.
AppId={{240CBC11-5297-4DE6-ADF5-C0C5E3811268}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=AI Control Center Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; PyInstaller's --onedir output for a PySide6 app is 64-bit only.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; The app writes its own project registry/config under %APPDATA%, not under
; the install directory, so a non-admin per-user install is fine and avoids
; a UAC prompt entirely.
PrivilegesRequired=lowest
; Ask Windows to close a running copy before installing. Without it the
; rename-aside backup in [Code] fails on a locked file, and the user gets the
; "cannot back up" prompt for what is really just "the app is open".
CloseApplications=yes
RestartApplications=no
; Built by tools/make_icon.py from icon-source.png. SetupIconFile is the installer's own icon;
; UninstallDisplayIcon is what Add/Remove Programs shows, and it has to point
; at the installed exe rather than at this file, which is not installed.
SetupIconFile=aicc.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
; Stamped into the exe's file properties, so Explorer's Details tab and any
; future update check see the same number as AppVersion above (which Inno
; writes to the registry, and InitializeSetup reads back to tell a fresh
; install from an upgrade or a downgrade).
VersionInfoVersion={#MyAppVersion}

[Languages]
; Inno Setup 6 ships no official Traditional Chinese file. Rather than depend
; on an unverified community .isl, the English file stays as the base and the
; [Messages] section below overrides the strings the user actually reads. Every
; key here exists in Default.isl - a typo'd key is silently ignored by the
; compiler, so any new one must be copied from it rather than invented.
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
SetupAppTitle=安裝
SetupWindowTitle=安裝 - %1
WelcomeLabel1=歡迎安裝 [name]
WelcomeLabel2=接下來會把 [name/ver] 安裝到這台電腦。%n%n建議先關閉其他應用程式再繼續。
ClickNext=按「下一步」繼續，或按「取消」結束安裝。
SelectDirDesc=請選擇 [name] 要安裝到哪裡。
SelectDirLabel3=安裝程式會把 [name] 安裝到下面這個資料夾。
SelectDirBrowseLabel=按「下一步」繼續。要改用其他資料夾請按「瀏覽」。
DiskSpaceGBLabel=至少需要 [gb] GB 可用磁碟空間。
DiskSpaceMBLabel=至少需要 [mb] MB 可用磁碟空間。
SelectTasksDesc=請選擇要執行的額外工作。
SelectTasksLabel2=請選擇安裝 [name] 時要一併執行的工作，然後按「下一步」。
ReadyLabel1=安裝程式已準備好把 [name] 安裝到這台電腦。
ReadyLabel2a=按「安裝」開始安裝，或按「上一步」檢查／修改設定。
InstallingLabel=正在安裝 [name]，請稍候。
FinishedHeadingLabel=[name] 安裝完成
FinishedLabel=[name] 已安裝到這台電腦，可以從捷徑開啟。
ClickFinish=按「完成」結束安裝程式。
ExitSetupTitle=結束安裝
ExitSetupMessage=安裝尚未完成。現在結束的話 [name] 不會被安裝。%n%n確定要結束嗎？
ConfirmUninstall=確定要完整移除 %1 及其所有元件嗎？
UninstallStatusLabel=正在從這台電腦移除 %1，請稍候。
ButtonNext=下一步(&N) >
ButtonBack=< 上一步(&B)
ButtonInstall=安裝(&I)
ButtonCancel=取消
ButtonFinish=完成(&F)
ButtonBrowse=瀏覽(&R)...
ButtonYes=是(&Y)
ButtonNo=否(&N)

[Tasks]
Name: "desktopicon"; Description: "建立桌面捷徑 (Create a desktop shortcut)"; GroupDescription: "額外圖示 (Additional icons):"

[Files]
; /* pulls in every file and subfolder under the PyInstaller onedir output.
Source: "..\dist\AI Control Center\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "使用手冊.html"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\使用手冊"; Filename: "{app}\使用手冊.html"
Name: "{group}\解除安裝 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\使用手冊.html"; Description: "開啟使用手冊 (Open the user manual)"; Flags: postinstall shellexec skipifsilent
Filename: "{app}\{#MyAppExeName}"; Description: "啟動 {#MyAppName} (Launch {#MyAppName})"; Flags: nowait postinstall skipifsilent

[Code]
{ ---------------------------------------------------------------------------
  Version comparison and rollback.

  Upgrading used to be a blind overwrite: the new files were copied on top of
  the old ones with no version check, and a failure part-way through left a
  half-new, half-old install with no way back.

  Keep the old directory under a unique backup name. A product executable
  is only a recognition marker: user files may live beside it, so setup never
  recursively deletes a previous or partially installed directory. Failed
  upgrades preserve the partial directory before attempting to restore the old
  one. This is a file-tree recovery, not a transaction over registry/shortcuts.
  --------------------------------------------------------------------------- }

var
  BackupDir: String;
  BackupMade: Boolean;
  InstallCompleted: Boolean;

function InstalledVersion(): String;
var
  Value: String;
begin
  Result := '';
  { Inno writes this itself for AppId; reading it is how the wizard knows
    whether this is a fresh install, an upgrade, or a downgrade. }
  if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#emit SetupSetting("AppId")}_is1',
                         'DisplayVersion', Value) then
    Result := Value
  else if RegQueryStringValue(HKLM, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#emit SetupSetting("AppId")}_is1',
                              'DisplayVersion', Value) then
    Result := Value;
end;

function InitializeSetup(): Boolean;
var
  Existing: String;
  Comparison: Integer;
begin
  Result := True;
  Existing := InstalledVersion();
  if Existing = '' then
    Exit;

  Comparison := CompareStr(Existing, '{#MyAppVersion}');
  if Existing = '{#MyAppVersion}' then
    MsgBox('這台電腦已經安裝 {#MyAppName} {#MyAppVersion}（相同版本）。' + #13#10 +
           '繼續會重新安裝一次，設定與專案不會被動到。',
           mbInformation, MB_OK)
  else if Comparison > 0 then
  begin
    { A newer version is already installed. Overwriting it with an older one
      is almost always a mistake, so it needs an explicit yes. }
    Result := MsgBox('這台電腦已安裝較新的版本 ' + Existing + '。' + #13#10 +
                     '確定要降級為 {#MyAppVersion} 嗎？',
                     mbConfirmation, MB_YESNO) = IDYES;
  end
  else
    MsgBox('將把 {#MyAppName} 從 ' + Existing + ' 更新為 {#MyAppVersion}。' + #13#10 +
           '設定、專案與共同記憶都不會被動到（它們存在 %APPDATA%，不在安裝資料夾裡）。',
           mbInformation, MB_OK);
end;

{ ---------------------------------------------------------------------------
  Recognize likely AICC installs, without treating that as ownership of every file.

  Until 2026-09-25 the rename-aside above ran on whatever directory the user
  picked, as long as it existed: pick an existing folder by mistake and it was
  renamed to <folder>.previous and deleted once setup finished, and an existing
  <folder>.previous was deleted before that (external review A12).

  The executable allows older installations to be recognized. It does not
  prove ownership of neighboring files; backups must always be retained.
  --------------------------------------------------------------------------- }

function IsAiccInstall(Dir: String): Boolean;
begin
  Result := FileExists(AddBackslash(Dir) + '{#MyAppExeName}');
end;

function DirIsEmpty(Dir: String): Boolean;
var
  Found: TFindRec;
begin
  Result := True;
  if FindFirst(AddBackslash(Dir) + '*', Found) then
  begin
    try
      repeat
        if (Found.Name <> '.') and (Found.Name <> '..') then
        begin
          Result := False;
          Exit;
        end;
      until not FindNext(Found);
    finally
      FindClose(Found);
    end;
  end;
end;

function NotOursAndNotEmpty(Dir: String): Boolean;
begin
  Result := DirExists(Dir) and (not IsAiccInstall(Dir)) and (not DirIsEmpty(Dir));
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  { Refuse on the page where the folder is chosen, so the user can pick
    another one instead of being stopped half-way through. }
  { WizardDirValue, not the app constant: on this page the constant may not
    hold the new choice yet. }
  if (CurPageID = wpSelectDir) and NotOursAndNotEmpty(WizardDirValue()) then
  begin
    MsgBox('這個資料夾裡已經有別的東西，而且不是 {#MyAppName} 的安裝：' + #13#10 +
           WizardDirValue() + #13#10 + #13#10 +
           '為了不動到你的檔案，請選一個空的資料夾，或新的資料夾名稱。',
           mbError, MB_OK);
    Result := False;
  end;
end;

function UnusedSibling(Dir, Suffix: String): String;
var
  Index: Integer;
begin
  Result := Dir + Suffix;
  Index := 0;
  while DirExists(Result) or FileExists(Result) do
  begin
    Index := Index + 1;
    Result := Dir + Suffix + '.' + IntToStr(Index);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
  begin
    BackupMade := False;
    InstallCompleted := False;
    { Silent installs must respect the same rule as the folder page. }
    if NotOursAndNotEmpty(ExpandConstant('{app}')) then
    begin
      SuppressibleMsgBox('安裝資料夾裡已經有別的東西，而且不是 {#MyAppName} 的安裝，已停止：' + #13#10 +
                         ExpandConstant('{app}'), mbError, MB_OK, IDOK);
      Abort();
    end;
    if IsAiccInstall(ExpandConstant('{app}')) then
    begin
      BackupDir := UnusedSibling(ExpandConstant('{app}'), '.previous');
      BackupMade := RenameFile(ExpandConstant('{app}'), BackupDir);
      if not BackupMade then
      begin
        SuppressibleMsgBox('無法保留現有版本（程式可能還開著）。為了避免覆蓋原有檔案，安裝已停止。',
                           mbError, MB_OK, IDOK);
        Abort();
      end;
    end;
  end
  else if CurStep = ssPostInstall then
  begin
    InstallCompleted := True;
    if BackupMade then
    begin
      Log('Previous installation retained at: ' + BackupDir);
      SuppressibleMsgBox('先前版本與原有檔案已保留在：' + #13#10 + BackupDir + #13#10 +
                         '確認新版可用並取回需要的檔案後，可自行清理這份備份。',
                         mbInformation, MB_OK, IDOK);
    end;
  end;
end;

procedure DeinitializeSetup();
var
  PartialDir: String;
begin
  PartialDir := '';
  if InstallCompleted or (not BackupMade) then
    Exit;
  { Preserve any partial directory as well; never delete unknown user files
    to make room for rollback. A name collision makes RenameFile fail closed. }
  if DirExists(ExpandConstant('{app}')) then
  begin
    PartialDir := UnusedSibling(ExpandConstant('{app}'), '.incomplete');
    if not RenameFile(ExpandConstant('{app}'), PartialDir) then
    begin
      SuppressibleMsgBox('更新未完成，無法移開未完成的資料夾。先前版本仍完整保留在：' + #13#10 +
                         BackupDir, mbError, MB_OK, IDOK);
      Exit;
    end;
    Log('Incomplete installation retained at: ' + PartialDir);
  end;
  if RenameFile(BackupDir, ExpandConstant('{app}')) then
    SuppressibleMsgBox('更新沒有完成，已回復先前安裝資料夾。未完成的檔案（若有）保留在：' + #13#10 +
                       PartialDir, mbInformation, MB_OK, IDOK)
  else
    SuppressibleMsgBox('更新沒有完成，而且資料夾回復失敗。先前版本仍保留在：' + #13#10 +
                       BackupDir + #13#10 + '請確認檔案後再手動回復。', mbError, MB_OK, IDOK);
end;
