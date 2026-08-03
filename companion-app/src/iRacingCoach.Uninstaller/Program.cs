using Microsoft.Win32;
using System.Diagnostics;
using System.Runtime.InteropServices;

namespace iRacingCoach.Uninstaller;

internal static class Program
{
    private const string RegistryPath = @"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\iRacing Coach";

    [STAThread]
    private static int Main(string[] args)
    {
        ApplicationConfiguration.Initialize();
        try
        {
            if (args.Length >= 2 && args[0] == "--test-clean-state")
            {
                RemoveTestOwnedState(ValidateTestStatePath(args[1]));
                return 0;
            }
            if (args.Length >= 2 && args[0] == "--test-run")
            {
                var target = ValidateTestPath(args[1]);
                var testRunner = Path.Combine(Path.GetTempPath(), $"iRacingCoach-Uninstall-TestRunner-{Guid.NewGuid():N}.exe");
                File.Copy(Environment.ProcessPath!, testRunner, overwrite: true);
                Process.Start(new ProcessStartInfo
                {
                    FileName = testRunner,
                    Arguments = $"--test-detached-run \"{target}\"",
                    UseShellExecute = false,
                    CreateNoWindow = true
                });
                return 0;
            }
            if (args.Length >= 2 && args[0] == "--test-detached-run")
            {
                var target = ValidateTestPath(args[1]);
                Thread.Sleep(700);
                RemoveInstallation(target, systemIntegration: false);
                ScheduleSelfDeletion();
                return 0;
            }
            if (args.Length >= 2 && args[0] == "--run")
            {
                var target = ValidateInstalledPath(args[1]);
                Thread.Sleep(700);
                RemoveInstallation(target, systemIntegration: true);
                MessageBox.Show(
                    $"iRacing Coach was removed. Your portable archive was kept at:\n\n{DurableArchivePath()}",
                    "iRacing Coach",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Information);
                ScheduleSelfDeletion();
                return 0;
            }

            if (MessageBox.Show(
                    $"Remove the iRacing Coach app?\n\nYour portable archive will be kept at:\n{DurableArchivePath()}\n\nThe uninstaller does not offer a delete-data option.",
                    "Uninstall iRacing Coach",
                    MessageBoxButtons.YesNo,
                    MessageBoxIcon.Question) != DialogResult.Yes)
            {
                return 0;
            }

            var installDirectory = ValidateInstalledPath(AppContext.BaseDirectory);
            var temporary = Path.Combine(Path.GetTempPath(), $"iRacingCoach-Uninstall-{Guid.NewGuid():N}.exe");
            File.Copy(Environment.ProcessPath!, temporary, overwrite: true);
            Process.Start(new ProcessStartInfo
            {
                FileName = temporary,
                Arguments = $"--run \"{installDirectory}\"",
                UseShellExecute = false,
                CreateNoWindow = true
            });
            return 0;
        }
        catch (Exception ex)
        {
            MessageBox.Show($"iRacing Coach could not be removed.\n\n{ex.Message}", "Uninstall iRacing Coach", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }

    private static string ValidateInstalledPath(string value)
    {
        var target = Path.GetFullPath(value).TrimEnd(Path.DirectorySeparatorChar);
        var perUserPrograms = Path.GetFullPath(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs"));
        if (!target.StartsWith(perUserPrograms + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase) ||
            !string.Equals(Path.GetFileName(target), "iRacing Coach", StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException("The install folder is not the expected per-user iRacing Coach folder.");
        }
        return target;
    }

    private static string ValidateTestPath(string value)
    {
        var target = Path.GetFullPath(value).TrimEnd(Path.DirectorySeparatorChar);
        var temporaryRoot = Path.GetFullPath(Path.GetTempPath()).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
        if (!target.StartsWith(temporaryRoot, StringComparison.OrdinalIgnoreCase) ||
            !Path.GetFileName(target).StartsWith("iRacingCoach-Installer-Test-", StringComparison.Ordinal))
        {
            throw new InvalidOperationException("Test removal is restricted to a named folder under the Windows temporary directory.");
        }
        return target;
    }

    private static string ValidateTestStatePath(string value)
    {
        var target = Path.GetFullPath(value).TrimEnd(Path.DirectorySeparatorChar);
        var temporaryRoot = Path.GetFullPath(Path.GetTempPath()).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
        if (!target.StartsWith(temporaryRoot, StringComparison.OrdinalIgnoreCase) ||
            !Path.GetFileName(target).StartsWith("iRacingCoach-Uninstall-Test-", StringComparison.Ordinal))
            throw new InvalidOperationException("Cleanup tests are restricted to a named folder under the Windows temporary directory.");
        return target;
    }

    private static void RemoveTestOwnedState(string sandbox)
    {
        var roots = new[]
        {
            Path.Combine(sandbox, "LocalAppData", "iRacingCoach"),
            Path.Combine(sandbox, "LocalAppData", "iRacing Coach"),
            Path.Combine(sandbox, "RoamingAppData", "iRacingCoach"),
            Path.Combine(sandbox, "RoamingAppData", "iRacing Coach"),
            Path.Combine(sandbox, "ProgramData", "iRacingCoach"),
            Path.Combine(sandbox, "ProgramFiles", "iRacing Coach")
        };
        foreach (var root in roots)
            DeleteExactDirectory(root, [Path.GetDirectoryName(root)!], Path.GetFileName(root));
    }

    private static void RemoveInstallation(string target, bool systemIntegration)
    {
        if (systemIntegration)
        {
            StopOwnedProcesses(target);
            DeleteShortcut(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), "iRacing Coach", "iRacing Coach.lnk"));
            DeleteShortcut(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "iRacing Coach.lnk"));
            using var currentUserUninstall = Registry.CurrentUser.OpenSubKey(@"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", writable: true);
            currentUserUninstall?.DeleteSubKeyTree("iRacing Coach", throwOnMissingSubKey: false);
            currentUserUninstall?.DeleteSubKeyTree("iRacingCoach", throwOnMissingSubKey: false);
            DeleteRegistryTree(Registry.CurrentUser, @"SOFTWARE\iRacingCoach");
            DeleteRegistryTree(Registry.CurrentUser, @"SOFTWARE\iRacing Coach");
            DeleteRegistryTree(Registry.CurrentUser, @"SOFTWARE\Classes\iRacingCoach");
            DeleteRegistryTree(Registry.CurrentUser, @"SOFTWARE\Classes\.iracingcoach");
            DeleteRegistryValue(Registry.CurrentUser, @"SOFTWARE\Microsoft\Windows\CurrentVersion\Run", "iRacing Coach");
            DeleteRegistryValue(Registry.CurrentUser, @"SOFTWARE\Microsoft\Windows\CurrentVersion\Run", "iRacingCoach");
            RemoveNamedWindowsIntegration();
            RemoveOwnedMachineState();
        }
        if (systemIntegration)
        {
            DeleteExactDirectory(target, [
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs")
            ], "iRacing Coach");
        }
        else if (Directory.Exists(target))
        {
            // ValidateTestPath already restricted this branch to the temporary
            // installer-test namespace.
            Directory.Delete(target, recursive: true);
        }
    }

    private static void RemoveOwnedMachineState()
    {
        DeleteExactDirectory(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "iRacingCoach"),
            [Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData)], "iRacingCoach");
        DeleteExactDirectory(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "iRacing Coach"),
            [Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData)], "iRacing Coach");
        DeleteExactDirectory(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "iRacing Coach"),
            [Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs")], "iRacing Coach");
        DeleteExactDirectory(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "iRacingCoach"),
            [Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData)], "iRacingCoach");
        DeleteExactDirectory(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "iRacing Coach"),
            [Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData)], "iRacing Coach");
        var startup = Environment.GetFolderPath(Environment.SpecialFolder.Startup);
        DeleteShortcut(Path.Combine(startup, "iRacing Coach.lnk"));
        DeleteShortcut(Path.Combine(startup, "iRacingCoach.lnk"));

        var crashRoot = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "CrashDumps");
        DeleteOwnedFiles(crashRoot, "iRacing Coach*.dmp");
        DeleteOwnedFiles(crashRoot, "iRacingCoach*.dmp");
        RemoveOwnedTemporaryArtifacts();
    }

    private static void RemoveOwnedTemporaryArtifacts()
    {
        var temporaryRoot = Path.GetFullPath(Path.GetTempPath()).TrimEnd(Path.DirectorySeparatorChar);
        if (!Directory.Exists(temporaryRoot)) return;
        foreach (var directory in Directory.EnumerateDirectories(temporaryRoot, "iRacingCoach-*", SearchOption.TopDirectoryOnly))
        {
            var leaf = Path.GetFileName(directory);
            if (!leaf.StartsWith("iRacingCoach-", StringComparison.OrdinalIgnoreCase)) continue;
            DeleteExactDirectory(directory, [temporaryRoot], leaf);
        }
        foreach (var file in Directory.EnumerateFiles(temporaryRoot, "iRacingCoach-*", SearchOption.TopDirectoryOnly))
        {
            var canonical = Path.GetFullPath(file);
            if (!string.Equals(canonical, Environment.ProcessPath, StringComparison.OrdinalIgnoreCase))
            {
                try { File.Delete(canonical); } catch (IOException) { }
            }
        }
    }

    private static void ScheduleSelfDeletion()
    {
        if (Environment.ProcessPath is not { } current) return;
        current = Path.GetFullPath(current);
        var temporaryRoot = Path.GetFullPath(Path.GetTempPath()).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
        if (!current.StartsWith(temporaryRoot, StringComparison.OrdinalIgnoreCase)) return;

        try
        {
            var helper = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), @"WindowsPowerShell\v1.0\powershell.exe");
            var start = new ProcessStartInfo
            {
                FileName = helper,
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden
            };
            start.ArgumentList.Add("-NoProfile");
            start.ArgumentList.Add("-NonInteractive");
            start.ArgumentList.Add("-WindowStyle");
            start.ArgumentList.Add("Hidden");
            start.ArgumentList.Add("-Command");
            var escapedCurrent = current.Replace("'", "''", StringComparison.Ordinal);
            start.ArgumentList.Add($"for ($i = 0; $i -lt 50; $i++) {{ Start-Sleep -Milliseconds 200; try {{ Remove-Item -LiteralPath '{escapedCurrent}' -Force -ErrorAction Stop; break }} catch {{ }} }}");
            Process.Start(start);
        }
        catch
        {
            _ = MoveFileEx(current, null, 4);
        }
    }

    private static void RemoveNamedWindowsIntegration()
    {
        foreach (var task in new[] { "iRacing Coach", "iRacingCoach" }) RunHidden("schtasks.exe", "/Delete", "/TN", task, "/F");
        foreach (var service in new[] { "iRacingCoach", "iRacing Coach" })
        {
            RunHidden("sc.exe", "stop", service);
            RunHidden("sc.exe", "delete", service);
        }
        foreach (var credential in new[] { "iRacingCoach.Garage61", "iRacingCoach.ChatGPT", "iRacingCoach" })
            RunHidden("cmdkey.exe", $"/delete:{credential}");
        RunHidden("netsh.exe", "advfirewall", "firewall", "delete", "rule", "name=iRacing Coach");
    }

    private static void RunHidden(string fileName, params string[] arguments)
    {
        try
        {
            using var process = new Process { StartInfo = new ProcessStartInfo(fileName) { UseShellExecute = false, CreateNoWindow = true, WindowStyle = ProcessWindowStyle.Hidden } };
            foreach (var argument in arguments) process.StartInfo.ArgumentList.Add(argument);
            process.Start();
            process.WaitForExit(3000);
        }
        catch (Exception ex) when (ex is InvalidOperationException or System.ComponentModel.Win32Exception) { }
    }

    private static void StopOwnedProcesses(string target)
    {
        var expected = Path.Combine(target, "iRacing Coach.exe");
        foreach (var process in Process.GetProcessesByName("iRacing Coach"))
        {
            using (process)
            {
                try
                {
                    if (!string.Equals(process.MainModule?.FileName, expected, StringComparison.OrdinalIgnoreCase)) continue;
                    if (process.CloseMainWindow() && process.WaitForExit(2000)) continue;
                    process.Kill(entireProcessTree: true);
                    process.WaitForExit(2000);
                }
                catch (Exception ex) when (ex is InvalidOperationException or System.ComponentModel.Win32Exception or NotSupportedException) { }
            }
        }
    }

    private static void DeleteExactDirectory(string path, IEnumerable<string> permittedParents, string expectedLeaf)
    {
        if (string.IsNullOrWhiteSpace(path) || string.IsNullOrWhiteSpace(expectedLeaf)) return;
        if (path.Contains('%') || path.StartsWith("\\\\", StringComparison.Ordinal) || path.StartsWith("\\\\?\\", StringComparison.Ordinal))
            throw new InvalidOperationException("Refusing to remove an unresolved, network, or device path.");
        var target = Path.GetFullPath(path).TrimEnd(Path.DirectorySeparatorChar);
        if (!string.Equals(Path.GetFileName(target), expectedLeaf, StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException($"Refusing to remove an unexpected folder: {target}");
        var allowed = permittedParents.Where(parent => !string.IsNullOrWhiteSpace(parent))
            .Select(parent => Path.GetFullPath(parent).TrimEnd(Path.DirectorySeparatorChar))
            .Any(parent => string.Equals(Path.GetDirectoryName(target), parent, StringComparison.OrdinalIgnoreCase));
        if (!allowed) throw new InvalidOperationException($"Refusing to remove a folder outside an exact app-owned root: {target}");
        var sensitiveRoots = new[]
        {
            Path.GetPathRoot(target),
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
            Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments),
            DurableArchivePath(),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "iRacing")
        }.Where(value => !string.IsNullOrWhiteSpace(value)).Select(value => Path.GetFullPath(value!).TrimEnd(Path.DirectorySeparatorChar));
        if (sensitiveRoots.Any(root => string.Equals(target, root, StringComparison.OrdinalIgnoreCase)))
            throw new InvalidOperationException("Refusing to remove a protected Windows, Documents, or iRacing root.");
        Debug.WriteLine($"Verified app-owned uninstall target: {target}");
        if (Directory.Exists(target)) Directory.Delete(target, recursive: true);
    }

    private static void DeleteOwnedFiles(string root, string pattern)
    {
        if (!Directory.Exists(root)) return;
        var canonicalRoot = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar);
        foreach (var file in Directory.EnumerateFiles(canonicalRoot, pattern, SearchOption.TopDirectoryOnly))
        {
            var canonical = Path.GetFullPath(file);
            if (!string.Equals(Path.GetDirectoryName(canonical), canonicalRoot, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("Refusing to remove a file outside the expected app-owned location.");
            File.Delete(canonical);
        }
    }

    private static void DeleteRegistryTree(RegistryKey hive, string path)
    {
        try { hive.DeleteSubKeyTree(path, throwOnMissingSubKey: false); }
        catch (UnauthorizedAccessException) { }
    }

    private static void DeleteRegistryValue(RegistryKey hive, string path, string valueName)
    {
        try
        {
            using var key = hive.OpenSubKey(path, writable: true);
            key?.DeleteValue(valueName, throwOnMissingValue: false);
        }
        catch (UnauthorizedAccessException) { }
    }

    private static string DurableArchivePath() => Path.GetFullPath(Path.Combine(KnownDocumentsPath(), "iRacing Coach")).TrimEnd(Path.DirectorySeparatorChar);

    private static string KnownDocumentsPath()
    {
        var folderId = new Guid("FDD39AD0-238F-46AF-ADB4-6C85480369C7");
        if (SHGetKnownFolderPath(folderId, 0, IntPtr.Zero, out var pointer) == 0)
        {
            try
            {
                var resolved = Marshal.PtrToStringUni(pointer);
                if (!string.IsNullOrWhiteSpace(resolved)) return resolved;
            }
            finally { Marshal.FreeCoTaskMem(pointer); }
        }
        return Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments);
    }

    private static void DeleteShortcut(string path)
    {
        if (File.Exists(path)) File.Delete(path);
        var directory = Path.GetDirectoryName(path);
        if (directory is not null && Directory.Exists(directory) && !Directory.EnumerateFileSystemEntries(directory).Any()) Directory.Delete(directory);
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool MoveFileEx(string existingFileName, string? newFileName, int flags);

    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    private static extern int SHGetKnownFolderPath([MarshalAs(UnmanagedType.LPStruct)] Guid folderId, uint flags, IntPtr token, out IntPtr path);
}

internal static class NativeTheme
{
    public static void ApplyDarkTitleBar(Form form)
    {
        var enabled = 1;
        var handle = form.Handle;
        if (DwmSetWindowAttribute(handle, 20, ref enabled, sizeof(int)) != 0)
            _ = DwmSetWindowAttribute(handle, 19, ref enabled, sizeof(int));
    }

    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(IntPtr window, int attribute, ref int value, int valueSize);
}
