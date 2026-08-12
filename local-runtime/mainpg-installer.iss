; 启凡电商平台（MainPG）安装程序定义
; 编译：ISCC.exe mainpg-installer.iss
; 前提：先运行 build_installer.ps1 生成 dist\MainPG（PyInstaller onedir）
; 注意：本文件含中文，编译时需先转换为 UTF-8 带 BOM（build_installer.ps1 已处理）

#define MyAppName "启凡电商平台"
#define MyAppNameEn "MainPG"
#define MyAppVersion "1.1.0"
#define MyAppPublisher "启凡"
#define MyAppExeName "MainPG.exe"

[Setup]
AppId={{7F2E8B1A-6D26-4C9A-A360-9FBC2B84BF06}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\{#MyAppNameEn}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Uninstallable=yes
OutputDir=dist
OutputBaseFilename={#MyAppNameEn}-Setup
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

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
// 安装前自动结束正在运行的旧版 MainPG.exe，避免文件占用导致升级不完整
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  Exec('taskkill.exe', '/F /IM MainPG.exe /T', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(1200);
end;
