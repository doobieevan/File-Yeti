#define MyAppName "File-Yeti"
#define MyAppVersion "0.2.0"
#define MyAppPublisher "File-Yeti"
#define MyAppURL "https://github.com/your-account/file-yeti"
#define MyAppExeName "File-Yeti.exe"

[Setup]
AppId={{7C7E0D36-4A2D-4B8C-9A9B-6D7A4B2D1130}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
VersionInfoVersion={#MyAppVersion}.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=Visual storage review and safe cleanup
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
DefaultDirName={autopf}\File-Yeti
DefaultGroupName=File-Yeti
OutputDir=installer
OutputBaseFilename=File-Yeti-Setup-{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
SetupIconFile=file-yeti.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
PrivilegesRequired=lowest

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\File-Yeti"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\File-Yeti"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch File-Yeti"; Flags: nowait postinstall skipifsilent
