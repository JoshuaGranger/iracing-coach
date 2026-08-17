using Microsoft.Win32;
using System.Diagnostics;
using System.IO.Compression;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text.Json;

namespace iRacingCoach.Installer;

internal static class Program
{
    internal const string ProductVersion = "0.16.0";
    internal static string RepairInstallerFileName => $"iRacingCoach-{ProductVersion}-Setup.exe";
    internal static string PerUserTarget => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "iRacing Coach");

    [STAThread]
    private static int Main(string[] args)
    {
        ApplicationConfiguration.Initialize();
        if (args.Length >= 2 && args[0] == "--test-install")
        {
            var testErrorPath = Path.Combine(Path.GetTempPath(), "iRacingCoach-installer-test-error.txt");
            try
            {
                if (File.Exists(testErrorPath)) File.Delete(testErrorPath);
                InstallerEngine.Install(ValidateTestTarget(args[1]), integrateWithWindows: false);
                return 0;
            }
            catch (Exception ex)
            {
                // The setup executable is a Windows app without a console. Keep an
                // explicit test-only log so automated release validation is diagnosable.
                File.WriteAllText(testErrorPath, ex.ToString());
                return 1;
            }
        }
        if (args.Length >= 2 && args[0] == "--test-rollback")
        {
            try
            {
                InstallerEngine.Install(ValidateTestTarget(args[1]), integrateWithWindows: false, simulateFailureAfterSwap: true);
                return 2;
            }
            catch (InvalidOperationException ex) when (ex.Message == "Simulated post-swap installer failure.")
            {
                return 0;
            }
        }
        if (args.Length >= 2 && args[0] == "--test-validate-payload")
        {
            try
            {
                InstallerEngine.ValidatePayloadForTest(ValidateTestTarget(args[1]));
                return 0;
            }
            catch
            {
                return 1;
            }
        }
        var silent = args.Any(argument => argument.Equals("/S", StringComparison.OrdinalIgnoreCase) || argument.Equals("--silent", StringComparison.OrdinalIgnoreCase));
        if (silent)
        {
            try
            {
                InstallerEngine.Install(PerUserTarget, integrateWithWindows: true);
                WriteInstallLog("Silent per-user installation completed successfully.");
                return 0;
            }
            catch (Exception ex)
            {
                WriteInstallLog(ex.ToString());
                return 1;
            }
        }
        if (args.Any(argument => string.Equals(argument, "--repair", StringComparison.OrdinalIgnoreCase)))
        {
            try
            {
                InstallerEngine.Install(PerUserTarget, integrateWithWindows: true);
                MessageBox.Show("iRacing Coach was repaired successfully.", "iRacing Coach Repair", MessageBoxButtons.OK, MessageBoxIcon.Information);
                return 0;
            }
            catch (Exception ex)
            {
                MessageBox.Show(ex.Message, "iRacing Coach Repair", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 1;
            }
        }
        Application.Run(new InstallerForm());
        return 0;
    }

    private static void WriteInstallLog(string message)
    {
        var directory = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "iRacingCoach", "logs");
        Directory.CreateDirectory(directory);
        File.AppendAllText(Path.Combine(directory, "installer.log"), $"{DateTimeOffset.UtcNow:O} {message}{Environment.NewLine}");
    }

    private static string ValidateTestTarget(string value)
    {
        var target = Path.GetFullPath(value).TrimEnd(Path.DirectorySeparatorChar);
        var temporaryRoot = Path.GetFullPath(Path.GetTempPath()).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
        if (!target.StartsWith(temporaryRoot, StringComparison.OrdinalIgnoreCase) ||
            !Path.GetFileName(target).StartsWith("iRacingCoach-Installer-Test-", StringComparison.Ordinal))
        {
            throw new InvalidOperationException("Test installs are restricted to a named folder under the Windows temporary directory.");
        }
        return target;
    }
}

internal sealed class InstallerForm : Form
{
    private readonly Button _install = new() { Text = "Install iRacing Coach", Width = 180, Height = 40, BackColor = Color.FromArgb(70, 81, 94), ForeColor = Color.White, FlatStyle = FlatStyle.Flat };
    private readonly CheckBox _desktop = new() { Text = "Create a desktop shortcut", AutoSize = true, Checked = true, ForeColor = Color.FromArgb(195, 196, 196) };
    private readonly CheckBox _launch = new() { Text = "Open iRacing Coach after installation", AutoSize = true, Checked = true, ForeColor = Color.FromArgb(195, 196, 196) };
    private readonly Label _status = new() { Text = "Ready to install", AutoSize = true, ForeColor = Color.FromArgb(150, 153, 157) };
    private readonly ProgressBar _progress = new() { Width = 430, Height = 8, Style = ProgressBarStyle.Marquee, Visible = false };

    public InstallerForm()
    {
        Text = "iRacing Coach Setup";
        Icon = Icon.ExtractAssociatedIcon(Environment.ProcessPath!);
        BackColor = Color.FromArgb(20, 21, 23);
        ForeColor = Color.FromArgb(232, 233, 231);
        Font = new Font("Segoe UI", 10);
        ClientSize = new Size(520, 370);
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false;
        StartPosition = FormStartPosition.CenterScreen;

        var title = new Label { Text = "iRacing Coach", AutoSize = true, Font = new Font("Segoe UI", 22, FontStyle.Bold), Location = new Point(42, 34), ForeColor = ForeColor };
        var subtitle = new Label { Text = "A local companion for race analysis, planning, setup work, and live coaching.", AutoSize = true, Location = new Point(45, 82), ForeColor = Color.FromArgb(195, 196, 196) };
        var boundary = new Label { Text = "App and analysis runtime", AutoSize = true, Location = new Point(45, 130), ForeColor = Color.FromArgb(150, 153, 157) };
        var programFiles = new Label { Text = Program.PerUserTarget, AutoSize = true, Location = new Point(45, 151), ForeColor = ForeColor };
        var data = new Label { Text = "Your Coach folder stays in Documents\\iRacing Coach and is never removed by uninstall or upgrade.", MaximumSize = new Size(430, 0), AutoSize = true, Location = new Point(45, 185), ForeColor = Color.FromArgb(163, 173, 184) };
        _desktop.Location = new Point(48, 232); _launch.Location = new Point(48, 258);
        _progress.Location = new Point(45, 296); _status.Location = new Point(45, 315); _install.Location = new Point(294, 305);
        _install.Click += InstallClicked;
        Controls.AddRange([title, subtitle, boundary, programFiles, data, _desktop, _launch, _progress, _status, _install]);
    }

    protected override void OnHandleCreated(EventArgs e)
    {
        base.OnHandleCreated(e);
        NativeTheme.ApplyDarkTitleBar(Handle);
    }

    private async void InstallClicked(object? sender, EventArgs e)
    {
        _install.Enabled = false; _desktop.Enabled = false; _launch.Enabled = false; _progress.Visible = true; _status.Text = "Installing app files…";
        try
        {
            var target = Program.PerUserTarget;
            await Task.Run(() => InstallerEngine.Install(target, integrateWithWindows: true, desktopShortcut: _desktop.Checked));
            _progress.Visible = false; _status.Text = "Installation complete"; _install.Text = "Close"; _install.Enabled = true;
            _install.Click -= InstallClicked;
            _install.Click += (_, _) => Close();
            if (_launch.Checked) Process.Start(new ProcessStartInfo(Path.Combine(target, "iRacing Coach.exe")) { UseShellExecute = true });
        }
        catch (Exception ex)
        {
            _progress.Visible = false; _status.Text = "Installation did not complete"; _install.Text = "Try again"; _install.Enabled = true; _desktop.Enabled = true; _launch.Enabled = true;
            MessageBox.Show(ex.Message, "iRacing Coach Setup", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}

internal static class NativeTheme
{
    public static void ApplyDarkTitleBar(IntPtr handle)
    {
        var enabled = 1;
        if (DwmSetWindowAttribute(handle, 20, ref enabled, sizeof(int)) != 0)
            _ = DwmSetWindowAttribute(handle, 19, ref enabled, sizeof(int));
        var caption = ColorRef(23, 25, 28);
        var text = ColorRef(232, 233, 231);
        var border = ColorRef(48, 53, 59);
        _ = DwmSetWindowAttribute(handle, 35, ref caption, sizeof(int));
        _ = DwmSetWindowAttribute(handle, 36, ref text, sizeof(int));
        _ = DwmSetWindowAttribute(handle, 34, ref border, sizeof(int));
    }

    private static int ColorRef(byte red, byte green, byte blue) => red | (green << 8) | (blue << 16);

    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(IntPtr window, int attribute, ref int value, int valueSize);
}

internal static class InstallerEngine
{
    private const string PayloadName = "iRacingCoach.Payload.zip";

    public static void Install(string target, bool integrateWithWindows, bool desktopShortcut = false, bool simulateFailureAfterSwap = false)
    {
        target = Path.GetFullPath(target).TrimEnd(Path.DirectorySeparatorChar);
        var parent = Directory.GetParent(target)?.FullName ?? throw new InvalidOperationException("The install path has no parent folder.");
        Directory.CreateDirectory(parent);
        var staging = target + ".installing";
        var backup = target + ".previous";
        if (Directory.Exists(staging)) Directory.Delete(staging, recursive: true);
        Directory.CreateDirectory(staging);
        try
        {
            ExtractPayload(staging);
            ValidatePayload(staging);
            StopRunningPriorVersion(target);
            if (Directory.Exists(backup)) Directory.Delete(backup, recursive: true);
            if (Directory.Exists(target)) MoveDirectoryWithRetry(target, backup);
            MoveDirectoryWithRetry(staging, target);
            if (simulateFailureAfterSwap) throw new InvalidOperationException("Simulated post-swap installer failure.");
            if (integrateWithWindows)
            {
                RemoveLegacyInstallations(target);
                IntegrateWithWindows(target, desktopShortcut);
            }
            if (Directory.Exists(backup)) Directory.Delete(backup, recursive: true);
            if (integrateWithWindows)
            {
                CacheRepairInstaller();
            }
        }
        catch
        {
            if (Directory.Exists(backup))
            {
                if (Directory.Exists(target)) Directory.Delete(target, recursive: true);
                Directory.Move(backup, target);
            }
            throw;
        }
        finally
        {
            if (Directory.Exists(staging)) Directory.Delete(staging, recursive: true);
        }
    }

    private static void StopRunningPriorVersion(string target)
    {
        var expected = Path.Combine(target, "iRacing Coach.exe");
        foreach (var process in Process.GetProcessesByName("iRacing Coach"))
        {
            using (process)
            {
                string? path = null;
                try { path = process.MainModule?.FileName; }
                catch (Exception ex) when (ex is InvalidOperationException or System.ComponentModel.Win32Exception or NotSupportedException) { }
                if (!string.Equals(path, expected, StringComparison.OrdinalIgnoreCase)) continue;
                try
                {
                    if (process.CloseMainWindow() && process.WaitForExit(2500)) continue;
                    process.Kill(entireProcessTree: true);
                    process.WaitForExit(2500);
                }
                catch (Exception ex) when (ex is InvalidOperationException or System.ComponentModel.Win32Exception or NotSupportedException)
                {
                    throw new IOException("The prior iRacing Coach version is still running. Exit it from the tray and try again.", ex);
                }
            }
        }
    }

    private static void MoveDirectoryWithRetry(string source, string destination)
    {
        var deadline = DateTime.UtcNow.AddSeconds(12);
        while (true)
        {
            try
            {
                Directory.Move(source, destination);
                return;
            }
            catch (Exception ex) when (
                ex is IOException or UnauthorizedAccessException
                && DateTime.UtcNow < deadline)
            {
                // Windows may keep WebView2 or a just-terminated child process handle
                // alive briefly after the app process exits. Keep the replacement
                // atomic, but allow those handles time to close.
                Thread.Sleep(200);
            }
        }
    }

    private static void RemoveLegacyInstallations(string currentTarget)
    {
        var candidates = new[]
        {
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "iRacing Coach"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86), "iRacing Coach"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "iRacing Coach"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "iRacing Coach")
        };
        foreach (var candidate in candidates.Where(path => !string.IsNullOrWhiteSpace(path)).Select(Path.GetFullPath).Distinct(StringComparer.OrdinalIgnoreCase))
        {
            if (string.Equals(candidate.TrimEnd(Path.DirectorySeparatorChar), currentTarget, StringComparison.OrdinalIgnoreCase)) continue;
            if (!Directory.Exists(candidate)) continue;
            if (!File.Exists(Path.Combine(candidate, "iRacing Coach.exe")) && !File.Exists(Path.Combine(candidate, "Uninstall iRacing Coach.exe"))) continue;
            try { Directory.Delete(candidate, recursive: true); }
            catch (UnauthorizedAccessException) { }
            catch (IOException) { }
        }
        using var currentUserUninstall = Registry.CurrentUser.OpenSubKey(@"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", writable: true);
        currentUserUninstall?.DeleteSubKeyTree("iRacingCoach", throwOnMissingSubKey: false);
    }

    private static void CacheRepairInstaller()
    {
        var source = Environment.ProcessPath ?? throw new InvalidOperationException("The setup path is unavailable.");
        var cacheRoot = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "iRacingCoach", "Installer");
        Directory.CreateDirectory(cacheRoot);
        var destination = Path.Combine(cacheRoot, Program.RepairInstallerFileName);
        foreach (var oldInstaller in Directory.EnumerateFiles(cacheRoot, "iRacingCoach-*-Setup.exe", SearchOption.TopDirectoryOnly))
        {
            if (!string.Equals(oldInstaller, destination, StringComparison.OrdinalIgnoreCase)) File.Delete(oldInstaller);
        }
        if (!string.Equals(Path.GetFullPath(source), Path.GetFullPath(destination), StringComparison.OrdinalIgnoreCase))
            File.Copy(source, destination, overwrite: true);
    }

    private static void ExtractPayload(string destination)
    {
        using var resource = Assembly.GetExecutingAssembly().GetManifestResourceStream(PayloadName)
            ?? throw new InvalidOperationException("The app payload is missing from this installer.");
        using var archive = new ZipArchive(resource, ZipArchiveMode.Read);
        var root = Path.GetFullPath(destination) + Path.DirectorySeparatorChar;
        foreach (var entry in archive.Entries)
        {
            var relative = entry.FullName.Replace('\\', '/');
            var path = Path.GetFullPath(Path.Combine(destination, relative.Replace('/', Path.DirectorySeparatorChar)));
            if (!path.StartsWith(root, StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("The installer payload contains an unsafe path.");
            if (relative.EndsWith('/')) { Directory.CreateDirectory(path); continue; }
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            entry.ExtractToFile(path, overwrite: true);
        }
    }

    private static void ValidatePayload(string staging)
    {
        var required = new[]
        {
            "iRacing Coach.exe",
            "Uninstall iRacing Coach.exe",
            Path.Combine("python", "python.exe"),
            Path.Combine("iracing-coach", "skills", "analyze-iracing-race", "scripts", "start-mcp.ps1"),
            Path.Combine("coach-engine", "codex", "codex.exe"),
            Path.Combine("coach-engine", "schemas", "codex_app_server_protocol.schemas.json"),
            Path.Combine("coach-engine", "coach-engine-manifest.json"),
            "release-manifest.json"
        };
        foreach (var relative in required)
        {
            if (!File.Exists(Path.Combine(staging, relative)))
                throw new InvalidDataException($"The installer payload is incomplete: {relative}");
        }

        ValidateReleaseManifest(staging);

        var manifestPath = Path.Combine(staging, "coach-engine", "coach-engine-manifest.json");
        using var manifest = JsonDocument.Parse(File.ReadAllText(manifestPath));
        var expectedHash = manifest.RootElement.GetProperty("runtimeSha256").GetString()
            ?? throw new InvalidDataException("The Coach Engine manifest has no runtime checksum.");
        var runtimePath = Path.Combine(staging, "coach-engine", "codex", "codex.exe");
        using var runtime = File.OpenRead(runtimePath);
        var actualHash = Convert.ToHexString(SHA256.HashData(runtime)).ToLowerInvariant();
        if (!string.Equals(expectedHash, actualHash, StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("The Coach Engine runtime failed its checksum validation.");

        using var versionCheck = Process.Start(new ProcessStartInfo(runtimePath, "--version")
        {
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden
        }) ?? throw new InvalidDataException("The Coach Engine runtime could not be validated.");
        if (!versionCheck.WaitForExit(15_000) || versionCheck.ExitCode != 0)
        {
            try { if (!versionCheck.HasExited) versionCheck.Kill(entireProcessTree: true); } catch (InvalidOperationException) { }
            throw new InvalidDataException("The Coach Engine runtime did not pass its startup check.");
        }
        var output = versionCheck.StandardOutput.ReadToEnd().Trim();
        var expectedVersion = manifest.RootElement.GetProperty("runtimeVersion").GetString();
        if (!string.Equals(output, $"codex-cli {expectedVersion}", StringComparison.Ordinal))
            throw new InvalidDataException("The Coach Engine runtime version does not match its protocol package.");
    }

    internal static void ValidatePayloadForTest(string staging) => ValidatePayload(staging);

    private static void ValidateReleaseManifest(string staging)
    {
        var manifestPath = Path.Combine(staging, "release-manifest.json");
        using var manifest = JsonDocument.Parse(File.ReadAllText(manifestPath));
        var root = manifest.RootElement;
        if (!root.TryGetProperty("manifestVersion", out var manifestVersion) ||
            manifestVersion.ValueKind != JsonValueKind.Number ||
            !manifestVersion.TryGetInt32(out var version) || version != 1)
            throw new InvalidDataException("The release manifest version is unsupported.");
        if (!root.TryGetProperty("appVersion", out var appVersion) ||
            appVersion.ValueKind != JsonValueKind.String ||
            !string.Equals(appVersion.GetString(), Program.ProductVersion, StringComparison.Ordinal))
            throw new InvalidDataException("The release manifest application version does not match the installer.");
        if (!root.TryGetProperty("sourceCommit", out var sourceCommit) ||
            sourceCommit.ValueKind != JsonValueKind.String ||
            sourceCommit.GetString() is not { Length: 40 } commit ||
            !commit.All(Uri.IsHexDigit))
            throw new InvalidDataException("The release manifest has no valid source commit.");
        if (!root.TryGetProperty("files", out var files) || files.ValueKind != JsonValueKind.Array)
            throw new InvalidDataException("The release manifest has no file inventory.");

        var canonicalRoot = Path.GetFullPath(staging).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
        var expected = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var entry in files.EnumerateArray())
        {
            if (entry.ValueKind != JsonValueKind.Object ||
                !entry.TryGetProperty("path", out var pathElement) || pathElement.ValueKind != JsonValueKind.String ||
                !entry.TryGetProperty("size", out var sizeElement) || sizeElement.ValueKind != JsonValueKind.Number ||
                !entry.TryGetProperty("sha256", out var hashElement) || hashElement.ValueKind != JsonValueKind.String ||
                !entry.TryGetProperty("component", out var componentElement) || componentElement.ValueKind != JsonValueKind.String ||
                !entry.TryGetProperty("version", out var componentVersion) || componentVersion.ValueKind != JsonValueKind.String)
                throw new InvalidDataException("A release manifest file entry is malformed.");
            var relative = pathElement.GetString() ?? string.Empty;
            var declaredHash = hashElement.GetString() ?? string.Empty;
            if (relative.Length == 0 || relative.Contains('\\') || Path.IsPathRooted(relative) ||
                string.Equals(relative, "release-manifest.json", StringComparison.OrdinalIgnoreCase) ||
                !sizeElement.TryGetInt64(out var declaredSize) || declaredSize < 0 ||
                declaredHash.Length != 64 || !declaredHash.All(Uri.IsHexDigit) ||
                string.IsNullOrWhiteSpace(componentElement.GetString()) || string.IsNullOrWhiteSpace(componentVersion.GetString()))
                throw new InvalidDataException("A release manifest file entry is invalid.");
            var path = Path.GetFullPath(Path.Combine(staging, relative.Replace('/', Path.DirectorySeparatorChar)));
            if (!path.StartsWith(canonicalRoot, StringComparison.OrdinalIgnoreCase) || !expected.Add(relative))
                throw new InvalidDataException("A release manifest file path is unsafe or duplicated.");
            if (!File.Exists(path)) throw new InvalidDataException($"The release payload is missing: {relative}");
            var info = new FileInfo(path);
            if (info.Length != declaredSize) throw new InvalidDataException($"The release payload size does not match: {relative}");
            using var stream = info.OpenRead();
            var actualHash = Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant();
            if (!string.Equals(actualHash, declaredHash, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException($"The release payload checksum does not match: {relative}");
        }

        var actual = Directory.EnumerateFiles(staging, "*", SearchOption.AllDirectories)
            .Select(path => Path.GetRelativePath(staging, path).Replace('\\', '/'))
            .Where(path => !string.Equals(path, "release-manifest.json", StringComparison.OrdinalIgnoreCase))
            .ToArray();
        if (actual.Length != expected.Count || actual.Any(path => !expected.Contains(path)))
            throw new InvalidDataException("The release payload contains an unmanifested file.");
    }

    private static void IntegrateWithWindows(string target, bool desktopShortcut)
    {
        var executable = Path.Combine(target, "iRacing Coach.exe");
        var startMenu = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), "iRacing Coach");
        Directory.CreateDirectory(startMenu);
        CreateShortcut(Path.Combine(startMenu, "iRacing Coach.lnk"), executable, target);
        if (desktopShortcut) CreateShortcut(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "iRacing Coach.lnk"), executable, target);
        using var key = Registry.CurrentUser.CreateSubKey(@"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\iRacing Coach");
        key.SetValue("DisplayName", "iRacing Coach"); key.SetValue("DisplayVersion", Program.ProductVersion); key.SetValue("Publisher", "iRacing Coach");
        key.SetValue("InstallLocation", target); key.SetValue("DisplayIcon", executable); key.SetValue("UninstallString", $"\"{Path.Combine(target, "Uninstall iRacing Coach.exe")}\"");
        key.SetValue("ModifyPath", $"\"{Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "iRacingCoach", "Installer", Program.RepairInstallerFileName)}\" --repair");
        key.SetValue("NoModify", 0, RegistryValueKind.DWord); key.SetValue("NoRepair", 0, RegistryValueKind.DWord);
    }

    private static void CreateShortcut(string shortcutPath, string executable, string workingDirectory)
    {
        var shellType = Type.GetTypeFromProgID("WScript.Shell") ?? throw new InvalidOperationException("Windows shortcut support is unavailable.");
        dynamic shell = Activator.CreateInstance(shellType)!;
        dynamic shortcut = shell.CreateShortcut(shortcutPath);
        shortcut.TargetPath = executable; shortcut.WorkingDirectory = workingDirectory; shortcut.IconLocation = executable; shortcut.Description = "iRacing Coach"; shortcut.Save();
    }
}
