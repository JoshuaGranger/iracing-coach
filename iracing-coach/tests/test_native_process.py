"""Portable backend tests; never opens a real process or calls Windows APIs."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'parsers'))
from coach_parsers import native_process as np
from coach_parsers import native_snapshot as ns


class Backend:
    def __init__(self, *, module=True, digest=None, read_result=None,
                 identities=None, modules=None):
        self.module = module
        self.digest = digest or ns.PINNED_EXECUTABLE_SHA256
        self.read_result = read_result
        self.identities = list(identities or [np.ProcessIdentity(42, r"C:\sim\iRacingSim64DX11.exe", 123)])
        self.modules = list(modules or [np.ModuleInfo(0x1_4000_0000, ns.IMAGE_MEMSPAN,
                                                       r"C:\sim\iRacingSim64DX11.exe")])
        self.calls = []
        self.closed = []

    def find_module(self, pid, module_name):
        self.calls.append(("find_module", pid, module_name))
        if not self.module:
            raise np.ProcessReaderError("required module missing")
        return self.modules.pop(0) if len(self.modules) > 1 else self.modules[0]

    def hash_file(self, path):
        self.calls.append(("hash_file", path))
        return self.digest

    def open_process(self, pid):
        self.calls.append(("open_process", pid))
        return 0x1234

    def process_identity(self, handle):
        self.calls.append(("process_identity", handle))
        return self.identities.pop(0) if len(self.identities) > 1 else self.identities[0]

    def read_process(self, handle, address, size):
        self.calls.append(("read_process", handle, address, size))
        return self.read_result if self.read_result is not None else bytes([0xAB]) * size

    def close_handle(self, handle):
        self.closed.append(handle)


class NativeProcessReaderTests(unittest.TestCase):
    def test_image_paths_use_windows_semantics_on_every_test_host(self):
        backend = Backend(
            identities=[np.ProcessIdentity(42, r"C:\SIM\iRacingSim64DX11.exe", 123)],
            modules=[np.ModuleInfo(0x1_4000_0000, ns.IMAGE_MEMSPAN,
                                   "c:/sim/iracingsim64dx11.exe")])
        with np.open_native_process(42, backend=backend) as reader:
            self.assertEqual(reader.read(reader.image_base, 1), b"\xab")
        self.assertEqual(backend.closed, [0x1234])

    def test_preserves_module_base_above_four_gib(self):
        backend = Backend()
        with np.open_native_process(42, backend=backend) as reader:
            self.assertEqual(reader.image_base, 0x1_4000_0000)
            self.assertEqual(reader.executable_sha256, ns.PINNED_EXECUTABLE_SHA256)
            self.assertEqual(reader.read(reader.image_base + 0x1000, 4), b"\xab" * 4)
            self.assertGreaterEqual(reader.read_timings["total_ns"], 0)
            self.assertEqual(reader.read_timings["total_bytes"], 4)
        self.assertEqual(backend.closed, [0x1234])
        self.assertIn(("open_process", 42), backend.calls)

    def test_wrong_hash_precedes_foreign_memory_read(self):
        backend = Backend(digest="0" * 64)
        with self.assertRaisesRegex(np.ProcessReaderError, "SHA-256"):
            with np.open_native_process(42, backend=backend):
                self.fail("hash mismatch must reject before entering")
        self.assertFalse(any(call[0] == "read_process" for call in backend.calls))
        self.assertEqual(backend.calls[0], ("open_process", 42))
        self.assertEqual(backend.closed, [0x1234])

    def test_missing_module_fails_and_closes_opened_handle(self):
        backend = Backend(module=False)
        with self.assertRaisesRegex(np.ProcessReaderError, "missing"):
            with np.open_native_process(42, backend=backend):
                pass
        self.assertEqual([x[0] for x in backend.calls],
                         ["open_process", "process_identity", "find_module"])
        self.assertEqual(backend.closed, [0x1234])

    def test_invalid_pid_fails_without_backend_calls(self):
        backend = Backend()
        for pid in (0, -1, 2**32, True, "42"):
            with self.subTest(pid=pid), self.assertRaises(np.ProcessReaderError):
                np.open_native_process(pid, backend=backend)
        self.assertEqual(backend.calls, [])

    def test_exact_short_read_is_refused_and_handle_closes(self):
        backend = Backend(read_result=b"x")
        with np.open_native_process(42, backend=backend) as reader:
            with self.assertRaisesRegex(np.ProcessReaderError, "short read"):
                reader.read(0x1_4000_0000, 2)
        self.assertEqual(backend.closed, [0x1234])

    def test_closed_reader_and_read_limits(self):
        backend = Backend()
        reader = np.NativeProcessReader(42, backend=backend, max_read_size=8)
        with reader:
            for address, size in ((0x1_4000_0000, 0), (0x1_4000_0000, 9), (0x1000, 1),
                                  (0x0000800000000000, 1), (0x7FFFFFFFFFFF, 2)):
                with self.subTest(address=address, size=size), self.assertRaises(np.ProcessReaderError):
                    reader.read(address, size)
        reader.close()  # Idempotent.
        self.assertEqual(backend.closed, [0x1234])
        with self.assertRaisesRegex(np.ProcessReaderError, "closed"):
            reader.read(0x1_4000_0000, 1)

    def test_invalid_constructor_limits_and_module_name(self):
        for kw in ({"max_read_size": np.MAX_READ_SIZE_HARD + 1},
                   {"module_name": "other.exe"}):
            with self.subTest(kw=kw), self.assertRaises(np.ProcessReaderError):
                np.NativeProcessReader(42, backend=Backend(), **kw)

    def test_handle_identity_change_detects_pid_reuse_and_closes(self):
        stable = np.ProcessIdentity(42, r"C:\sim\iRacingSim64DX11.exe", 123)
        reused = np.ProcessIdentity(42, r"C:\sim\iRacingSim64DX11.exe", 124)
        backend = Backend(identities=[stable, reused])
        with self.assertRaisesRegex(np.ProcessReaderError, "identity changed"):
            with np.open_native_process(42, backend=backend):
                pass
        self.assertFalse(any(call[0] == "read_process" for call in backend.calls))
        self.assertEqual(backend.closed, [0x1234])

    def test_handle_pid_mismatch_is_refused_and_closed(self):
        backend = Backend(identities=[np.ProcessIdentity(43, r"C:\sim\iRacingSim64DX11.exe", 123)])
        with self.assertRaisesRegex(np.ProcessReaderError, "PID does not match"):
            with np.open_native_process(42, backend=backend):
                pass
        self.assertEqual(backend.closed, [0x1234])

    def test_module_change_or_wrong_size_is_refused_and_closes(self):
        original = np.ModuleInfo(0x1_4000_0000, ns.IMAGE_MEMSPAN,
                                 r"C:\sim\iRacingSim64DX11.exe")
        changed = np.ModuleInfo(0x1_5000_0000, ns.IMAGE_MEMSPAN,
                                r"C:\sim\iRacingSim64DX11.exe")
        backend = Backend(modules=[original, changed])
        with self.assertRaisesRegex(np.ProcessReaderError, "module identity changed"):
            with np.open_native_process(42, backend=backend):
                pass
        self.assertEqual(backend.closed, [0x1234])
        backend = Backend(modules=[np.ModuleInfo(original.base, original.size + 1, original.path)])
        with self.assertRaisesRegex(np.ProcessReaderError, "invalid metadata"):
            with np.open_native_process(42, backend=backend):
                pass
        self.assertEqual(backend.closed, [0x1234])


if __name__ == "__main__":
    unittest.main()
