; One installer: GSTR2Tally Desktop (WebView2 shell) + the local Tally Connector. No secrets are packaged.
#define MyAppName "GSTR2Tally"
#ifndef MyAppVersion
  #define MyAppVersion "1.1.0"
#endif
#ifndef OutputName
  #define OutputName "GSTR2TallySetup"
#endif
#define DesktopExe "GSTR2Tally.exe"
#define ConnectorExe "GSTR2TallyConnector.exe"

[Setup]
AppId={{5D0F6A52-3C1B-4E7B-9B6E-2A7C61E8D9A4}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Sai Infosys
DefaultDirName={autopf}\GSTR2Tally
DefaultGroupName=GSTR2Tally
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputBaseFilename={#OutputName}
Compression=lzma
SolidCompression=yes
UninstallDisplayIcon={app}\{#DesktopExe}
UninstallDisplayName={#MyAppName}
CloseApplications=yes

[Files]
Source: "dist\GSTR2Tally\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "dist\GSTR2TallyConnector\*"; DestDir: "{app}\Connector"; Flags: recursesubdirs ignoreversion
Source: "release.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "release.json"; DestDir: "{app}\Connector"; Flags: ignoreversion
Source: "desktop.json"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist

[Icons]
Name: "{autodesktop}\GSTR2Tally"; Filename: "{app}\{#DesktopExe}"
Name: "{autoprograms}\GSTR2Tally\GSTR2Tally"; Filename: "{app}\{#DesktopExe}"
Name: "{autoprograms}\GSTR2Tally\Uninstall GSTR2Tally"; Filename: "{uninstallexe}"
; Connector auto-starts for every Windows user at sign-in (single instance by named mutex).
Name: "{commonstartup}\GSTR2Tally Connector"; Filename: "{app}\Connector\{#ConnectorExe}"

[Run]
Filename: "{app}\Connector\{#ConnectorExe}"; Flags: nowait runasoriginaluser
Filename: "{app}\{#DesktopExe}"; Description: "Launch GSTR2Tally"; Flags: nowait postinstall skipifsilent runasoriginaluser

[Code]
var
  RemoveUserData: Boolean;

procedure StopApps();
var
  Rc: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM {#DesktopExe}', '', SW_HIDE, ewWaitUntilTerminated, Rc);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM {#ConnectorExe}', '', SW_HIDE, ewWaitUntilTerminated, Rc);
end;

function WebView2Installed(): Boolean;
var
  V: String;
begin
  Result := RegQueryStringValue(HKLM32, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', V)
         or RegQueryStringValue(HKLM64, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', V)
         or RegQueryStringValue(HKCU, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', V);
  Result := Result and (V <> '') and (V <> '0.0.0.0');
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if not WebView2Installed() then
    if MsgBox('GSTR2Tally needs the Microsoft Edge WebView2 Runtime, which was not found on this PC.' + #13#10 +
              'Install it from https://go.microsoft.com/fwlink/p/?LinkId=2124703 and run this setup again.' + #13#10#13#10 +
              'Continue anyway?', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDNO then
      Result := False;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then StopApps();
end;

function InitializeUninstall(): Boolean;
begin
  Result := True;
  StopApps();
  RemoveUserData := (not UninstallSilent) and
    (MsgBox('Also remove saved GSTR2Tally data (sign-in cache and Connector pairing)?' + #13#10 +
            'Choose No to keep it for a reinstall.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and RemoveUserData then
  begin
    DelTree(ExpandConstant('{localappdata}\GSTR2Tally'), True, True, True);
    DelTree(ExpandConstant('{localappdata}\GSTR2TallyConnector'), True, True, True);
  end;
end;
