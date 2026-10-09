; 界野电商平台（MainPG）安装程序定义
; 编译：ISCC.exe mainpg-installer.iss
; 前提：先运行 build_installer.ps1 生成 dist\MainPG（PyInstaller onedir）
; 注意：本文件含中文，编译时需先转换为 UTF-8 带 BOM（build_installer.ps1 已处理）

#define MyAppName "界野电商平台"
#define MyAppNameEn "MainPG"
#ifndef MyAppVersion
  #define MyAppVersion "1.1.0"
#endif
#ifndef MySetupBaseFilename
  #define MySetupBaseFilename "MainPG-Setup-" + MyAppVersion
#endif
#define MyAppPublisher "界野"
#define MyAppExeName "MainPG.exe"

[Setup]
AppId={{7F2E8B1A-6D26-4C9A-A360-9FBC2B84BF06}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\{#MyAppNameEn}
; 始终显示"安装位置"页，允许用户自定义安装目录（含覆盖升级时）
DisableDirPage=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Uninstallable=yes
OutputDir=dist
OutputBaseFilename={#MySetupBaseFilename}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=app-icon.ico

; 升级时清掉旧版本残留的程序文件（_internal），保证新旧版本文件不会混在一起
[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\build"
Type: files; Name: "{app}\MainPG.exe"

[Files]
Source: "dist\{#MyAppNameEn}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

; 桌面 + 开始菜单快捷方式：双击即启动，无需进文件夹找 exe
[Icons]
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\app-icon.ico"
Name: "{userprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\app-icon.ico"

; 数据根目录键（DataRoot 值由 [Code] 在安装收尾写入）；卸载时一并清理
[Registry]
Root: HKCU; Subkey: "Software\{#MyAppNameEn}"; ValueType: none; Flags: uninsdeletekey

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent
Filename: "{app}\{#MyAppExeName}"; Flags: nowait skipifnotsilent

[Code]
var
  DataDirPage: TInputDirWizardPage;

const
  DataRootRegSubKey = 'Software\MainPG';
  DataRootRegValueName = 'DataRoot';

// 安装前自动结束正在运行的旧版 MainPG.exe，避免文件占用导致升级不完整
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  Exec('taskkill.exe', '/F /IM MainPG.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(1200);
end;

// 新增"数据存放位置"向导页：让用户把数据放到非系统盘，避免 C 盘被用户数据撑满。
// 默认 {userappdata}\MainPG，即与历史版本一致的位置——保持默认则行为零变化。
procedure InitializeWizard();
begin
  DataDirPage := CreateInputDirPage(
    wpSelectDir,
    '选择数据存放位置',
    '请选择本软件保存用户数据的位置',
    '本软件会把数据库、日志、缓存、导出文件等数据保存在下面的目录中。' + #13#10 +
    '为避免系统盘（C 盘）空间被占满，建议选择空间充足的非系统盘目录。' + #13#10 +
    '若保持默认，数据将保存在当前用户的应用数据目录中。',
    False, '');
  DataDirPage.Add('数据存放目录：');
  DataDirPage.Values[0] := ExpandConstant('{userappdata}\MainPG');
end;

// 向导"下一步"校验：确保所选目录可创建、可写入，避免装完启动才报错。
function NextButtonClick(CurPageID: Integer): Boolean;
var
  Chosen: String;
begin
  Result := True;
  if (DataDirPage <> nil) and (CurPageID = DataDirPage.ID) then
  begin
    Chosen := Trim(DataDirPage.Values[0]);
    if Chosen = '' then
    begin
      MsgBox('请选择数据存放目录。', mbError, MB_OK);
      Result := False;
      Exit;
    end;
    if not DirExists(Chosen) then
    begin
      if not CreateDir(Chosen) then
      begin
        MsgBox('无法创建目录：' + Chosen + #13#10 + '请换一个位置或检查是否有写入权限。', mbError, MB_OK);
        Result := False;
        Exit;
      end;
    end;
    if not SaveStringToFile(AddBackslash(Chosen) + '.write-test', 'ok', False) then
    begin
      MsgBox('该目录不可写：' + Chosen + #13#10 + '请选择有写入权限的目录。', mbError, MB_OK);
      Result := False;
      Exit;
    end;
    DeleteFile(AddBackslash(Chosen) + '.write-test');
  end;
end;

// 安装收尾：把用户选择的数据根目录写入 HKCU 注册表，供主程序 / 启动器读取。
// 静默安装（自动更新 /VERYSILENT）不覆盖用户既有选择，保证老用户数据位置不变。
procedure CurStepChanged(CurStep: TSetupStep);
var
  Existing: String;
  Chosen: String;
begin
  if CurStep = ssPostInstall then
  begin
    Chosen := '';
    if DataDirPage <> nil then
      Chosen := Trim(DataDirPage.Values[0]);
    if Chosen = '' then
      Chosen := ExpandConstant('{userappdata}\MainPG');
    if (not WizardSilent) or
       (not RegQueryStringValue(HKEY_CURRENT_USER, DataRootRegSubKey, DataRootRegValueName, Existing)) then
      RegWriteStringValue(HKEY_CURRENT_USER, DataRootRegSubKey, DataRootRegValueName, Chosen);
  end;
end;
