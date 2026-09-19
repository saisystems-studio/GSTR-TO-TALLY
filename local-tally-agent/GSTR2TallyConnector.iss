#define MyAppName "GSTR2Tally Connector"
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#define MyAppPublisher "GSTR2Tally"
#define MyAppExeName "GSTR2TallyConnector.exe"

[Setup]
AppId={{A34F75D7-9B47-4C85-A293-2DD3B9F47D6F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\GSTR2Tally Connector
DisableWelcomePage=yes
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputBaseFilename=GSTR2TallyConnectorSetup
Compression=lzma
SolidCompression=yes
UninstallDisplayIcon={app}\{#MyAppExeName}

[Files]
Source: "dist\GSTR2TallyConnector\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "release.json"; DestDir: "{app}"; Flags: ignoreversion

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "GSTR2TallyConnector"; ValueData: """{app}\{#MyAppExeName}"""; Flags: uninsdeletevalue

[Icons]
Name: "{userprograms}\GSTR2Tally Connector"; Filename: "{app}\{#MyAppExeName}"

; Start immediately after the one normal installation. The executable is
; windowless and also starts automatically at each subsequent Windows sign-in.
[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Start GSTR2Tally Connector"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\GSTR2TallyConnector"
