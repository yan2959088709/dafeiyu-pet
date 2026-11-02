; 大肥鱼桌宠 Windows 安装包（Inno Setup 6）。
; 等价上游 electron-builder 的 NSIS 产物：单文件安装、可选桌面图标、安装后可勾选立即运行。
; 构建：先跑 tools\build_exe.py 生成 dist\大肥鱼桌宠.exe，再执行
;   tools\build_exe.py --installer
; 版本号由构建脚本用 /DMyAppVersion=<pet/version.py> 传入，避免两处手改漂移。

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppName "大肥鱼桌宠"
#define MyAppExeName "大肥鱼桌宠.exe"

[Setup]
AppId={{6C1B7E64-3F2A-4C0E-9A1B-5D2E7F8A9C10}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
DefaultDirName={autopf}\DafeiyuPet
DefaultGroupName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir=..\dist
OutputBaseFilename=大肥鱼桌宠-{#MyAppVersion}-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: unchecked

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即运行 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\voice-cache"
Type: filesandordirs; Name: "{app}\attachments"
