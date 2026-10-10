# Windows companion distribution

The user-facing name is **iRacing Coach Companion**. The lightweight Rust process
remains `coach-collector.exe`. The website's muted **Download companion** link
downloads `/downloads/iRacingCoachCompanionSetup.exe`.

Run that installer on the Windows x64 sim PC with iRacing closed. It installs for
the signed-in user under `%LOCALAPPDATA%\Programs\iRacingCoach`, adds a Start menu
entry and Windows installed-app entry, starts the companion, and enables launch
at Windows sign-in. New installs connect to `http://192.168.1.81:8210`; existing
installs keep their server, identity, placement, layout and other settings. The
tray menu opens Coach and shows the monitor. Installing an update gracefully
stops the old collector before replacing it. Removal stops it and removes startup
and installed program files; `%LOCALAPPDATA%\iRacingCoach` and iRacing's files stay
intact.

The installer is self-contained: no runtime download, .NET, WebView, administrator
rights, or reboot. It is currently unsigned; Windows may show a publisher warning.

## Build and verify

Use the [official Inno Setup 6 compiler](https://jrsoftware.org/isdl.php), currently
6.7.3. Verify its Authenticode signature (publisher Pyrsys B.V.) as documented in
the [verification instructions](https://jrsoftware.org/isdl-verify.php). A portable
compiler may live at `%USERPROFILE%\.cache\iracing-coach-tools\inno-6.7.3\ISCC.exe`,
or pass `-InnoCompilerPath`.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy\windows\Build-Companion.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy\windows\Test-Companion.ps1
```

Build output and the SHA-256/revision manifest are under ignored `target/companion`.
The smoke installer has a separate app identity, fake iRacing folders, Run key,
state folder and named objects. It verifies real installation, a running upgrade,
identity/settings retention, startup registration and data-preserving removal.
It leaves evidence under `target/companion/smoke-*`.

## Publish and release

Run the repository's required checks, commit the release, then:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy\mini-pc\Release-WithCompanion.ps1
```

This builds the Windows MSVC collector and installer on the development PC,
verifies and publishes the installer on the mini PC, then releases the committed
service through the mini-pc skill's managed release script. It verifies the final
HTTP download against the build SHA-256. No Docker Desktop is needed. An installer
built from dirty source or a different revision cannot be published.

Artifacts live under `/srv/g-house/apps/iracing-coach/downloads/releases/<revision>`;
previous releases remain. Atomic relative symlinks select the current installer
and `companion.json`. Compose mounts that directory read-only at `/app/web/downloads`.
The downloads static route returns 404 for an absent file, never the SPA document.
The binary is distributed separately because the Linux image build cannot produce
the proven Windows MSVC collector. Later service-only releases may continue using
the managed release script; use this wrapper whenever the companion changes.
