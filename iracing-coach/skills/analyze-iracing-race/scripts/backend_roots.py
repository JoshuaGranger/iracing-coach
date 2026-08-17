"""Portable resolution of the backend's iRacing, archive, and installation roots.

`IDENTITY-PATH-001` requires that no personal or developer literal decide where
the backend reads or writes, and that the roots resolve through the frozen
environment contract instead. Before this module three separate copies of the
same precedence chain lived in `workflow.py`, `tuning_workflow.py`, and
`mcp_server.py`, and a fourth, differently-ordered chain lived in `storage.py`.
Shipped configuration carried one developer's absolute paths, so a copy of the
repository resolved its iRacing root under a user account that did not exist on
the running machine.

This module is the single authority. Its precedence is:

1. an explicit caller argument;
2. the environment variable named in the frozen environment contract;
3. a genuinely configured value in `config/defaults.json`;
4. a portable default derived from the running user's profile.

Step 3 carries the one subtlety worth stating. A configured value that is
merely a *previously generated* default is not a user choice; it is the stale
literal this workstream exists to remove, and honoring it on a different
machine reintroduces the defect. Such a value is recognised by shape and
ignored, and the reason is reported rather than hidden. A configured value that
expresses a real choice, such as a simulator installed on another volume, is
preserved exactly. That is the "migrate defaults only when the old value is a
known generated default" rule, encoded.

Resolution is a pure function of its inputs. The environment mapping and the
configuration mapping are parameters, so a redirected profile, an absent
variable, and a non-default root are all directly testable without mutating the
real process environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Any, Mapping

try:  # Package import and direct script execution are both supported.
    from .path_security import local_path
except ImportError:  # pragma: no cover - normal CLI/MCP script-loading path.
    from path_security import local_path


#: Environment variable names, from the frozen environment contract published in
#: `contracts/compatibility.json`. They are named here once so a rename cannot
#: silently disagree between the resolver and the generated contract.
IRACING_ROOT_VARIABLE = "IRACING_COACH_IRACING_ROOT"
ARCHIVE_ROOT_VARIABLE = "IRACING_COACH_DATA"
INSTALL_ROOT_VARIABLE = "IRACING_COACH_INSTALL_ROOT"

#: Configuration keys in `config/defaults.json`.
IRACING_ROOT_KEY = "iracing_root"
ARCHIVE_ROOT_KEY = "archive_root"
INSTALL_ROOT_KEY = "install_root"

#: Path of each portable default relative to the running user's profile. These
#: are the `%USERPROFILE%\\Documents\\...` data boundary the product contract
#: keeps; only the profile itself is resolved at runtime.
IRACING_ROOT_RELATIVE = ("Documents", "iRacing")
ARCHIVE_ROOT_RELATIVE = ("Documents", "iRacing Coach", "data")

#: Tail of the well-known installation default. Unlike the two profile roots
#: this one hangs off a machine directory rather than a user directory.
INSTALL_ROOT_RELATIVE = ("iRacing",)

#: Sources a resolution can come from, most to least specific.
SOURCE_ARGUMENT = "explicit-argument"
SOURCE_ENVIRONMENT = "environment"
SOURCE_CONFIGURATION = "configuration"
SOURCE_PORTABLE_DEFAULT = "portable-default"


class BackendRootError(ValueError):
    """A root could not be resolved to a usable local path."""


@dataclass(frozen=True)
class RootResolution:
    """One resolved root together with the evidence for where it came from.

    Callers that only need a path use :attr:`path`. Diagnostics and the
    portability tests use the remaining fields, which is why a resolution is
    returned instead of a bare path: "the archive is here" and "the archive is
    here because nothing was configured" are different claims, and only the
    second one can be checked.
    """

    path: Path
    source: str
    #: Set when a configured value was present but deliberately not used,
    #: carrying the reason. `None` whenever the configuration was honored or
    #: absent.
    ignored_configuration: str | None = None
    #: The variable consulted, for an environment resolution.
    variable: str | None = None

    @property
    def is_default(self) -> bool:
        return self.source == SOURCE_PORTABLE_DEFAULT


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _environment(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def _non_empty(value: Any) -> str | None:
    """Return a stripped non-empty string, or `None`.

    An empty or whitespace-only override is treated as absent rather than as an
    error, matching the prior behavior of every call site: a variable that is
    set to nothing does not express a location.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        text = os.fspath(value) if isinstance(value, (str, PurePath, os.PathLike)) else str(value)
    except TypeError:
        return None
    text = text.strip()
    return text or None


def user_profile_root(environ: Mapping[str, str] | None = None) -> Path:
    """Return the running user's profile directory.

    `USERPROFILE` is consulted first because it is what an isolated or
    redirected profile sets, and because it is the value the .NET host and the
    development sandbox agree on. `Path.home()` is the fallback for a process
    whose environment omits it. Neither branch can produce a hardcoded account
    name.
    """
    profile = _non_empty(_environment(environ).get("USERPROFILE"))
    if profile is not None:
        return Path(profile).expanduser()
    return Path.home()


def _program_files_x86(environ: Mapping[str, str] | None = None) -> Path | None:
    """Return the 32-bit program-files directory, or `None` when unknown.

    Returning `None` rather than a `C:\\Program Files (x86)` literal is
    deliberate: on a machine whose system drive is not `C:` the literal is
    wrong, and a wrong path presented as a default is exactly the class of
    silent untruth this workstream removes.
    """
    values = _environment(environ)
    for name in ("PROGRAMFILES(X86)", "ProgramFiles(x86)", "PROGRAMFILES"):
        candidate = _non_empty(values.get(name))
        if candidate is not None:
            return Path(candidate).expanduser()
    return None


def _casefold_parts(path: Path) -> tuple[str, ...]:
    return tuple(os.path.normcase(part) for part in path.parts)


def _has_profile_shaped_head(path: Path, tail_length: int) -> bool:
    """Return whether `path` looks like `<drive>\\Users\\<name>\\<tail>`.

    This is the shape the removed literals had, and requiring it keeps the
    stale-default rule narrow. A deliberate configuration such as
    `D:\\Backup\\Documents\\iRacing` shares the tail but has no `Users`
    segment in the required position, so it is preserved rather than discarded.
    """
    parts = _casefold_parts(path)
    if len(parts) < tail_length + 2:
        return False
    head = parts[: len(parts) - tail_length]
    # head[-1] is the account name and head[-2] must be the profile container.
    return head[-2] == os.path.normcase("Users")


def _matches_tail(path: Path, relative: tuple[str, ...]) -> bool:
    parts = _casefold_parts(path)
    tail = tuple(os.path.normcase(part) for part in relative)
    return len(parts) >= len(tail) and parts[len(parts) - len(tail) :] == tail


def _is_generated_profile_default(path: Path, relative: tuple[str, ...]) -> bool:
    """Return whether `path` is a previously generated profile-rooted default."""
    return _matches_tail(path, relative) and _has_profile_shaped_head(path, len(relative))


def _is_generated_install_default(
    path: Path, environ: Mapping[str, str] | None = None
) -> bool:
    """Return whether `path` is a previously generated installation default.

    The installation default is not profile-rooted, so it is recognised either
    by equalling the machine's own program-files default or by carrying the
    well-known `Program Files (x86)\\iRacing` tail on any drive.
    """
    if not _matches_tail(path, INSTALL_ROOT_RELATIVE):
        return False
    program_files = _program_files_x86(environ)
    if program_files is not None:
        expected = program_files.joinpath(*INSTALL_ROOT_RELATIVE)
        if _casefold_parts(path) == _casefold_parts(expected):
            return True
    parts = _casefold_parts(path)
    if len(parts) < 2:
        return False
    return parts[-2] in {
        os.path.normcase("Program Files (x86)"),
        os.path.normcase("Program Files"),
    }


def _resolve(
    label: str,
    *,
    explicit: Any,
    variable: str,
    key: str,
    environ: Mapping[str, str] | None,
    defaults: Mapping[str, Any] | None,
    default_factory,
    is_generated_default,
) -> RootResolution:
    explicit_value = _non_empty(explicit)
    if explicit_value is not None:
        return RootResolution(local_path(explicit_value, label), SOURCE_ARGUMENT)

    override = _non_empty(_environment(environ).get(variable))
    if override is not None:
        return RootResolution(
            local_path(override, variable), SOURCE_ENVIRONMENT, variable=variable
        )

    configured = _non_empty(_mapping(defaults).get(key))
    if configured is not None:
        candidate = Path(configured).expanduser()
        if not is_generated_default(candidate):
            return RootResolution(
                local_path(configured, f"configured {label}"), SOURCE_CONFIGURATION
            )
        ignored = (
            f"{key!r} in configuration is a previously generated default "
            f"({configured!r}) rather than a chosen location, so the portable "
            f"default for this machine is used instead"
        )
        return RootResolution(
            local_path(default_factory(), label),
            SOURCE_PORTABLE_DEFAULT,
            ignored_configuration=ignored,
        )

    return RootResolution(local_path(default_factory(), label), SOURCE_PORTABLE_DEFAULT)


def resolve_iracing_root(
    explicit: Any = None,
    *,
    environ: Mapping[str, str] | None = None,
    defaults: Mapping[str, Any] | None = None,
) -> RootResolution:
    """Resolve the local iRacing Documents root."""
    return _resolve(
        "iRacing root",
        explicit=explicit,
        variable=IRACING_ROOT_VARIABLE,
        key=IRACING_ROOT_KEY,
        environ=environ,
        defaults=defaults,
        default_factory=lambda: user_profile_root(environ).joinpath(*IRACING_ROOT_RELATIVE),
        is_generated_default=lambda path: _is_generated_profile_default(
            path, IRACING_ROOT_RELATIVE
        ),
    )


def resolve_archive_root(
    explicit: Any = None,
    *,
    environ: Mapping[str, str] | None = None,
    defaults: Mapping[str, Any] | None = None,
) -> RootResolution:
    """Resolve the backend-owned archive root."""
    return _resolve(
        "archive_root",
        explicit=explicit,
        variable=ARCHIVE_ROOT_VARIABLE,
        key=ARCHIVE_ROOT_KEY,
        environ=environ,
        defaults=defaults,
        default_factory=lambda: user_profile_root(environ).joinpath(*ARCHIVE_ROOT_RELATIVE),
        is_generated_default=lambda path: _is_generated_profile_default(
            path, ARCHIVE_ROOT_RELATIVE
        ),
    )


def resolve_install_root(
    explicit: Any = None,
    *,
    environ: Mapping[str, str] | None = None,
    defaults: Mapping[str, Any] | None = None,
) -> RootResolution | None:
    """Resolve the read-only iRacing installation root, or `None` when unknown.

    Unlike the other two roots this one has no guaranteed default. When the
    environment does not name a program-files directory and nothing is
    configured, the honest answer is that the installation location is unknown,
    so `None` is returned and the caller omits the candidate rather than
    inventing a path that probably does not exist.
    """
    explicit_value = _non_empty(explicit)
    if explicit_value is not None:
        return RootResolution(local_path(explicit_value, "install root"), SOURCE_ARGUMENT)

    override = _non_empty(_environment(environ).get(INSTALL_ROOT_VARIABLE))
    if override is not None:
        return RootResolution(
            local_path(override, INSTALL_ROOT_VARIABLE),
            SOURCE_ENVIRONMENT,
            variable=INSTALL_ROOT_VARIABLE,
        )

    program_files = _program_files_x86(environ)
    default_path = (
        program_files.joinpath(*INSTALL_ROOT_RELATIVE) if program_files is not None else None
    )

    configured = _non_empty(_mapping(defaults).get(INSTALL_ROOT_KEY))
    if configured is not None:
        candidate = Path(configured).expanduser()
        if not _is_generated_install_default(candidate, environ):
            return RootResolution(
                local_path(configured, "configured install root"), SOURCE_CONFIGURATION
            )
        if default_path is None:
            return None
        return RootResolution(
            local_path(default_path, "install root"),
            SOURCE_PORTABLE_DEFAULT,
            ignored_configuration=(
                f"{INSTALL_ROOT_KEY!r} in configuration is a previously generated "
                f"default ({configured!r}) rather than a chosen location, so the "
                f"portable default for this machine is used instead"
            ),
        )

    if default_path is None:
        return None
    return RootResolution(local_path(default_path, "install root"), SOURCE_PORTABLE_DEFAULT)


def iracing_root_path(
    explicit: Any = None,
    *,
    environ: Mapping[str, str] | None = None,
    defaults: Mapping[str, Any] | None = None,
) -> Path:
    """Convenience wrapper for call sites that need only the path."""
    return resolve_iracing_root(explicit, environ=environ, defaults=defaults).path


def archive_root_path(
    explicit: Any = None,
    *,
    environ: Mapping[str, str] | None = None,
    defaults: Mapping[str, Any] | None = None,
) -> Path:
    """Convenience wrapper for call sites that need only the path."""
    return resolve_archive_root(explicit, environ=environ, defaults=defaults).path
