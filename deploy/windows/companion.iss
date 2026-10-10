; Build with Build-Companion.ps1. The collector owns its state; Inno owns installed files.
#ifndef CollectorExe
  #error CollectorExe is required
#endif
#ifndef CompanionVersion
  #error CompanionVersion is required
#endif
#ifndef OutputDirectory
  #error OutputDirectory is required
#endif
#ifndef ServerUrl
  #define ServerUrl "http://192.168.1.81:8210"
#endif

[Setup]
#ifdef SMOKE_TEST
AppId=iRacingCoachCompanionSmoke
AppName=iRacing Coach Companion Test
OutputBaseFilename=iRacingCoachCompanionSetup-test
#else
AppId=iRacingCoachCompanion
AppName=iRacing Coach Companion
OutputBaseFilename=iRacingCoachCompanionSetup
#endif
AppVersion={#CompanionVersion}
AppPublisher=iRacing Coach
DefaultDirName={localappdata}\Programs\iRacingCoach
DefaultGroupName=iRacing Coach
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDirectory}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
WizardSizePercent=100
DisableWelcomePage=no
DisableReadyPage=no
DisableFinishedPage=no
CloseApplications=no
RestartApplications=no
UninstallDisplayIcon={app}\coach-collector.exe
SetupLogging=yes

[Files]
Source: "{#CollectorExe}"; DestDir: "{app}"; Flags: ignoreversion

#ifndef SMOKE_TEST
[Icons]
Name: "{group}\iRacing Coach Companion"; Filename: "{app}\coach-collector.exe"; Parameters: "run"; WorkingDir: "{app}"
#endif

[Code]
const
  EventModifyState = $0002;

function OpenEvent(Access: Cardinal; InheritHandle: Boolean; Name: String): THandle;
  external 'OpenEventW@kernel32.dll stdcall';
function SetEvent(Event: THandle): Boolean;
  external 'SetEvent@kernel32.dll stdcall';
function CloseHandle(Handle: THandle): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';

function CollectorMutex: String;
begin
  Result := 'Local\iRacingCoach.Collector';
#ifdef SMOKE_TEST
  Result := Result + '.' + GetEnv('COACH_TEST_INSTANCE_ID');
#endif
end;

function CollectorQuitEvent: String;
begin
  Result := 'Local\iRacingCoach.Collector.Quit';
#ifdef SMOKE_TEST
  Result := Result + '.' + GetEnv('COACH_TEST_INSTANCE_ID');
#endif
end;

function StateDirectory: String;
begin
#ifdef SMOKE_TEST
  Result := GetEnv('COACH_TEST_STATE_DIR');
#else
  Result := ExpandConstant('{localappdata}\iRacingCoach');
#endif
end;

function StateArguments: String;
begin
  Result := '';
#ifdef SMOKE_TEST
  Result := ' --state-dir ' + AddQuotes(StateDirectory);
#endif
end;

function SimClosed: Boolean;
var
  Locator, Services, Processes: Variant;
begin
  Result := False;
  try
    Locator := CreateOleObject('WbemScripting.SWbemLocator');
    Services := Locator.ConnectServer('', 'root\CIMV2');
    Processes := Services.ExecQuery('SELECT ProcessId FROM Win32_Process WHERE Name=''iRacingSim64DX11.exe''');
    Result := Processes.Count = 0;
  except
    Log('Could not check whether iRacing is closed: ' + GetExceptionMessage);
  end;
end;

function InitializeSetup: Boolean;
begin
#ifdef SMOKE_TEST
  Result := (GetEnv('COACH_TEST_INSTANCE_ID') <> '') and
    (GetEnv('COACH_TEST_RUN_KEY') <> '') and (StateDirectory <> '') and
    (GetEnv('COACH_TEST_IRACING_ROOT') <> '');
  if not Result then begin
    Log('The smoke installer requires isolated instance, registry and data paths.');
    Exit;
  end;
#endif
  Result := SimClosed;
  if not Result then
    SuppressibleMsgBox('Close iRacing before installing the companion.', mbError, MB_OK, IDOK);
end;

procedure InitializeWizard;
begin
  WizardForm.WelcomeLabel1.Caption := 'iRacing Coach Companion';
  WizardForm.WelcomeLabel2.Caption :=
    'Install on the Windows PC you race on.' + #13#10 + #13#10 +
    'The companion captures telemetry and garage setups, shows the live monitor, and starts when you sign in to Windows.';
  WizardForm.FinishedLabel.Caption :=
    'The companion is running and will start when you sign in to Windows.' + #13#10 + #13#10 +
    'Use its system tray icon to show the monitor or open iRacing Coach.';
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo,
  MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := 'Captures telemetry and garage setups from iRacing.' + NewLine +
    'Shows the live monitor while iRacing is connected.' + NewLine +
    'Starts automatically when you sign in to Windows.' + NewLine + NewLine +
    'Your existing settings and recordings are kept.';
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  QuitEvent: THandle;
  Attempt: Integer;
begin
  Result := '';
  if not SimClosed then begin
    Result := 'Close iRacing before installing the companion.';
    Exit;
  end;
  if not CheckForMutexes(CollectorMutex) then Exit;
  { Uses the collector's existing quit event, including the pre-installer build. }
  QuitEvent := OpenEvent(EventModifyState, False, CollectorQuitEvent);
  if QuitEvent <> 0 then begin
    if not SetEvent(QuitEvent) then Log('Could not signal the collector quit event.');
    CloseHandle(QuitEvent);
  end;
  for Attempt := 1 to 80 do begin
    if not CheckForMutexes(CollectorMutex) then Exit;
    Sleep(100);
  end;
  Result := 'Close the companion using Exit in its system tray menu, then try again.';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Arguments: String;
  ExitCode: Integer;
begin
  if CurStep <> ssPostInstall then Exit;
  Arguments := 'install' + StateArguments;
  if not FileExists(StateDirectory + '\collector.json') then
    Arguments := Arguments + ' --server ' + AddQuotes('{#ServerUrl}');
#ifdef SMOKE_TEST
  Arguments := Arguments + ' --iracing-root ' + AddQuotes(GetEnv('COACH_TEST_IRACING_ROOT'));
#endif
  if not Exec(ExpandConstant('{app}\coach-collector.exe'), Arguments,
    ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ExitCode) then
    RaiseException('Could not start the companion. Run setup again.');
  if ExitCode <> 0 then
    RaiseException('The companion could not finish setup. Run setup again.');
end;

function InitializeUninstall: Boolean;
begin
#ifdef SMOKE_TEST
  Result := (GetEnv('COACH_TEST_INSTANCE_ID') <> '') and
    (GetEnv('COACH_TEST_RUN_KEY') <> '') and (StateDirectory <> '');
  if not Result then Exit;
#endif
  Result := SimClosed;
  if not Result then
    SuppressibleMsgBox('Close iRacing before removing the companion.', mbError, MB_OK, IDOK);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ExitCode: Integer;
  Collector: String;
begin
  if CurUninstallStep <> usUninstall then Exit;
  if not SimClosed then
    RaiseException('Close iRacing before removing the companion.');
  Collector := ExpandConstant('{app}\coach-collector.exe');
  if not FileExists(Collector) then Exit;
  if not Exec(Collector, 'uninstall --installer' + StateArguments,
    ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ExitCode) then
    RaiseException('Could not stop the companion. Close it from the system tray and try again.');
  if ExitCode <> 0 then
    RaiseException('Could not remove the companion startup entry. Try again after closing it.');
end;
