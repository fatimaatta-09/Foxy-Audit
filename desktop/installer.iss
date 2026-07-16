; Inno Setup script for the Foxy Audit desktop pet (Windows).
; Compiled in CI after PyInstaller produces dist\FoxyAudit.exe:
;   iscc /DAppVersion=1.0.0 desktop\installer.iss
; Output: Output\FoxyAudit-Setup-<version>.exe
;
; NOTE: the resulting installer is UNSIGNED unless a code-signing certificate is
; supplied in CI. An unsigned installer triggers a SmartScreen warning and must
; not be handed to real clients as-is.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{6F0C2A1E-2B7D-4E2A-9E2C-F0X9AUDIT0001}
AppName=Foxy Audit
AppVersion={#AppVersion}
AppPublisher=Foxy Audit
AppPublisherURL=https://foxyaudit.tech
DefaultDirName={autopf}\Foxy Audit
DefaultGroupName=Foxy Audit
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=FoxyAudit-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
PrivilegesRequired=lowest
; Brand the installer + Add/Remove Programs entry with the fox logo.
SetupIconFile=foxy.ico
UninstallDisplayIcon={app}\FoxyAudit.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"
Name: "startup"; Description: "Start Foxy Audit when I sign in"; GroupDescription: "Startup:"

[Files]
; PyInstaller onefile output — a single self-contained exe.
Source: "..\dist\FoxyAudit.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Foxy Audit"; Filename: "{app}\FoxyAudit.exe"
Name: "{autodesktop}\Foxy Audit"; Filename: "{app}\FoxyAudit.exe"; Tasks: desktopicon
Name: "{userstartup}\Foxy Audit"; Filename: "{app}\FoxyAudit.exe"; Tasks: startup

[Run]
Filename: "{app}\FoxyAudit.exe"; Description: "Launch Foxy Audit"; Flags: nowait postinstall skipifsilent
