"""Explicit, read-only Windows process memory reader for native snapshots.

Importing this module opens nothing. Runtime use requires an explicit PID and a
context manager; no process discovery, writes, privilege changes, or input occurs.
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import platform
import time
from dataclasses import dataclass

from . import native_snapshot

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
MAX_READ_SIZE_HARD = 1_048_576
MAX_READ_SIZE_DEFAULT = 65_536
_U64_MAX = (1 << 64) - 1
_USER_PTR_MIN = 0x10000


class ProcessReaderError(RuntimeError):
    """Process discovery, validation, or exact-read failure."""


@dataclass(frozen=True)
class ModuleInfo:
    base: int
    size: int
    path: str


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    path: str
    creation_filetime: int


class _WindowsBackend:
    """Small ctypes wrapper. Methods are invoked only after explicit construction."""

    def __init__(self):
        if os.name != "nt" or platform.architecture()[0] != "64bit":
            raise ProcessReaderError("native process reader requires 64-bit Windows")
        self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._bind()

    def _bind(self):
        k = self.k32
        k.CreateToolhelp32Snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        k.Module32FirstW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        k.Module32FirstW.restype = ctypes.c_int
        k.Module32NextW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        k.Module32NextW.restype = ctypes.c_int
        k.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        k.OpenProcess.restype = ctypes.c_void_p
        k.GetProcessId.argtypes = [ctypes.c_void_p]
        k.GetProcessId.restype = ctypes.c_uint32
        k.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                                  ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
        k.QueryFullProcessImageNameW.restype = ctypes.c_int
        k.GetProcessTimes.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                      ctypes.c_void_p, ctypes.c_void_p]
        k.GetProcessTimes.restype = ctypes.c_int
        k.ReadProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_void_p, ctypes.c_size_t,
                                        ctypes.POINTER(ctypes.c_size_t)]
        k.ReadProcessMemory.restype = ctypes.c_int
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        k.CloseHandle.restype = ctypes.c_int

    def close_handle(self, handle):
        if handle:
            self.k32.CloseHandle(ctypes.c_void_p(handle))

    def find_module(self, pid: int, module_name: str) -> ModuleInfo:
        class MODULEENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", ctypes.c_uint32), ("th32ModuleID", ctypes.c_uint32),
                ("th32ProcessID", ctypes.c_uint32), ("GlblcntUsage", ctypes.c_uint32),
                ("ProccntUsage", ctypes.c_uint32), ("modBaseAddr", ctypes.c_void_p),
                ("modBaseSize", ctypes.c_uint32), ("hModule", ctypes.c_void_p),
                ("szModule", ctypes.c_wchar * 256), ("szExePath", ctypes.c_wchar * 260),
            ]

        snap = self.k32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
        invalid = ctypes.c_void_p(-1).value
        if not snap or snap == invalid:
            raise ProcessReaderError(f"module snapshot failed for PID {pid}: {ctypes.get_last_error()}")
        try:
            entry = MODULEENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            if not self.k32.Module32FirstW(snap, ctypes.byref(entry)):
                raise ProcessReaderError(f"module enumeration failed for PID {pid}: {ctypes.get_last_error()}")
            while True:
                if entry.szModule.casefold() == module_name.casefold():
                    return ModuleInfo(int(entry.modBaseAddr or 0), int(entry.modBaseSize), entry.szExePath)
                entry.dwSize = ctypes.sizeof(entry)
                if not self.k32.Module32NextW(snap, ctypes.byref(entry)):
                    break
        finally:
            self.close_handle(snap)
        raise ProcessReaderError(f"required module {module_name} is missing from PID {pid}")

    def hash_file(self, path: str) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def open_process(self, pid: int):
        rights = PROCESS_VM_READ | PROCESS_QUERY_LIMITED_INFORMATION
        handle = self.k32.OpenProcess(rights, False, pid)
        if not handle:
            raise ProcessReaderError(f"OpenProcess(read/query-limited) failed for PID {pid}: {ctypes.get_last_error()}")
        return handle

    def process_identity(self, handle) -> ProcessIdentity:
        pid = int(self.k32.GetProcessId(handle))
        if not pid:
            raise ProcessReaderError(f"GetProcessId failed for process handle: {ctypes.get_last_error()}")
        capacity = ctypes.c_uint32(32768)
        path_buffer = ctypes.create_unicode_buffer(capacity.value)
        if not self.k32.QueryFullProcessImageNameW(handle, 0, path_buffer, ctypes.byref(capacity)):
            raise ProcessReaderError(f"QueryFullProcessImageNameW failed: {ctypes.get_last_error()}")

        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", ctypes.c_uint32), ("dwHighDateTime", ctypes.c_uint32)]

        creation, exit_time, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
        if not self.k32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time),
                                        ctypes.byref(kernel), ctypes.byref(user)):
            raise ProcessReaderError(f"GetProcessTimes failed: {ctypes.get_last_error()}")
        if exit_time.dwLowDateTime or exit_time.dwHighDateTime:
            raise ProcessReaderError("target process has exited")
        created = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
        return ProcessIdentity(pid, path_buffer.value, created)

    def read_process(self, handle, address: int, size: int) -> bytes:
        buf = ctypes.create_string_buffer(size)
        received = ctypes.c_size_t()
        ok = self.k32.ReadProcessMemory(handle, ctypes.c_void_p(address), buf, size,
                                        ctypes.byref(received))
        if not ok or received.value != size:
            raise ProcessReaderError(
                f"ReadProcessMemory short/failing read at 0x{address:x}: "
                f"{received.value}/{size} bytes, error {ctypes.get_last_error()}")
        return buf.raw[:size]


class NativeProcessReader:
    """Context-managed exact reader. Supply a backend only for controlled tests."""

    def __init__(self, pid: int, *, backend=None, module_name="iRacingSim64DX11.exe",
                 max_read_size: int = MAX_READ_SIZE_DEFAULT):
        if type(pid) is not int or not 1 <= pid <= 0xFFFFFFFF:
            raise ProcessReaderError("PID must be an explicit positive DWORD")
        if type(max_read_size) is not int or not 1 <= max_read_size <= MAX_READ_SIZE_HARD:
            raise ProcessReaderError("read size limit outside hard bounds")
        if module_name.casefold() != "iracingsim64dx11.exe":
            raise ProcessReaderError("only iRacingSim64DX11.exe is supported")
        self.pid = pid
        self._backend = backend
        self._module_name = module_name
        self._expected_sha256 = native_snapshot.PINNED_EXECUTABLE_SHA256
        self.max_read_size = max_read_size
        self.image_base = None
        self.image_size = None
        self.image_path = None
        self.executable_sha256 = None
        self._handle = None
        self._opened = False
        self._identity = None
        self.total_read_ns = 0
        self.max_read_ns = 0
        self.total_read_bytes = 0

    def __enter__(self):
        if self._opened:
            raise ProcessReaderError("reader is already open")
        backend = self._backend
        if backend is None:
            backend = _WindowsBackend()
            self._backend = backend
        try:
            self._handle = backend.open_process(self.pid)
            if not self._handle:
                raise ProcessReaderError("backend returned an invalid process handle")
            identity_before = backend.process_identity(self._handle)
            if identity_before.pid != self.pid:
                raise ProcessReaderError("opened handle PID does not match requested PID")
            module = backend.find_module(self.pid, self._module_name)
            if (not isinstance(module, ModuleInfo) or type(module.base) is not int or
                    not _USER_PTR_MIN <= module.base < (1 << 47) or
                    module.size != native_snapshot.IMAGE_MEMSPAN or not module.path):
                raise ProcessReaderError("module discovery returned invalid metadata")
            if os.path.normcase(module.path) != os.path.normcase(identity_before.path):
                raise ProcessReaderError("enumerated module path does not match opened process image")
            if os.path.basename(module.path).casefold() != self._module_name.casefold():
                raise ProcessReaderError("enumerated image is not the supported executable")
            self.image_base, self.image_size, self.image_path = module.base, module.size, module.path
            self.executable_sha256 = backend.hash_file(module.path)
            if self.executable_sha256 != self._expected_sha256:
                raise ProcessReaderError("installed executable SHA-256 does not match pinned build")
            identity_after = backend.process_identity(self._handle)
            module_after = backend.find_module(self.pid, self._module_name)
            if identity_after != identity_before:
                raise ProcessReaderError("process identity changed during executable validation")
            if module_after != module:
                raise ProcessReaderError("module identity changed during executable validation")
            if os.path.normcase(module_after.path) != os.path.normcase(identity_after.path):
                raise ProcessReaderError("revalidated module path does not match process handle")
            self._identity = identity_after
            self._opened = True
            return self
        except Exception:
            self.close()
            raise

    def read(self, address: int, size: int) -> bytes:
        if not self._opened or self._handle is None:
            raise ProcessReaderError("reader is closed")
        if (type(address) is not int or not _USER_PTR_MIN <= address < (1 << 47)):
            raise ProcessReaderError("read address is not canonical 64-bit")
        if type(size) is not int or not 1 <= size <= self.max_read_size:
            raise ProcessReaderError("read size outside configured limit")
        end = address + size - 1
        if end >= (1 << 47):
            raise ProcessReaderError("read address range is not canonical 64-bit")
        started = time.perf_counter_ns()
        data = self._backend.read_process(self._handle, address, size)
        elapsed = time.perf_counter_ns() - started
        self.total_read_ns += elapsed
        self.max_read_ns = max(self.max_read_ns, elapsed)
        if not isinstance(data, (bytes, bytearray, memoryview)) or len(data) != size:
            raise ProcessReaderError(f"short read at 0x{address:x}: expected {size} bytes")
        self.total_read_bytes += size
        return bytes(data)

    @property
    def read_timings(self) -> dict:
        return {"total_ns": self.total_read_ns, "max_ns": self.max_read_ns,
                "total_bytes": self.total_read_bytes}

    def close(self):
        handle, self._handle = self._handle, None
        self._opened = False
        self._identity = None
        if handle is not None and self._backend is not None:
            self._backend.close_handle(handle)

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def open_native_process(pid: int, **kwargs) -> NativeProcessReader:
    """Construct a reader for explicit use in ``with open_native_process(pid)``."""
    return NativeProcessReader(pid, **kwargs)
