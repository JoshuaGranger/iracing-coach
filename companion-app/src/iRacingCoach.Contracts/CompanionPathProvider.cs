using System.Runtime.InteropServices;

namespace iRacingCoach.Contracts;

public interface ICompanionPathProvider
{
    string UserProfile { get; }
    string Documents { get; }
    string Desktop { get; }
    string LocalApplicationData { get; }
    string ProgramFiles { get; }
    string ProgramFilesX86 { get; }
    IReadOnlyList<string> FixedDriveRoots { get; }
}

public sealed class WindowsCompanionPathProvider : ICompanionPathProvider
{
    private static readonly Guid DocumentsFolderId = new("FDD39AD0-238F-46AF-ADB4-6C85480369C7");

    public static WindowsCompanionPathProvider Instance { get; } = new();

    private WindowsCompanionPathProvider() { }

    public string UserProfile => Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
    public string Documents => ResolveDocuments();
    public string Desktop => Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
    public string LocalApplicationData => Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
    public string ProgramFiles => Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
    public string ProgramFilesX86 => Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86);
    public IReadOnlyList<string> FixedDriveRoots => DriveInfo.GetDrives()
        .Where(candidate => candidate.DriveType == DriveType.Fixed && candidate.IsReady)
        .Select(candidate => candidate.RootDirectory.FullName)
        .ToArray();

    private static string ResolveDocuments()
    {
        if (OperatingSystem.IsWindows() && SHGetKnownFolderPath(DocumentsFolderId, 0, IntPtr.Zero, out var pointer) == 0)
        {
            try
            {
                var resolved = Marshal.PtrToStringUni(pointer);
                if (!string.IsNullOrWhiteSpace(resolved)) return resolved;
            }
            finally
            {
                Marshal.FreeCoTaskMem(pointer);
            }
        }
        return Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments);
    }

    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    private static extern int SHGetKnownFolderPath([MarshalAs(UnmanagedType.LPStruct)] Guid folderId, uint flags, IntPtr token, out IntPtr path);
}

public sealed class IsolatedCompanionPathProvider : ICompanionPathProvider
{
    public IsolatedCompanionPathProvider(string root)
    {
        Root = Path.GetFullPath(root ?? throw new ArgumentNullException(nameof(root)));
        UserProfile = Path.Combine(Root, "profile");
        Documents = Path.Combine(UserProfile, "Documents");
        Desktop = Path.Combine(UserProfile, "Desktop");
        LocalApplicationData = Path.Combine(UserProfile, "AppData", "Local");
        ProgramFiles = Path.Combine(Root, "ProgramFiles");
        ProgramFilesX86 = Path.Combine(Root, "ProgramFilesX86");
        FixedDriveRoots = Array.Empty<string>();
    }

    public string Root { get; }
    public string UserProfile { get; }
    public string Documents { get; }
    public string Desktop { get; }
    public string LocalApplicationData { get; }
    public string ProgramFiles { get; }
    public string ProgramFilesX86 { get; }
    public IReadOnlyList<string> FixedDriveRoots { get; }
}

public static class WindowsKnownFolders
{
    public static string Documents => WindowsCompanionPathProvider.Instance.Documents;
}
