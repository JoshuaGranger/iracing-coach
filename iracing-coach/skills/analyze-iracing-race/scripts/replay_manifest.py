"""Replay manifest discrimination, and the window/cursor contract above it.

`REPLAY-MANIFEST-COMPAT-001`, `REPLAY-CONTRACT-001`, `PERF-REPLAY-PAYLOAD-001`.

A one-hour projection at 64 cars is 512,768 rows and 23.4 MiB delivered inline,
and a sanitized probe showed the parse and retention cost is real. What the
exact packaged host can afford is *not* known, so this module deliberately
splits the two halves of the finding, because they have different standing.

* **Discrimination is not contingent.** A manifest must say which schema, which
  format and which status it is, and be refused when it does not. A future
  schema version is refused rather than read optimistically: reading a newer
  manifest with older rules is how a format change becomes silent data loss.
  Old inline manifests keep working, which is the compatibility half.
* **Paging is contingent.** Whether bounded windows are used at all, and how big
  they are, is a measurement question that belongs to the host. This module
  supplies the semantics - what a cursor is bound to, when it stops being valid,
  how a gap is stated - and a planner that respects whatever row budget the
  measurement chooses. It does not pick the budget.

The cursor rule is the one that prevents the subtle failure. A cursor is bound
to a manifest **revision**, not just to an offset, so a replay that was
re-encoded underneath a paging reader invalidates the reader's position instead
of serving frames from a different recording at the same index.

Gaps are stated rather than skipped. A window that is missing frames says which
ones, because a consumer that receives a short window and no gap list cannot
tell a dropped range from the end of the data - and `REPLAY-WRITEFAIL-001`
guarantees short windows exist.

No file access and no clock here: this is the contract the reader and the writer
both have to obey.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator, Sequence

#: Version of the replay manifest contract.
REPLAY_MANIFEST_VERSION = 1

#: The replay payload schema this contract knows how to read. Kept in step with
#: live_replay_v2.SCHEMA_VERSION deliberately: the manifest describes those
#: frames, so it cannot outlive their format silently.
SUPPORTED_SCHEMA_VERSION = 2

FORMAT_INLINE = "inline"
FORMAT_WINDOWED = "windowed"

#: How the frames are delivered. `inline` is the existing whole-payload
#: delivery and is never removed; `windowed` is the bounded alternative.
MANIFEST_FORMATS = (FORMAT_INLINE, FORMAT_WINDOWED)

STATUS_COMPLETE = "complete"
STATUS_INCOMPLETE = "incomplete"
STATUS_FAILED = "failed"

#: Whether the recording finished. `incomplete` is a first-class state because
#: a capture that hit a persistence failure must not be advertised as whole.
MANIFEST_STATUSES = (STATUS_COMPLETE, STATUS_INCOMPLETE, STATUS_FAILED)

__all__ = [
    "FrameGap",
    "MANIFEST_FORMATS",
    "MANIFEST_STATUSES",
    "REPLAY_MANIFEST_VERSION",
    "ReplayManifest",
    "ReplayManifestError",
    "SUPPORTED_SCHEMA_VERSION",
    "WindowCursor",
    "WindowPlan",
    "cursor_is_valid",
    "plan_windows",
    "read_manifest",
]


class ReplayManifestError(ValueError):
    """A manifest, cursor or window violated the replay contract."""


def _require_int(value: Any, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReplayManifestError(f"{name} must be a JSON integer")
    if value < minimum:
        raise ReplayManifestError(f"{name} must be at least {minimum}")
    return value


@dataclass(frozen=True)
class FrameGap:
    """A range of frames the recording does not contain."""

    start_frame: int
    end_frame: int

    def __post_init__(self) -> None:
        _require_int(self.start_frame, "gap start_frame")
        _require_int(self.end_frame, "gap end_frame")
        if self.end_frame < self.start_frame:
            raise ReplayManifestError("a gap cannot end before it starts")

    @property
    def frame_count(self) -> int:
        return self.end_frame - self.start_frame + 1

    def to_payload(self) -> dict[str, Any]:
        return {"start_frame": self.start_frame, "end_frame": self.end_frame}


@dataclass(frozen=True)
class ReplayManifest:
    """What a replay is, strictly enough that a reader cannot guess wrong."""

    schema_version: int
    format: str
    status: str
    revision: str
    frame_count: int
    car_count: int
    cadence_hz: float
    gaps: tuple[FrameGap, ...] = ()

    def __post_init__(self) -> None:
        _require_int(self.schema_version, "schema_version", minimum=1)
        if self.schema_version > SUPPORTED_SCHEMA_VERSION:
            # Refused, not degraded. A newer writer may mean something different
            # by the same field, and reading it with today's rules is exactly
            # how a format change turns into silently wrong frames.
            raise ReplayManifestError(
                f"replay schema {self.schema_version} is newer than the supported "
                f"{SUPPORTED_SCHEMA_VERSION} and cannot be read"
            )
        if self.format not in MANIFEST_FORMATS:
            raise ReplayManifestError(f"unknown replay format: {self.format!r}")
        if self.status not in MANIFEST_STATUSES:
            raise ReplayManifestError(f"unknown replay status: {self.status!r}")
        if not self.revision:
            raise ReplayManifestError("a manifest must carry a revision")
        _require_int(self.frame_count, "frame_count")
        _require_int(self.car_count, "car_count")
        if isinstance(self.cadence_hz, bool) or not isinstance(self.cadence_hz, (int, float)):
            raise ReplayManifestError("cadence_hz must be a number")
        if not 0 < float(self.cadence_hz) <= 1000:
            raise ReplayManifestError("cadence_hz must be positive and plausible")
        if self.status == STATUS_COMPLETE and self.gaps:
            # The false-completeness case named in REPLAY-WRITEFAIL-001.
            raise ReplayManifestError(
                "a complete recording cannot declare missing frames"
            )
        if self.status == STATUS_FAILED and self.frame_count:
            raise ReplayManifestError(
                "a failed recording cannot claim to have delivered frames"
            )
        previous_end = -1
        for gap in sorted(self.gaps, key=lambda item: item.start_frame):
            if gap.start_frame <= previous_end:
                raise ReplayManifestError("gaps must not overlap")
            if gap.end_frame >= self.frame_count:
                raise ReplayManifestError("a gap must fall inside the frame range")
            previous_end = gap.end_frame

    @property
    def total_rows(self) -> int:
        """Frames times cars: the number the payload budget is actually about."""
        return self.frame_count * self.car_count

    @property
    def missing_frames(self) -> int:
        return sum(gap.frame_count for gap in self.gaps)

    @property
    def is_readable(self) -> bool:
        return self.status in (STATUS_COMPLETE, STATUS_INCOMPLETE) and self.frame_count > 0

    def to_payload(self) -> dict[str, Any]:
        return {
            "contract_version": REPLAY_MANIFEST_VERSION,
            "schema_version": self.schema_version,
            "format": self.format,
            "status": self.status,
            "revision": self.revision,
            "frame_count": self.frame_count,
            "car_count": self.car_count,
            "cadence_hz": float(self.cadence_hz),
            "total_rows": self.total_rows,
            "missing_frames": self.missing_frames,
            "gaps": [gap.to_payload() for gap in self.gaps],
        }


def read_manifest(payload: Any) -> ReplayManifest:
    """Discriminate a manifest payload, including the old inline shape.

    An older payload that predates the named fields is read as a complete inline
    recording, which is what it was. Everything else must say what it is.
    """
    if not isinstance(payload, dict):
        raise ReplayManifestError("a manifest must be an object")

    schema_version = payload.get("schema_version", payload.get("schemaVersion"))
    if schema_version is None:
        raise ReplayManifestError("a manifest must state its schema version")

    if "format" not in payload and "status" not in payload:
        # The pre-contract inline manifest. Accepted, and pinned to the meaning
        # it actually had rather than to today's defaults.
        return ReplayManifest(
            schema_version=_require_int(schema_version, "schema_version", minimum=1),
            format=FORMAT_INLINE,
            status=STATUS_COMPLETE,
            revision=str(payload.get("revision") or "legacy-inline"),
            frame_count=_require_int(payload.get("frame_count", 0), "frame_count"),
            car_count=_require_int(payload.get("car_count", 0), "car_count"),
            cadence_hz=payload.get("cadence_hz", 60),
        )

    missing = [name for name in ("format", "status") if name not in payload]
    if missing:
        # Half-present is not legacy. A payload that carries one discriminator
        # and not the other is malformed, and guessing the other one is how a
        # windowed recording gets read as an inline one.
        raise ReplayManifestError(
            "a manifest carrying " + ", ".join(sorted(set(payload) & {"format", "status"}))
            + " must also carry " + ", ".join(missing)
        )

    return ReplayManifest(
        schema_version=_require_int(schema_version, "schema_version", minimum=1),
        format=payload["format"],
        status=payload["status"],
        revision=str(payload.get("revision") or ""),
        frame_count=_require_int(payload.get("frame_count", 0), "frame_count"),
        car_count=_require_int(payload.get("car_count", 0), "car_count"),
        cadence_hz=payload.get("cadence_hz", 60),
        gaps=tuple(
            FrameGap(
                start_frame=_require_int(item.get("start_frame"), "gap start_frame"),
                end_frame=_require_int(item.get("end_frame"), "gap end_frame"),
            )
            for item in payload.get("gaps", ())
        ),
    )


@dataclass(frozen=True)
class WindowCursor:
    """A position in a replay, bound to the revision it was taken against."""

    revision: str
    next_frame: int

    def __post_init__(self) -> None:
        if not self.revision:
            raise ReplayManifestError("a cursor must name the revision it belongs to")
        _require_int(self.next_frame, "next_frame")


def cursor_is_valid(manifest: ReplayManifest, cursor: WindowCursor) -> bool:
    """Whether a cursor still refers to the recording it was taken from.

    Revision first: the same offset in a re-encoded replay is a different frame,
    so an offset check alone would happily serve the wrong data.
    """
    if not isinstance(manifest, ReplayManifest) or not isinstance(cursor, WindowCursor):
        raise ReplayManifestError("cursor validity needs a manifest and a cursor")
    return cursor.revision == manifest.revision and cursor.next_frame <= manifest.frame_count


@dataclass(frozen=True)
class WindowPlan:
    """One bounded read: which frames, and what the reader should expect."""

    revision: str
    start_frame: int
    end_frame: int
    rows: int
    contains_gap: bool = False

    @property
    def frame_count(self) -> int:
        return self.end_frame - self.start_frame + 1

    @property
    def cursor(self) -> WindowCursor:
        return WindowCursor(revision=self.revision, next_frame=self.end_frame + 1)

    def to_payload(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
            "frame_count": self.frame_count,
            "rows": self.rows,
            "contains_gap": self.contains_gap,
        }


def plan_windows(
    manifest: ReplayManifest, *, row_budget: int, cursor: WindowCursor | None = None
) -> Iterator[WindowPlan]:
    """Walk a replay in windows that each respect the row budget.

    The budget is in rows rather than frames because rows are what the payload
    cost is measured in: at 64 cars a frame is 64 times as expensive as at one,
    and a frame-based bound would be 64 times wrong at the top of the range.

    The budget itself is the host measurement's to choose. This only guarantees
    that whatever budget arrives is respected and that at least one frame is
    always delivered, so a budget smaller than a single frame cannot stall the
    reader silently - it raises instead.
    """
    if not isinstance(manifest, ReplayManifest):
        raise ReplayManifestError("plan_windows needs a ReplayManifest")
    _require_int(row_budget, "row_budget", minimum=1)
    if not manifest.is_readable:
        return
    if manifest.car_count < 1:
        raise ReplayManifestError("a readable replay must contain at least one car")
    if row_budget < manifest.car_count:
        raise ReplayManifestError(
            f"a row budget of {row_budget} cannot hold one frame of "
            f"{manifest.car_count} cars"
        )

    if cursor is not None:
        if not cursor_is_valid(manifest, cursor):
            raise ReplayManifestError(
                "the cursor does not belong to this manifest revision"
            )
        start = cursor.next_frame
    else:
        start = 0

    frames_per_window = row_budget // manifest.car_count
    gap_frames = {
        frame
        for gap in manifest.gaps
        for frame in range(gap.start_frame, gap.end_frame + 1)
    }

    while start < manifest.frame_count:
        end = min(start + frames_per_window - 1, manifest.frame_count - 1)
        window_frames = range(start, end + 1)
        yield WindowPlan(
            revision=manifest.revision,
            start_frame=start,
            end_frame=end,
            rows=(end - start + 1) * manifest.car_count,
            contains_gap=any(frame in gap_frames for frame in window_frames),
        )
        start = end + 1
