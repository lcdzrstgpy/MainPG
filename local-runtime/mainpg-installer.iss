; 启凡电商平台（MainPG）安装程序定义
; 编译：ISCC.exe mainpg-installer.iss
; 前提：先运行 build_installer.ps1 生成 dist\MainPG（PyInstaller onedir）
; 注意：本文件含中文，编译时需先转换为 UTF-8 带 BOM（build_installer.ps1 已处理）

#define MyAppName "启凡电商平台"
#define MyAppNameEn "MainPG"
#define MyAppVersion "1.0.0"
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

[Files]
Source: "dist\{#MyAppNameEn}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

; 桌面 + 开始菜单快捷方式：双击即启动，无需进文件夹找 exe
[Icons]
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{userprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent
