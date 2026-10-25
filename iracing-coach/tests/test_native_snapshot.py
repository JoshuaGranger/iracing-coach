"""Synthetic memory only; no process access or native game data."""
import math
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'parsers'))
from coach_parsers import native_snapshot as ns

BASE = 0x10000000
WORLD = 0x20000000
ARRAY = 0x21000000
RACE = 0x22000000
PIT = 0x23000000
SECTIONS = 0x24000000


class Memory:
    def __init__(self):
        self.data = {}
        self.calls = []

    def put(self, address, fmt, *values):
        raw = struct.pack(fmt, *values)
        for i, byte in enumerate(raw):
            self.data[address + i] = byte

    def read(self, address, size):
        self.calls.append((address, size))
        try:
            return bytes(self.data[address + i] for i in range(size))
        except KeyError:
            return b""


def make_memory(route_count=51, pit_pointer=PIT, sections=1, branch_mode=0):
    m = Memory()
    m.put(BASE + 0x1258D6E0, "<Q", WORLD)
    m.put(WORLD + 0x44, "<f", 0.0)
    m.put(WORLD + 0x48, "<f", 120.0)
    m.put(WORLD + 0x50, "<d", 40.0)
    m.put(WORLD + 0x58, "<d", -80.0)
    m.put(WORLD + 0x68, "<d", 1.0)  # Deliberately different metadata fields.
    m.put(WORLD + 0x70, "<d", 2.0)
    m.put(WORLD + 0x38, "<f", 1609.344)
    m.put(WORLD + 0xB4C4, "<4f", 101.0, 102.0, 103.0, 104.0)
    m.put(WORLD + 0xBCD8, "<4f", 201.0, 202.0, 203.0, 204.0)
    m.put(WORLD + 0x3F4, "<i", branch_mode)
    m.put(WORLD + 0x703D0, "<i", route_count)
    m.put(WORLD + 0x703E8, "<Q", ARRAY)
    m.put(ARRAY + 0 * 8, "<Q", RACE)
    m.put(ARRAY + 43 * 8, "<Q", pit_pointer)
    m.put(ARRAY + 50 * 8, "<Q", 0)
    for rp in (RACE, PIT):
        if rp == PIT and not pit_pointer:
            continue
        m.put(rp + 0x10, "<f", 1000.0)
        m.put(rp + 0x14, "<f", math.nan)  # Must not be consumed as part of a double.
        m.put(rp + 0x18, "<i", sections)
        m.put(rp + 0x1C, "<i", 1)
        m.put(rp + 0x38, "<Q", SECTIONS)
    p = SECTIONS
    m.put(p + 0x50, "<d", 12.5)
    m.put(p + 0x58, "<2f", 3.0, 4.0)
    m.put(p + 0x7C, "<f", 7.0)
    m.put(p + 0x80, "<f", math.nan)  # Padding/sentinel; inverse span is at +0x84.
    m.put(p + 0x84, "<f", 0.5)
    m.put(p + 0x88, "<2f", 1.5, -2.0)
    m.put(p + 0x98, "<2f", 0.25, 0.5)
    m.put(p + 0xA8, "<2f", -0.1, 0.2)
    return m


def make_pose_memory(mode):
    m = make_memory()
    m.put(BASE + 0x11916C48, "<i", 88)
    m.put(BASE + 0x1258CD34, "<i", 2)
    m.put(BASE + 0x1258CD28, "<i", mode)
    manager = 0x25000000 + mode * 0x1000
    entity_base = 0x26000000
    m.put(BASE + (0x1258D6F0 if mode == 1 else 0x1258D6E8), "<Q", manager)
    m.put(manager + 8, "<Q", entity_base)
    entity = entity_base + 2 * 0xE68
    m.put(entity + 0x490, "<Q", 0x27000000)
    m.put(entity + 0xC0, "<B", 1)
    m.put(entity + 0xC4, "<3f", 10.0, 20.0, 3.0)
    m.put(entity + 0x15C, "<9f", 1, 0, 0, 0, 1, 0, 0, 0, 1)
    m.put(entity + 0x19C, "<f", 5.0)
    return m


class NativeSnapshotTests(unittest.TestCase):
    def test_hash_rejected_before_any_read(self):
        m = Memory()
        with self.assertRaisesRegex(ns.SnapshotError, "SHA-256"):
            ns.decode_snapshot(m.read, BASE, "0" * 64)
        self.assertEqual(m.calls, [])

    def test_routes_coefficients_metadata_and_optional_absence(self):
        m = make_memory(pit_pointer=0)
        result = ns.decode_snapshot(m.read, BASE, ns.PINNED_EXECUTABLE_SHA256)
        self.assertEqual(set(result["routes"]), {"race"})
        section = result["routes"]["race"]["sections"][0]
        self.assertEqual(section["raw_rl_station"], 12.5)
        self.assertEqual(section["xy"], (3.0, 4.0))
        self.assertEqual(section["checkpoint_station"], 7.0)
        self.assertEqual(section["inverse_span"], 0.5)
        self.assertEqual(section["n0"], (0.25, 0.5))
        self.assertEqual(result["world"]["origin_lat_deg"], 40.0)
        phase = result["sdk_phase_inputs"]
        self.assertAlmostEqual(phase["ordinary_sdk_length"], 1609.344, places=3)
        self.assertEqual(phase["checkpoint_00_segment"], (101.0, 102.0, 103.0, 104.0))
        self.assertEqual(phase["cp_start_segment"], (201.0, 202.0, 203.0, 204.0))
        self.assertNotEqual(phase["checkpoint_00_segment"], phase["cp_start_segment"])
        self.assertEqual(phase["layout_branch_mode"], 0)
        self.assertFalse(phase["sdk_phase_admitted"])
        self.assertFalse(result["driving_admitted"])
        self.assertEqual(result["metadata_coherence"], "UNQUALIFIED")

    def test_alternate_layout_branch_is_reported_without_guessing(self):
        result = ns.decode_snapshot(make_memory(branch_mode=1).read, BASE,
                                    ns.PINNED_EXECUTABLE_SHA256)
        phase = result["sdk_phase_inputs"]
        self.assertEqual(phase["layout_branch_mode"], 1)
        self.assertTrue(phase["alternate_layout_detected"])
        self.assertFalse(phase["sdk_phase_admitted"])

    def test_low_level_read_rejects_non_user_ranges(self):
        m = Memory()
        for address, size in ((0xFFFF, 1), (0x800000000000, 1),
                              (0x7FFFFFFFFFFF, 2)):
            with self.subTest(address=address), self.assertRaisesRegex(
                    ns.SnapshotError, "user-mode pointer"):
                ns._read(m.read, address, size)
        self.assertEqual(m.calls, [])

    def test_route_slot_count_is_bounded(self):
        m = make_memory(route_count=2)
        result = ns.decode_snapshot(m.read, BASE, ns.PINNED_EXECUTABLE_SHA256)
        self.assertEqual(set(result["routes"]), {"race"})
        m = make_memory(route_count=2**31 - 1)
        with self.assertRaisesRegex(ns.SnapshotError, "slot count"):
            ns.decode_snapshot(m.read, BASE, ns.PINNED_EXECUTABLE_SHA256)

    def test_bad_section_count_and_nonfinite_are_refused(self):
        m = make_memory(sections=ns.HARD_MAX_SECTIONS + 1)
        with self.assertRaisesRegex(ns.SnapshotError, "section count"):
            ns.decode_snapshot(m.read, BASE, ns.PINNED_EXECUTABLE_SHA256)
        m = make_memory()
        m.put(SECTIONS + 0x58, "<2f", math.inf, 0.0)
        with self.assertRaisesRegex(ns.SnapshotError, "non-finite"):
            ns.decode_snapshot(m.read, BASE, ns.PINNED_EXECUTABLE_SHA256)

    def test_empty_sections_and_low_user_pointer_are_refused(self):
        m = make_memory(sections=0)
        with self.assertRaisesRegex(ns.SnapshotError, "section count"):
            ns.decode_snapshot(m.read, BASE, ns.PINNED_EXECUTABLE_SHA256)
        m = make_memory()
        m.put(WORLD + 0x703E8, "<Q", 0x1000)
        with self.assertRaisesRegex(ns.SnapshotError, "invalid pointer"):
            ns.decode_snapshot(m.read, BASE, ns.PINNED_EXECUTABLE_SHA256)

    def test_short_read_is_clear_error(self):
        with self.assertRaisesRegex(ns.SnapshotError, "short read"):
            ns.decode_snapshot(lambda _a, _n: b"", BASE, ns.PINNED_EXECUTABLE_SHA256)

    def test_world_to_en_known_axes(self):
        self.assertEqual(ns.world_to_en(0, 0, 0), (0.0, 0.0))
        e, n = ns.world_to_en(0, 1, 0)
        self.assertEqual((e, n), (-1.0, 0.0))
        e, n = ns.world_to_en(1, 0, math.pi / 2)
        self.assertAlmostEqual(e, 1.0)
        self.assertAlmostEqual(n, 0.0, places=15)

    def test_pose_selector_both_manager_branches(self):
        for mode in (0, 1):
            with self.subTest(mode=mode):
                m = make_pose_memory(mode)
                result = ns.decode_player_pose(m.read, BASE, ns.PINNED_EXECUTABLE_SHA256)
                self.assertEqual(result["player_index"], 2)
                self.assertEqual(result["native_session_tick_before"], 88)
                self.assertEqual(result["native_session_tick_after"], 88)
                self.assertEqual((result["east_m"], result["north_m"]), (-20.0, 10.0))
                self.assertEqual(result["compass_yaw_rad"], 0.0)
                self.assertFalse(result["driving_admitted"])

    def test_inactive_or_invalid_pose_does_not_affect_route_decode(self):
        m = make_memory()
        result = ns.decode_snapshot(m.read, BASE, ns.PINNED_EXECUTABLE_SHA256)
        self.assertIn("race", result["routes"])
        p = make_pose_memory(0)
        p.put(BASE + 0x1258CD34, "<i", 72)
        self.assertIsNone(ns.decode_player_pose(p.read, BASE, ns.PINNED_EXECUTABLE_SHA256))
        p = make_pose_memory(0)
        p.put(0x26000000 + 2 * 0xE68 + 0xC0, "<B", 0)
        self.assertIsNone(ns.decode_player_pose(p.read, BASE, ns.PINNED_EXECUTABLE_SHA256))

    def test_pose_hash_rejected_before_read(self):
        m = Memory()
        with self.assertRaisesRegex(ns.SnapshotError, "SHA-256"):
            ns.decode_player_pose(m.read, BASE, "0" * 64)
        self.assertEqual(m.calls, [])


if __name__ == "__main__":
    unittest.main()
