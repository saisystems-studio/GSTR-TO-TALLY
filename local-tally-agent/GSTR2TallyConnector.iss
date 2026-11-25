#define MyAppName "GSTR2Tally Connector"
#ifndef OutputName
  #define OutputName "SaiInfosys-GSTR2Tally-Setup"
#endif
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
; Default: C:\Program Files\GSTR2TallyConnector (user may Browse elsewhere).
DefaultDirName={autopf}\GSTR2TallyConnector
DefaultGroupName=GSTR2Tally Connector
UsePreviousAppDir=yes
DisableWelcomePage=yes
DisableDirPage=no
DisableProgramGroupPage=yes
DisableReadyPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputBaseFilename={#OutputName}
PrivilegesRequiredOverridesAllowed=commandline
Compression=lzma
SolidCompression=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}

[Files]
Source: "dist\GSTR2TallyConnector\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "release.json"; DestDir: "{app}"; Flags: ignoreversion

[Registry]
; Selected installation path, removed on uninstall.
Root: HKLM; Subkey: "Software\GSTR2TallyConnector"; ValueType: string; ValueName: "InstallPath"; ValueData: "{app}"; Flags: uninsdeletekey

[Icons]
; Auto-start for EVERY Windows user at sign-in. An admin install (possibly by a different administrator account than
; the person who uses the PC) must not write the per-user HKCU Run key, which would land in the admin's profile.
; The Connector's named mutex keeps it to one instance per user session.
Name: "{commonstartup}\GSTR2Tally Connector"; Filename: "{app}\{#MyAppExeName}"
Name: "{autoprograms}\GSTR2Tally Connector"; Filename: "{app}\{#MyAppExeName}"
Name: "{autoprograms}\Uninstall GSTR2Tally Connector"; Filename: "{uninstallexe}"

; Start immediately after the one normal installation. The executable is
; windowless and also starts automatically at each subsequent Windows sign-in.
[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Start GSTR2Tally Connector"; Flags: nowait runasoriginaluser

[Code]
var
  RemoveUserData: Boolean;

procedure StopConnector();
var
  Rc: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM {#MyAppExeName}', '', SW_HIDE, ewWaitUntilTerminated, Rc);
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpFinished then
  begin
    WizardForm.FinishedHeadingLabel.Caption := 'GSTR2Tally Connector installed successfully';
    WizardForm.FinishedLabel.Caption := 'The connector has been started and will start automatically at each sign-in.';
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  { Stop a running (e.g. repair/upgrade) instance so files can be replaced. }
  if CurStep = ssInstall then StopConnector();
end;

function InitializeUninstall(): Boolean;
begin
  Result := True;
  StopConnector();
  { Pairing credentials/config live in %LOCALAPPDATA%\GSTR2TallyConnector and
    are kept unless the user explicitly agrees to remove them. }
  RemoveUserData := (not UninstallSilent) and
    (MsgBox('Also remove your saved GSTR2Tally configuration and pairing credentials?' + #13#10 +
            'Choose No to keep them for a future reinstall.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and RemoveUserData then
    DelTree(ExpandConstant('{localappdata}\GSTR2TallyConnector'), True, True, True);
end;

const
  UninstKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{A34F75D7-9B47-4C85-A293-2DD3B9F47D6F}_is1';

{ Returns the install folder of any registered installation whose EXE really
  exists (checks every registry view). A stale entry whose EXE is gone is
  ignored, so repair / fresh install is allowed. }
function ExistingInstallDir(): String;
var
  Loc: String;
  I: Integer;
  Root: Integer;
begin
  Result := '';
  for I := 0 to 2 do
  begin
    case I of
      0: Root := HKLM64;
      1: Root := HKLM32;
    else
      Root := HKCU;
    end;
    if RegQueryStringValue(Root, UninstKey, 'InstallLocation', Loc) then
    begin
      Loc := RemoveBackslashUnlessRoot(Loc);
      if (Loc <> '') and FileExists(Loc + '\{#MyAppExeName}') then
      begin
        Result := Loc;
        Exit;
      end;
    end;
  end;
end;

function InitializeSetup(): Boolean;
var
  Dir: String;
begin
  Result := True;
  Dir := ExistingInstallDir();
  if Dir <> '' then
  begin
    if not WizardSilent() then
      MsgBox('GSTR2Tally Connector Already Installed' + #13#10 + Dir, mbInformation, MB_OK);
    Result := False;
  end;
end;
