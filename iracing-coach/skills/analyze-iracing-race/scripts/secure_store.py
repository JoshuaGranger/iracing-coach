"""Garage61 credential loading for the companion app and command-line tools.

On a Windows desktop the personal access token is stored only in the Windows
user-bound DPAPI store. On the server the worker reads it from a root-only
secret file whose path (never the token) is named by
``IRACING_COACH_GARAGE61_TOKEN_FILE``; that route is checked first and needs no
Windows. Portable settings and archives never contain credentials. Tokens are
never placed in command-line arguments, environment variables, or diagnostic
output.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Final, Mapping


class SecureStoreError(RuntimeError):
    """Raised when the Garage61 credential cannot be stored or loaded safely."""


TOKEN_FILE_VARIABLE: Final[str] = "IRACING_COACH_GARAGE61_TOKEN_FILE"
STORAGE_DPAPI: Final[str] = "windows-user-dpapi"
STORAGE_TOKEN_FILE: Final[str] = "token-file"
#: A personal access token is far shorter; anything larger is not a token file.
_TOKEN_FILE_MAX_BYTES: Final[int] = 4096


def _token_file(environ: Mapping[str, str] | None = None) -> Path | None:
    value = (os.environ if environ is None else environ).get(TOKEN_FILE_VARIABLE, "").strip()
    return Path(value) if value else None


def credential_storage(environ: Mapping[str, str] | None = None) -> str:
    """Name the credential route in use: the server token file, else DPAPI."""

    return STORAGE_TOKEN_FILE if _token_file(environ) is not None else STORAGE_DPAPI


def _load_token_file(path: Path) -> str:
    try:
        status = path.stat()
    except FileNotFoundError:
        raise SecureStoreError(
            f"Garage61 is not configured: the token file named by {TOKEN_FILE_VARIABLE} does not exist."
        ) from None
    except OSError as exc:
        raise SecureStoreError(f"Garage61 token file is unreadable ({type(exc).__name__}).") from None
    if not stat.S_ISREG(status.st_mode):
        raise SecureStoreError("Garage61 token file is not a regular file.")
    if os.name != "nt" and status.st_mode & 0o077:
        raise SecureStoreError("Garage61 token file must be readable by its owner only (mode 0600).")
    if status.st_size > _TOKEN_FILE_MAX_BYTES:
        raise SecureStoreError("Garage61 token file is too large to hold a token.")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SecureStoreError(f"Garage61 token file is unreadable ({type(exc).__name__}).") from None
    try:
        token = raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise SecureStoreError("The Garage61 token file is not UTF-8 text.") from None
    if not token:
        raise SecureStoreError("The Garage61 token file is empty.")
    if any(ord(character) < 32 for character in token):
        raise SecureStoreError("The Garage61 token file is malformed.")
    return token


def _default_local_app_data() -> Path:
    configured = os.environ.get("LOCALAPPDATA")
    if configured:
        return Path(configured)
    # This fallback makes the location deterministic for diagnostics and tests.
    # Actual credential operations remain Windows-only.
    return Path.home() / "AppData" / "Local"


DEFAULT_CREDENTIAL_PATH: Final[Path] = (
    _default_local_app_data()
    / "iRacingCoach"
    / "credentials"
    / "garage61.pat.dpapi"
)

def _is_windows() -> bool:
    return os.name == "nt"


def _powershell_executable() -> str:
    executable = shutil.which("powershell.exe") or shutil.which("powershell")
    if executable is None:
        raise SecureStoreError(
            "Windows PowerShell is required to access the Garage61 credential store."
        )
    return executable


def _configuration_script() -> Path:
    script = Path(__file__).with_name("configure-garage61.ps1")
    if not script.is_file():
        raise SecureStoreError(f"Garage61 configuration script is missing: {script}")
    return script


def _credential_path(path: str | os.PathLike[str] | None) -> Path:
    selected = Path(path) if path is not None else DEFAULT_CREDENTIAL_PATH
    return selected.expanduser().resolve(strict=False)


def credential_exists(path: str | os.PathLike[str] | None = None) -> bool:
    """Return whether a Garage61 credential exists (token file, else DPAPI blob)."""

    token_file = _token_file() if path is None else None
    if token_file is not None:
        return token_file.is_file()
    return _credential_path(path).is_file()


def store_token(
    token: str,
    path: str | os.PathLike[str] | None = None,
) -> Path:
    """Encrypt and store *token* with user-bound Windows DPAPI.

    The token is transmitted to the local PowerShell process over stdin.  It is
    deliberately never included in the process argument list or an exception.
    """

    if not _is_windows():
        raise SecureStoreError("Garage61 DPAPI credential storage is Windows-only.")
    if not isinstance(token, str):
        raise TypeError("Garage61 token must be a string.")
    normalized = token.strip()
    if not normalized:
        raise ValueError("Garage61 token cannot be empty.")
    if "\r" in normalized or "\n" in normalized or "\x00" in normalized:
        raise ValueError("Garage61 token contains an invalid control character.")

    credential_path = _credential_path(path)
    command = [
        _powershell_executable(),
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(_configuration_script()),
        "-CredentialPath",
        str(credential_path),
        "-FromStdin",
        "-Quiet",
    ]
    try:
        completed = subprocess.run(
            command,
            input=normalized + "\n",
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SecureStoreError(
            "Unable to start Windows PowerShell to store the Garage61 credential."
        ) from exc
    if completed.returncode != 0:
        # Never include stdout: the read mode of the script intentionally emits
        # plaintext there, and defensive error handling should not risk leakage.
        detail = _safe_powershell_error(completed.stderr)
        raise SecureStoreError(f"Garage61 credential storage failed{detail}.")
    return credential_path


def configure_interactively(
    path: str | os.PathLike[str] | None = None,
) -> Path:
    """Prompt for and save a token without echoing it to the console."""

    if not _is_windows():
        raise SecureStoreError("Garage61 DPAPI credential storage is Windows-only.")
    credential_path = _credential_path(path)
    command = [
        _powershell_executable(),
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(_configuration_script()),
        "-CredentialPath",
        str(credential_path),
    ]
    try:
        completed = subprocess.run(command, check=False, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SecureStoreError(
            "Unable to start the interactive Garage61 credential prompt."
        ) from exc
    if completed.returncode != 0:
        raise SecureStoreError("Garage61 credential configuration did not complete.")
    return credential_path


def load_token(path: str | os.PathLike[str] | None = None) -> str:
    """Return the Garage61 token from the server token file, else from DPAPI.

    With ``IRACING_COACH_GARAGE61_TOKEN_FILE`` set (and no explicit *path*) the
    token is that file's single line. Otherwise DPAPI decrypts it for the
    current Windows user; DPAPI prevents another Windows user, or a copied
    credential on another machine, from decrypting the file. The plaintext
    exists only in the local process memories and their private stdout pipe.
    """

    token_file = _token_file() if path is None else None
    if token_file is not None:
        return _load_token_file(token_file)
    if not _is_windows():
        raise SecureStoreError("Garage61 DPAPI credential loading is Windows-only.")
    credential_path = _credential_path(path)
    if not credential_path.is_file():
        raise SecureStoreError(
            "Garage61 is not configured. Run configure-garage61.ps1 once."
        )
    command = [
        _powershell_executable(),
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(_configuration_script()),
        "-CredentialPath",
        str(credential_path),
        "-ReadToken",
        "-Quiet",
    ]
    try:
        completed = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SecureStoreError(
            "Unable to start Windows PowerShell to load the Garage61 credential."
        ) from exc
    if completed.returncode != 0:
        detail = _safe_powershell_error(completed.stderr)
        raise SecureStoreError(f"Garage61 credential loading failed{detail}.")
    token = completed.stdout.strip()
    if not token:
        raise SecureStoreError(
            "The Garage61 credential decrypted to an empty value; configure it again."
        )
    if "\r" in token or "\n" in token or "\x00" in token:
        raise SecureStoreError("The stored Garage61 credential is malformed.")
    return token


def _safe_powershell_error(stderr: str | None) -> str:
    """Return a short, single-line PowerShell error without echoing data."""

    if not stderr:
        return ""
    first_line = next((line.strip() for line in stderr.splitlines() if line.strip()), "")
    if not first_line:
        return ""
    # PowerShell errors from this script contain only paths and fixed messages.
    # Bound length to keep logs predictable and avoid accidental verbose dumps.
    return f": {first_line[:240]}"


__all__ = [
    "DEFAULT_CREDENTIAL_PATH",
    "STORAGE_DPAPI",
    "STORAGE_TOKEN_FILE",
    "SecureStoreError",
    "TOKEN_FILE_VARIABLE",
    "configure_interactively",
    "credential_exists",
    "credential_storage",
    "load_token",
    "store_token",
]
