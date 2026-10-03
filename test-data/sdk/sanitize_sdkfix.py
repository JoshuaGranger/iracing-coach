#!/usr/bin/env python3
"""Sanitize and minimize a raw `coach-collector record-sdk` capture into a committable fixture.

    python sanitize_sdkfix.py RAW.sdkfix OUT.sdkfix

A raw capture holds the driver's name and customer id, the file names of the owner's setups and the
numbers of a purchased setup, and it is hundreds of megabytes. This script rebuilds it as a small
fixture that replays through the same reader code:

* The map is rewritten (header, session info, variable table, three row buffers) with only the
  scalar variables of the original table. The CarIdx and shock arrays are dropped and each row is
  repacked to the new offsets (about 1.7 KB instead of 8.6 KB).
* Session info is rewritten on every update: driver name, team name and customer id are replaced,
  the two setup file names get neutral names, and every number in the `CarSetup` section is scaled
  by a factor fixed per key (equal values stay equal and different values stay different, which is
  all the garage logic reads, but the purchased setup's numbers are gone). `UpdateCount` is kept.
* Ticks are thinned: at most one per 800 ms, but every tick within 3 ticks of a change in the
  status, the session-info counter, `IsInGarage`, `IsOnTrack`, `OnPitRoad` or `PlayerCarInPitStall`
  stays, so every transition keeps its real timing. Status records and session-info updates are all
  kept at their original times and order.
* The path of the recording iRacing writes (`TelemetryDiskFile`, under the user profile) is blanked.
* The result is checked: none of the names, ids and setup file names in BANNED may occur in it.

Chat is not part of the SDK map and never appears in a capture. Nothing is written to the raw
capture.
"""

import hashlib
import re
import struct
import sys

MAGIC = b"IRCSDKF1"
HEADER_LEN = 112
VAR_HEADER_LEN = 144
NUM_BUF = 3
MIN_GAP_MS = 800
KEEP_AROUND = 3

# Literal replacements. Left: what a raw capture contains. Right: what the fixture says instead.
TEXT_MAP = [
    ("NOAPS_MaconiSetupShop LasVegas 26S4 R.sto", "Base LV R.sto"),
    ("claude LV R CAM.sto", "Test LV R CAM.sto"),
    ("Joshua Granger", "Test Driver"),
    ("1507810", "100001"),
]
BANNED = [b"Joshua", b"Granger", b"1507810", b"Maconi", b"NOAPS", b"claude", b"joshu"]

SIZES = {0: 1, 1: 1, 2: 4, 3: 4, 4: 4, 5: 8}  # char, bool, int, bitfield, float, double
WATCH = ("IsInGarage", "IsOnTrack", "OnPitRoad", "PlayerCarInPitStall")


def records(path):
    with open(path, "rb") as f:
        if f.read(8) != MAGIC:
            raise SystemExit("not an SDK fixture")
        while True:
            h = f.read(5)
            if len(h) < 5:
                return
            n = struct.unpack("<I", h[1:])[0]
            p = f.read(n)
            if len(p) < n:
                return
            yield h[0], p


def geti(m, o):
    return struct.unpack_from("<i", m, o)[0]


def var_table(m):
    out = []
    for i in range(geti(m, 24)):
        o = geti(m, 28) + i * VAR_HEADER_LEN
        t, off, cnt = struct.unpack_from("<iii", m, o)
        name = bytes(m[o + 16 : o + 48]).split(b"\0")[0]
        unit = bytes(m[o + 112 : o + 144]).split(b"\0")[0]
        out.append(dict(ty=t, off=off, cnt=cnt, name=name, unit=unit))
    return out


def static_entry(m):
    """(sanitized session text, status, tick rate, update counter) from a raw map prefix."""
    sio, sil = geti(m, 20), geti(m, 16)
    text = bytes(m[sio : sio + sil]).split(b"\0")[0].decode("latin-1")
    return ("s", sanitize(text), geti(m, 4), geti(m, 8), geti(m, 12))


def factor(path):
    h = int(hashlib.sha256(path.encode()).hexdigest()[:8], 16)
    return 1.03 + (h % 17) / 100.0


def scale_number(tok, f):
    x = float(tok)
    if x == 0:
        return tok
    dec = len(tok.split(".")[1]) if "." in tok else 0
    y = round(x * f, dec)
    if y == x:
        y = x + (1 if x > 0 else -1) * 10 ** (-dec)
    return f"{y:.{dec}f}"


def sanitize(text):
    for a, b in TEXT_MAP:
        text = text.replace(a, b)
    # The recording's own path (under the user profile) appears once telemetry is being written.
    text = re.sub(r'TelemetryDiskFile: ".*"', 'TelemetryDiskFile: ""', text)
    out = []
    in_setup = False
    stack = []
    for line in text.split("\n"):
        if line and not line.startswith(" "):
            in_setup = line.startswith("CarSetup:")
            stack = []
        elif in_setup and line.strip():
            ind = len(line) - len(line.lstrip(" "))
            key, sep, val = line.strip().partition(":")
            while stack and stack[-1][0] >= ind:
                stack.pop()
            if sep and val.strip() == "":
                stack.append((ind, key))
            elif sep and key != "UpdateCount":
                f = factor(".".join([s[1] for s in stack] + [key]))
                val2 = re.sub(r"-?\d+(?:\.\d+)?", lambda mm: scale_number(mm.group(0), f), val)
                line = " " * ind + key + ":" + val2
        out.append(line)
    return "\n".join(out)


def main(raw, dst):
    it = records(raw)
    kind, p0 = next(it)
    assert kind == 1
    m = bytearray(p0[4:])
    vt = var_table(m)
    scalars = sorted((v for v in vt if v["cnt"] == 1), key=lambda v: v["off"])
    row_len = 0
    for v in scalars:
        w = SIZES[v["ty"]]
        v["new"] = -(-row_len // w) * w
        row_len = v["new"] + w
    row_len = -(-row_len // 16) * 16
    idx = {v["name"].decode(): v for v in scalars}
    watch = [idx[n] for n in WATCH]

    # Pass 1: everything in stream order, with a change key per tick.
    stream = [static_entry(m)]
    for kind, p in it:
        if kind == 1:
            b = p[4:]
            m[: len(b)] = b
            stream.append(static_entry(m))
        elif kind == 3:
            stream.append(("st", p))
        elif kind == 2:
            t, st, tr, up, tk = struct.unpack_from("<Iiiii", p)
            row = p[20:]
            key = (st, up) + tuple(row[v["off"]] for v in watch)
            stream.append(("t", (t, st, tr, up, tk), row, key))

    tick_ids = [i for i, s in enumerate(stream) if s[0] == "t"]
    keys = [stream[i][3] for i in tick_ids]
    near = [False] * len(tick_ids)
    for j in range(len(tick_ids)):
        if j == 0 or keys[j] != keys[j - 1]:
            for k in range(max(0, j - KEEP_AROUND), min(len(tick_ids), j + KEEP_AROUND + 1)):
                near[k] = True
    keep = set()
    last_t = None
    for j, i in enumerate(tick_ids):
        t = stream[i][1][0]
        if near[j] or last_t is None or t - last_t >= MIN_GAP_MS:
            keep.add(i)
            last_t = t

    maxlen = max(len(s[1]) for s in stream if s[0] == "s")
    cap = -(-(maxlen + 1) // 16) * 16 + 64
    var_off = HEADER_LEN + cap
    buf0 = -(-(var_off + len(scalars) * VAR_HEADER_LEN) // 16) * 16
    map_len = buf0 + NUM_BUF * row_len

    def static_bytes(text, status, rate, update, full):
        end = var_off + len(scalars) * VAR_HEADER_LEN if full else HEADER_LEN + cap
        b = bytearray(end)

        def put(at, v):
            b[at : at + 4] = struct.pack("<i", v)

        raw_text = text.encode("latin-1")
        put(0, 2)
        put(4, status)
        put(8, rate)
        put(12, update)
        put(16, len(raw_text) + 1)
        put(20, HEADER_LEN)
        put(24, len(scalars))
        put(28, var_off)
        put(32, NUM_BUF)
        put(36, row_len)
        for k in range(NUM_BUF):
            put(48 + 16 * k + 4, buf0 + k * row_len)
        b[HEADER_LEN : HEADER_LEN + len(raw_text)] = raw_text
        if full:
            for i, v in enumerate(scalars):
                at = var_off + i * VAR_HEADER_LEN
                put(at, v["ty"])
                put(at + 4, v["new"])
                put(at + 8, 1)
                b[at + 16 : at + 16 + len(v["name"])] = v["name"]
                b[at + 112 : at + 112 + len(v["unit"])] = v["unit"]
        return bytes(b)

    out = bytearray(MAGIC)

    def rec(kind, payload):
        out.extend(bytes([kind]) + struct.pack("<I", len(payload)) + payload)

    first = True
    n_static = 0
    for i, s in enumerate(stream):
        if s[0] == "s":
            _, text, status, rate, update = s
            rec(1, struct.pack("<I", map_len) + static_bytes(text, status, rate, update, first))
            first = False
            n_static += 1
        elif s[0] == "st":
            rec(3, s[1])
        elif i in keep:
            (t, st, tr, up, tk), row = s[1], s[2]
            new = bytearray(row_len)
            for v in scalars:
                w = SIZES[v["ty"]]
                new[v["new"] : v["new"] + w] = row[v["off"] : v["off"] + w]
            rec(2, struct.pack("<Iiiii", t, st, tr, up, tk) + bytes(new))

    blob = bytes(out)
    for word in BANNED:
        if word in blob:
            raise SystemExit(f"sanitizer left {word!r} in the output")
    with open(dst, "wb") as f:
        f.write(blob)
    print(
        f"{raw}: {len(tick_ids)} ticks -> {len(keep)}; row {row_len} B; session info up to "
        f"{maxlen} chars in {n_static} versions; {len(scalars)} of {len(vt)} variables; "
        f"output {len(blob)} bytes, sha256 {hashlib.sha256(blob).hexdigest()}"
    )


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2])
