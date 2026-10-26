# Native route and position acquisition

For what race/pit/min/max/center traces mean, native consumer evidence and the
IRP comparison, see [NATIVE-ROUTE-SEMANTICS.md](NATIVE-ROUTE-SEMANTICS.md).
Acquisition qualification and geometric/physical interpretation are separate.

Coach owns this reader; iDriver consumes an immutable installed Coach package.
No worker, service, iDriver checkout, reference lap, model call, injection or
simulator input is needed to decode a loaded track. Offline RL/RLC parsing
remains available without a simulator.

## Repeatable extraction

Install a wheel built from the selected clean Coach commit. Load the intended
track in a solo offline Test Drive, keep the car OUT, and obtain that simulator
process's PID. Importing the package does not open a process. The explicit
context manager checks the executable identity before any memory read:

```python
import json
from pathlib import Path
from coach_parsers.native_process import open_native_process
from coach_parsers.native_snapshot import decode_snapshot, decode_player_pose

def acquire(pid: int, new_output: Path):
    with open_native_process(pid) as reader:
        routes = decode_snapshot(reader.read, reader.image_base,
                                 reader.executable_sha256)
        pose = decode_player_pose(reader.read, reader.image_base,
                                  reader.executable_sha256)
        result = {"routes": routes, "player": pose,
                  "read_timings": reader.read_timings}
    with new_output.open("x", encoding="utf-8") as output:
        json.dump(result, output, indent=2)
```

Use a private destination outside Git. The reader never changes installed
track assets, recordings, setups, controls or graphics. An inactive player may
produce `None` while route acquisition succeeds. An unsupported executable or
access denial is an explicit error; there is no privilege escalation fallback.

The caller supplies current SDK track/layout/version and session identity.
Persist those with the executable and route hashes. Do not reuse an old track's
frame or assert that a snapshot belongs to a different session. Two matching
route reads are useful stationary evidence; they do not prove atomicity.

## What is recovered

The current build layout is pinned in `native_snapshot.py` to executable SHA-256
`6581dd68f7fa4367ae874fd41b459597029058ddcba9829c90d8b710170e6362`.
The world pointer is at image RVA `0x1258d6e0`. Its processed route-pointer
array is at `world+0x703e8`, with signed slot count at `world+0x703d0`.
Statically identified slots are race `0`, pit0 `43`, and pitb0 `50`. Missing
optional routes remain absent. Section records retain their native station,
checkpoint station, XY, inverse span and interpolation coefficients.

The native loader aligns ordinary RL geometry to the finite `checkpoint_00`
segment before resampling. This checkpoint differs from
`cp_startpathbegin`, the start-path marker at world+0xbcd8. That marker is
not the separate `cp_start` key. The decoder preserves checkpoint_00,
cp_startpathbegin and the SDK layout branch mode.
Alternate layout mode is reported without assuming the ordinary lap divisor.
These data replace a track-specific human fit only after runtime verification.

Native XY is transformed into east/north using world yaw theta:

```text
east  = sin(theta) * X - cos(theta) * Y
north = cos(theta) * X + sin(theta) * Y
```

The native GPS converter uses geographic origin `world+0x50/+0x58`, distinct
from the WeekendInfo metadata origin. Mixing those origins creates a false
translation. Body compass yaw is `wrap(theta - atan2(matrix[3], matrix[0]))`;
the decoder uses the canonical range [-pi, pi). Compare yaw circularly.

Player selection uses the native player index and manager branch; inactive or
invalid players are not synthesized. Native XYZ, rotation and station are
returned separately from routes. The native tick is the SDK **SessionTick**
source; SDK shared-memory buffer revision is a different counter.

## Qualification boundaries

This release establishes a statically verified decoder and portable synthetic
regression tests. Runtime acquisition, moving-pose coherence and sim-PC impact
remain unqualified. No seqlock or atomic publication has been established.
Before control uses a moving sample, an adapter must bind process/session,
player, route frame and timing, validate repeated reads, rotation/yaw, station
and kinematic consistency, and reject missing or inconsistent evidence.

Routes and planning envelopes do not themselves establish collision walls,
ground mesh, legal limits, pit-speed boundaries or an optimal racing line.
Successful parsing grants no driving admission. A build change requires new
layout evidence; a new track requires fresh identity/frame/phase evidence,
not another reverse-engineering session or a human reference lap.

A first frozen OUT-only acquisition on DESKTOP-SIM refused module enumeration
with Win32 access error 5 before any track-memory read. This machine has not
qualified the process reader. No protected-process workaround is implemented.
Version 1.1.1 corrects the start-path marker label; 1.1.0 called it cp_start.
