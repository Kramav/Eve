; Inno Setup script for Eve — packages a *pre-built* distribution folder into a
; single Eve-Setup.exe with Start Menu shortcut, optional desktop icon, optional
; run-at-login, and an uninstaller.
;
; This script does NOT build Eve — it packages dist\Eve\ produced by the build
; step in installer\README.md (PyInstaller onedir + the Electron UI). Compile:
;     iscc installer\eve.iss
; (ISCC.exe ships with Inno Setup 6: https://jrsoftware.org/isdl.php)

#define MyAppName "Eve"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "Eve"
#define MyAppExeName "Eve.exe"
; Folder containing the built app (Eve.exe + everything it needs). Override with
;     iscc /DAppDir=..\dist\Eve installer\eve.iss
#ifndef AppDir
  #define AppDir "..\dist\Eve"
#endif

[Setup]
AppId={{6F3D2A10-7E54-4B8C-9A2E-EVE0000DEFAB}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Per-user install by default → no admin prompt. Switch to "admin" + {autopf}
; if you want an all-users install.
PrivilegesRequiredOverridesAllowed=dialog commandline
OutputDir=Output
OutputBaseFilename=Eve-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked
Name: "startuplogin"; Description: "Start {#MyAppName} automatically when I log in"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
; Pull in the entire built app folder.
Source: "{#AppDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; Optional run-at-login (mirrors core/autostart.py's HKCU Run key).
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; \
  ValueName: "Eve"; ValueData: """{app}\{#MyAppExeName}"""; Tasks: startuplogin; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName} now"; Flags: nowait postinstall skipifsilent
