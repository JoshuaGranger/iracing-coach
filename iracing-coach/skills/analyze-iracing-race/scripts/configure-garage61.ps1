[CmdletBinding(DefaultParameterSetName = 'Interactive')]
param(
    [Parameter()]
    [string]$CredentialPath = (Join-Path $env:LOCALAPPDATA 'iRacingCoach\credentials\garage61.pat.dpapi'),

    [Parameter(ParameterSetName = 'Stdin')]
    [switch]$FromStdin,

    [Parameter(ParameterSetName = 'Read')]
    [switch]$ReadToken,

    [Parameter()]
    [switch]$Quiet
)

<#
.SYNOPSIS
Stores or reads a Garage61 personal access token using Windows user-bound DPAPI.

.DESCRIPTION
Interactive use prompts with Read-Host -AsSecureString, so the token is never
echoed. The Python secure_store module uses -FromStdin or -ReadToken through a
private process pipe; it never places plaintext credentials in command-line
arguments, environment variables, or files.
#>

$ErrorActionPreference = 'Stop'

if (-not $IsWindows -and $PSVersionTable.PSEdition -eq 'Core') {
    throw 'Garage61 DPAPI credential storage is Windows-only.'
}

function Resolve-CredentialPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        throw 'CredentialPath cannot be empty.'
    }
    return [System.IO.Path]::GetFullPath($Path)
}

function Ensure-CredentialDirectory {
    param([Parameter(Mandatory = $true)][string]$Directory)

    if (-not (Test-Path -LiteralPath $Directory -PathType Container)) {
        [void][System.IO.Directory]::CreateDirectory($Directory)
    }
}

function Protect-CredentialFile {
    param([Parameter(Mandatory = $true)][string]$Path)

    $currentSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    $icaclsPath = Join-Path $env:WINDIR 'System32\icacls.exe'
    $currentGrant = '*' + $currentSid.Value + ':F'
    $systemGrant = '*S-1-5-18:F'

    # Set-Acl can require SeSecurityPrivilege when handed a newly constructed
    # security object. icacls updates only this file's DACL and works for a
    # non-admin current user while still removing inherited access. Restricting
    # only the credential avoids surprising ACL changes if a custom parent
    # directory was supplied.
    & $icaclsPath $Path '/inheritance:r' '/grant:r' $currentGrant $systemGrant | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Unable to restrict access to the Garage61 credential file.'
    }
}

function ConvertTo-PlainText {
    param([Parameter(Mandatory = $true)][Security.SecureString]$SecureValue)

    $bstr = [IntPtr]::Zero
    try {
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureValue)
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    }
    finally {
        if ($bstr -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        }
    }
}

$resolvedPath = Resolve-CredentialPath -Path $CredentialPath

if ($ReadToken) {
    if (-not (Test-Path -LiteralPath $resolvedPath -PathType Leaf)) {
        throw 'Garage61 credential file does not exist. Run this script interactively first.'
    }
    $encrypted = [System.IO.File]::ReadAllText($resolvedPath).Trim()
    if ([string]::IsNullOrWhiteSpace($encrypted)) {
        throw 'Garage61 credential file is empty.'
    }
    $secureValue = ConvertTo-SecureString -String $encrypted
    $plainValue = ConvertTo-PlainText -SecureValue $secureValue
    try {
        [Console]::Out.Write($plainValue)
    }
    finally {
        $plainValue = $null
        $secureValue.Dispose()
    }
    exit 0
}

if ($FromStdin) {
    $plainInput = [Console]::In.ReadLine()
    if ([string]::IsNullOrWhiteSpace($plainInput)) {
        throw 'Garage61 token cannot be empty.'
    }
    $plainInput = $plainInput.Trim()
    $secureToken = ConvertTo-SecureString -String $plainInput -AsPlainText -Force
    $plainInput = $null
}
else {
    if (-not $Quiet) {
        Write-Host 'Create or reveal a personal access token in Garage61 My applications.'
        Write-Host 'Paste it below. It will not be displayed or written as plaintext.'
    }
    $secureToken = Read-Host 'Garage61 personal access token' -AsSecureString
}

$validationValue = ConvertTo-PlainText -SecureValue $secureToken
try {
    if ([string]::IsNullOrWhiteSpace($validationValue)) {
        throw 'Garage61 token cannot be empty.'
    }
}
finally {
    $validationValue = $null
}

$parentDirectory = Split-Path -Parent $resolvedPath
Ensure-CredentialDirectory -Directory $parentDirectory

$encryptedToken = ConvertFrom-SecureString -SecureString $secureToken
$secureToken.Dispose()
$temporaryPath = Join-Path $parentDirectory ('.garage61-' + [Guid]::NewGuid().ToString('N') + '.tmp')

try {
    $utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($temporaryPath, $encryptedToken, $utf8WithoutBom)
    Move-Item -LiteralPath $temporaryPath -Destination $resolvedPath -Force
    Protect-CredentialFile -Path $resolvedPath
}
finally {
    $encryptedToken = $null
    if (Test-Path -LiteralPath $temporaryPath -PathType Leaf) {
        Remove-Item -LiteralPath $temporaryPath -Force
    }
}

if (-not $Quiet) {
    Write-Host "Garage61 credential stored for the current Windows user at: $resolvedPath"
    Write-Host 'No plaintext token was written to disk.'
}
