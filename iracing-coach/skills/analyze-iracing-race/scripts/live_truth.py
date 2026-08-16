"""The racing-truth policy the live path must obey, frozen as data.

`DOMAIN-DRIFT-001` with `LIVE-FLAG-001`, `LIVE-CLEAN-001`, `LIVE-REPAIR-001`
and `LIVE-MISSING-001`. The live telemetry path stays frame-local and in .NET;
this module does not run in it. What this module does is decide, once and in
one language, what the answers are - which flag bits mean caution, what makes a
lap clean, what a missing channel means - and emit conformance vectors that the
.NET decoder must reproduce exactly.

That indirection is the point. The defects being closed here are all *parity*
defects, where two implementations of the same question disagreed:

* **`LIVE-FLAG-001`.** The offline analyzer treats `0x0100` and `0x0200` as
  caution bits. The live `FlagLabel` decoder checks only `0x0008`, `0x4000` and
  `0x8000`, so a session under those two bits is labelled `RACING` live and
  `caution` in the analysis of the same race. A driver is told the track is
  green while it is yellow.
* **`LIVE-CLEAN-001`.** The offline clean-lap rule excludes pit, caution,
  restart, non-racing-state, off-track and close-traffic laps. The live path
  applies a subset, so a lap the analysis will later exclude can be presented
  live as a clean reference.
* **`LIVE-MISSING-001`.** `LapDistancePercent ?? 0` turns an absent channel
  into a position at the start/finish line. A car whose position is unknown is
  drawn on the line, confidently, in the wrong place.
* **`LIVE-REPAIR-001`.** The repair-required bit says repairs are *required*.
  It does not say how long they will take. Repair-only time is not derivable
  from it and must not be stated.

Every rule below is declared as data rather than as code a second
implementation would have to re-read, and :func:`conformance_vectors` renders
the whole policy as cases with expected outputs. A .NET decoder that passes
every vector agrees with the backend by construction; one that reimplements the
prose does not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

#: Version of the racing-truth policy. The conformance vectors carry it, so a
#: consumer pinned to an older policy fails loudly rather than passing a stale
#: vector set.
LIVE_TRUTH_POLICY_VERSION = 1

# --------------------------------------------------------------------------
# Session flag bits
# --------------------------------------------------------------------------

#: Every session-flag bit this policy decides on, with the name the backend
#: uses. Declared as a table so the generated constants, the conformance
#: vectors and the caution mask all come from one place.
SESSION_FLAG_BITS: tuple[tuple[str, int], ...] = (
    ("checkered", 0x00000001),
    ("white", 0x00000002),
    ("green", 0x00000004),
    ("yellow", 0x00000008),
    ("red", 0x00000010),
    ("blue", 0x00000020),
    ("debris", 0x00000040),
    ("crossed", 0x00000080),
    ("yellow_waving", 0x00000100),
    ("one_lap_to_green", 0x00000200),
    ("green_held", 0x00000400),
    ("ten_to_go", 0x00000800),
    ("five_to_go", 0x00001000),
    ("random_waving", 0x00002000),
    ("caution", 0x00004000),
    ("caution_waving", 0x00008000),
    ("black", 0x00010000),
    ("disqualify", 0x00020000),
    ("servicible", 0x00040000),
    ("furled", 0x00080000),
    ("repair", 0x00100000),
    ("start_hidden", 0x10000000),
    ("start_ready", 0x20000000),
    ("start_set", 0x40000000),
    ("start_go", 0x80000000),
)

_BIT_BY_NAME = dict(SESSION_FLAG_BITS)

#: The bits that mean the track is under caution. This is the backend's
#: authority and it is what the offline analyzer already uses.
CAUTION_MASK = (
    _BIT_BY_NAME["yellow"]
    | _BIT_BY_NAME["yellow_waving"]
    | _BIT_BY_NAME["one_lap_to_green"]
    | _BIT_BY_NAME["caution"]
    | _BIT_BY_NAME["caution_waving"]
)

#: The caution bits the shipped .NET `FlagLabel` decoder tested. Recorded so a
#: conformance vector can assert on the exact difference rather than on a claim
#: that a difference exists.
DOTNET_LEGACY_CAUTION_MASK = (
    _BIT_BY_NAME["yellow"] | _BIT_BY_NAME["caution"] | _BIT_BY_NAME["caution_waving"]
)

#: The bits the live decoder omitted. Non-empty, and a test proves it.
CAUTION_BITS_MISSING_FROM_DOTNET = CAUTION_MASK & ~DOTNET_LEGACY_CAUTION_MASK

STATE_DISQUALIFIED = "disqualified"
STATE_BLACK = "black"
STATE_RED = "red"
STATE_CAUTION = "caution"
STATE_CHECKERED = "checkered"
STATE_WHITE = "white"
STATE_GREEN = "green"
STATE_RACING = "racing"
STATE_UNKNOWN = "unknown"

#: Racing states in decision order. The order is the policy: a black flag
#: outranks a caution because a penalty applies to this car regardless of what
#: the track is doing, and `unknown` is never reached by precedence - it is only
#: produced when the channel is absent.
RACING_STATE_PRECEDENCE: tuple[tuple[str, int], ...] = (
    (STATE_DISQUALIFIED, _BIT_BY_NAME["disqualify"]),
    (STATE_BLACK, _BIT_BY_NAME["black"]),
    (STATE_RED, _BIT_BY_NAME["red"]),
    (STATE_CAUTION, CAUTION_MASK),
    (STATE_CHECKERED, _BIT_BY_NAME["checkered"]),
    (STATE_WHITE, _BIT_BY_NAME["white"]),
    (STATE_GREEN, _BIT_BY_NAME["green"] | _BIT_BY_NAME["start_go"]),
)

RACING_STATES = tuple(name for name, _ in RACING_STATE_PRECEDENCE) + (
    STATE_RACING,
    STATE_UNKNOWN,
)

# --------------------------------------------------------------------------
# Clean-lap policy
# --------------------------------------------------------------------------

EXCLUSION_CAUTION_OR_MIXED = "caution_or_mixed"
EXCLUSION_PIT = "pit"
EXCLUSION_NOT_RACING_STATE = "not_racing_state"
EXCLUSION_OFF_TRACK = "off_track"
EXCLUSION_CLOSE_TRAFFIC = "close_traffic"
EXCLUSION_RESTART = "restart"
EXCLUSION_INCOMPLETE = "incomplete_lap"
EXCLUSION_REPAIR_EPISODE = "repair_correlated"

#: Every exclusion reason, in the order they are reported. A live surface shows
#: this list; it does not invent a shorter one.
CLEAN_LAP_EXCLUSIONS = (
    EXCLUSION_INCOMPLETE,
    EXCLUSION_CAUTION_OR_MIXED,
    EXCLUSION_PIT,
    EXCLUSION_NOT_RACING_STATE,
    EXCLUSION_OFF_TRACK,
    EXCLUSION_CLOSE_TRAFFIC,
    EXCLUSION_RESTART,
    EXCLUSION_REPAIR_EPISODE,
)

#: Thresholds, matched to the offline analyzer so the two agree on the same
#: lap. Changing one of these changes what "clean" means and therefore what
#: every pace comparison is measured against, so they are named and frozen.
PIT_TIME_EXCLUSION_S = 1.0
MINIMUM_RACING_STATE_FRACTION = 0.98
MINIMUM_ON_TRACK_FRACTION = 0.98
MAXIMUM_TRAFFIC_PROXIMITY_FRACTION = 0.10

#: Channels a clean-lap verdict depends on. A live path missing any of them
#: cannot decide the lap, and this policy says so rather than defaulting.
CLEAN_LAP_REQUIRED_CHANNELS = (
    "flag_state",
    "complete",
    "pit_time_s",
)

#: Channels that refine the verdict when present and make it indeterminate when
#: absent. They are separated from the required set because a live feed
#: legitimately starts without them.
CLEAN_LAP_REFINING_CHANNELS = (
    "racing_state_fraction",
    "on_track_fraction",
    "traffic_proximity_fraction",
)

VERDICT_CLEAN = "clean"
VERDICT_EXCLUDED = "excluded"
VERDICT_INDETERMINATE = "indeterminate"

CLEAN_LAP_VERDICTS = (VERDICT_CLEAN, VERDICT_EXCLUDED, VERDICT_INDETERMINATE)

# --------------------------------------------------------------------------
# Repair truth
# --------------------------------------------------------------------------

REPAIR_NOT_REQUIRED = "not_required"
REPAIR_REQUIRED = "required"
REPAIR_UNKNOWN = "unknown"

REPAIR_STATES = (REPAIR_NOT_REQUIRED, REPAIR_REQUIRED, REPAIR_UNKNOWN)

#: The repair bit is a requirement, not a duration. Stated here so a consumer
#: reading only this module still learns the rule.
REPAIR_TIME_IS_NOT_DERIVABLE = (
    "The repair-required bit states that repairs are required. It carries no "
    "duration, so repair-only time must not be displayed from it."
)

__all__ = [
    "CAUTION_BITS_MISSING_FROM_DOTNET",
    "CAUTION_MASK",
    "CLEAN_LAP_EXCLUSIONS",
    "CLEAN_LAP_REFINING_CHANNELS",
    "CLEAN_LAP_REQUIRED_CHANNELS",
    "CLEAN_LAP_VERDICTS",
    "DOTNET_LEGACY_CAUTION_MASK",
    "LIVE_TRUTH_POLICY_VERSION",
    "MAXIMUM_TRAFFIC_PROXIMITY_FRACTION",
    "MINIMUM_ON_TRACK_FRACTION",
    "MINIMUM_RACING_STATE_FRACTION",
    "PIT_TIME_EXCLUSION_S",
    "RACING_STATES",
    "RACING_STATE_PRECEDENCE",
    "REPAIR_STATES",
    "REPAIR_TIME_IS_NOT_DERIVABLE",
    "SESSION_FLAG_BITS",
    "CleanLapVerdict",
    "LiveTruthError",
    "clean_lap_verdict",
    "conformance_vectors",
    "lap_distance_percent",
    "racing_state",
    "repair_state",
]


class LiveTruthError(ValueError):
    """A policy input was of a shape this module refuses to interpret."""


def _flags(value: Any) -> int | None:
    """Read a session-flag word, or None when there is not one.

    A boolean is refused. `SessionFlags` arriving as `true` would otherwise
    become the integer 1 and be decoded as a checkered flag, which is a
    fabricated race state built out of a type error.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value & 0xFFFFFFFF
    if isinstance(value, float):
        if value != int(value):
            return None
        return int(value) & 0xFFFFFFFF
    return None


def racing_state(session_flags: Any) -> str:
    """Decide the racing state from a session-flag word.

    Returns `unknown` when the channel is absent. It deliberately does not
    return `racing`: absent flags are not evidence that the track is green, and
    the shipped live decoder's habit of falling through to a racing label is
    exactly the fail-open this policy closes.
    """
    flags = _flags(session_flags)
    if flags is None:
        return STATE_UNKNOWN
    for state, mask in RACING_STATE_PRECEDENCE:
        if flags & mask:
            return state
    return STATE_RACING


def repair_state(session_flags: Any) -> str:
    """Whether repairs are required. Never how long they take."""
    flags = _flags(session_flags)
    if flags is None:
        return REPAIR_UNKNOWN
    return REPAIR_REQUIRED if flags & _BIT_BY_NAME["repair"] else REPAIR_NOT_REQUIRED


def lap_distance_percent(value: Any) -> float | None:
    """Normalise lap position, keeping absence absent.

    The whole rule is the return type. A caller that wants to draw a car must
    handle None by not drawing it, and no substitution - `?? 0` above all -
    turns an unknown position into the start/finish line. Out-of-range and
    non-finite values are absent for the same reason: they are not positions.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        # A numeric channel arriving as a string is a transport fault, not a
        # position. Coercing "0.5" here would let a malformed frame draw a car
        # at a plausible place on the track.
        return None
    number = float(value)
    if number != number or number in (float("inf"), -float("inf")):
        return None
    if not 0.0 <= number <= 1.0:
        return None
    return number


@dataclass(frozen=True)
class CleanLapVerdict:
    """Whether a lap may be used as a clean reference, and why not if not."""

    verdict: str
    reasons: tuple[str, ...] = ()
    missing_channels: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.verdict not in CLEAN_LAP_VERDICTS:
            raise LiveTruthError(f"unknown clean-lap verdict: {self.verdict!r}")
        unknown = [reason for reason in self.reasons if reason not in CLEAN_LAP_EXCLUSIONS]
        if unknown:
            raise LiveTruthError(f"undeclared exclusion reason(s): {unknown}")
        if self.verdict == VERDICT_CLEAN and (self.reasons or self.missing_channels):
            raise LiveTruthError("a clean lap has no exclusions and no missing channels")
        if self.verdict == VERDICT_EXCLUDED and not self.reasons:
            raise LiveTruthError("an excluded lap must say why")
        if self.verdict == VERDICT_INDETERMINATE and not self.missing_channels:
            raise LiveTruthError("an indeterminate lap must name the absent channels")

    @property
    def usable_as_reference(self) -> bool:
        """Only a clean verdict may be used. Indeterminate is not permission."""
        return self.verdict == VERDICT_CLEAN

    def to_payload(self) -> dict[str, Any]:
        return {
            "missing_channels": list(self.missing_channels),
            "reasons": list(self.reasons),
            "usable_as_reference": self.usable_as_reference,
            "verdict": self.verdict,
        }


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def clean_lap_verdict(
    lap: Mapping[str, Any],
    *,
    previous_flag_state: str | None = None,
) -> CleanLapVerdict:
    """Decide one lap against the frozen clean-lap policy.

    Missing channels make the verdict indeterminate rather than clean. This is
    the deliberate divergence from the offline analyzer, which treats an absent
    refining fraction as satisfied. Offline that is defensible: the whole lap is
    in hand and an absent channel means the recording never had it. Live it is
    not, because a channel that has not arrived yet would license a clean
    reference that the same lap loses the moment the data appears.
    """
    if not isinstance(lap, Mapping):
        raise LiveTruthError("lap must be a mapping")

    missing = [name for name in CLEAN_LAP_REQUIRED_CHANNELS if lap.get(name) is None]
    missing += [
        name
        for name in CLEAN_LAP_REFINING_CHANNELS
        if name in lap and _number(lap.get(name)) is None and lap.get(name) is not None
    ]
    missing += [name for name in CLEAN_LAP_REFINING_CHANNELS if name not in lap]
    if missing:
        return CleanLapVerdict(
            VERDICT_INDETERMINATE, missing_channels=tuple(sorted(set(missing)))
        )

    reasons: list[str] = []
    if not bool(lap.get("complete")):
        reasons.append(EXCLUSION_INCOMPLETE)
    flag_state = str(lap.get("flag_state"))
    if flag_state != STATE_GREEN:
        reasons.append(EXCLUSION_CAUTION_OR_MIXED)
    if (_number(lap.get("pit_time_s")) or 0.0) >= PIT_TIME_EXCLUSION_S:
        reasons.append(EXCLUSION_PIT)
    racing_fraction = _number(lap.get("racing_state_fraction"))
    if racing_fraction is not None and racing_fraction < MINIMUM_RACING_STATE_FRACTION:
        reasons.append(EXCLUSION_NOT_RACING_STATE)
    on_track = _number(lap.get("on_track_fraction"))
    if on_track is not None and on_track < MINIMUM_ON_TRACK_FRACTION:
        reasons.append(EXCLUSION_OFF_TRACK)
    traffic = _number(lap.get("traffic_proximity_fraction"))
    if traffic is not None and traffic >= MAXIMUM_TRAFFIC_PROXIMITY_FRACTION:
        reasons.append(EXCLUSION_CLOSE_TRAFFIC)
    if previous_flag_state == STATE_CAUTION and flag_state == STATE_GREEN:
        reasons.append(EXCLUSION_RESTART)
    if lap.get("repair_correlated") is True:
        reasons.append(EXCLUSION_REPAIR_EPISODE)

    if reasons:
        ordered = tuple(
            reason for reason in CLEAN_LAP_EXCLUSIONS if reason in set(reasons)
        )
        return CleanLapVerdict(VERDICT_EXCLUDED, reasons=ordered)
    return CleanLapVerdict(VERDICT_CLEAN)


# --------------------------------------------------------------------------
# Conformance vectors
# --------------------------------------------------------------------------


def _flag_vectors() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for name, mask in SESSION_FLAG_BITS:
        cases.append(
            {
                "case": f"single-bit-{name}",
                "session_flags": mask,
                "expected_racing_state": racing_state(mask),
                "expected_repair_state": repair_state(mask),
            }
        )
    # The two bits the live decoder omitted, alone and together, are the whole
    # point of this vector set and are named so a failure reads as itself.
    for name in ("yellow_waving", "one_lap_to_green"):
        mask = _BIT_BY_NAME[name]
        cases.append(
            {
                "case": f"caution-bit-omitted-by-dotnet-{name}",
                "session_flags": mask,
                "expected_racing_state": STATE_CAUTION,
                "expected_repair_state": REPAIR_NOT_REQUIRED,
            }
        )
    combined = _BIT_BY_NAME["yellow_waving"] | _BIT_BY_NAME["one_lap_to_green"]
    cases.append(
        {
            "case": "caution-bits-omitted-by-dotnet-combined",
            "session_flags": combined,
            "expected_racing_state": STATE_CAUTION,
            "expected_repair_state": REPAIR_NOT_REQUIRED,
        }
    )
    # Precedence: every pair where a lower-precedence bit accompanies a higher
    # one must resolve to the higher one.
    for index, (state, mask) in enumerate(RACING_STATE_PRECEDENCE):
        for lower_state, lower_mask in RACING_STATE_PRECEDENCE[index + 1 :]:
            cases.append(
                {
                    "case": f"precedence-{state}-over-{lower_state}",
                    "session_flags": mask | lower_mask,
                    "expected_racing_state": state,
                    "expected_repair_state": repair_state(mask | lower_mask),
                }
            )
    cases.append(
        {
            "case": "no-flag-bits-set-is-racing",
            "session_flags": 0,
            "expected_racing_state": STATE_RACING,
            "expected_repair_state": REPAIR_NOT_REQUIRED,
        }
    )
    cases.append(
        {
            "case": "absent-channel-is-unknown-not-racing",
            "session_flags": None,
            "expected_racing_state": STATE_UNKNOWN,
            "expected_repair_state": REPAIR_UNKNOWN,
        }
    )
    cases.append(
        {
            "case": "boolean-is-not-a-flag-word",
            "session_flags": True,
            "expected_racing_state": STATE_UNKNOWN,
            "expected_repair_state": REPAIR_UNKNOWN,
        }
    )
    cases.append(
        {
            "case": "repair-required-under-green",
            "session_flags": _BIT_BY_NAME["green"] | _BIT_BY_NAME["repair"],
            "expected_racing_state": STATE_GREEN,
            "expected_repair_state": REPAIR_REQUIRED,
        }
    )
    return cases


def _missing_value_vectors() -> list[dict[str, Any]]:
    return [
        {"case": "absent", "value": None, "expected": None},
        {"case": "start-finish-line", "value": 0.0, "expected": 0.0},
        {"case": "mid-lap", "value": 0.5, "expected": 0.5},
        {"case": "lap-end", "value": 1.0, "expected": 1.0},
        {"case": "below-range", "value": -0.01, "expected": None},
        {"case": "above-range", "value": 1.01, "expected": None},
        {"case": "boolean-false-is-not-the-line", "value": False, "expected": None},
        {"case": "string", "value": "0.5", "expected": None},
    ]


_CLEAN_LAP_BASE = {
    "flag_state": STATE_GREEN,
    "complete": True,
    "pit_time_s": 0.0,
    "racing_state_fraction": 1.0,
    "on_track_fraction": 1.0,
    "traffic_proximity_fraction": 0.0,
}


def _clean_lap_vectors() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = [
        {"case": "clean", "lap": dict(_CLEAN_LAP_BASE), "previous_flag_state": None},
        {
            "case": "incomplete",
            "lap": {**_CLEAN_LAP_BASE, "complete": False},
            "previous_flag_state": None,
        },
        {
            "case": "under-caution",
            "lap": {**_CLEAN_LAP_BASE, "flag_state": STATE_CAUTION},
            "previous_flag_state": None,
        },
        {
            "case": "pit-lap",
            "lap": {**_CLEAN_LAP_BASE, "pit_time_s": PIT_TIME_EXCLUSION_S},
            "previous_flag_state": None,
        },
        {
            "case": "pit-just-below-threshold",
            "lap": {**_CLEAN_LAP_BASE, "pit_time_s": PIT_TIME_EXCLUSION_S - 0.01},
            "previous_flag_state": None,
        },
        {
            "case": "not-racing-state",
            "lap": {
                **_CLEAN_LAP_BASE,
                "racing_state_fraction": MINIMUM_RACING_STATE_FRACTION - 0.01,
            },
            "previous_flag_state": None,
        },
        {
            "case": "racing-state-at-threshold",
            "lap": {
                **_CLEAN_LAP_BASE,
                "racing_state_fraction": MINIMUM_RACING_STATE_FRACTION,
            },
            "previous_flag_state": None,
        },
        {
            "case": "off-track",
            "lap": {
                **_CLEAN_LAP_BASE,
                "on_track_fraction": MINIMUM_ON_TRACK_FRACTION - 0.01,
            },
            "previous_flag_state": None,
        },
        {
            "case": "close-traffic",
            "lap": {
                **_CLEAN_LAP_BASE,
                "traffic_proximity_fraction": MAXIMUM_TRAFFIC_PROXIMITY_FRACTION,
            },
            "previous_flag_state": None,
        },
        {
            "case": "traffic-just-below-threshold",
            "lap": {
                **_CLEAN_LAP_BASE,
                "traffic_proximity_fraction": MAXIMUM_TRAFFIC_PROXIMITY_FRACTION - 0.001,
            },
            "previous_flag_state": None,
        },
        {
            "case": "restart",
            "lap": dict(_CLEAN_LAP_BASE),
            "previous_flag_state": STATE_CAUTION,
        },
        {
            "case": "repair-correlated",
            "lap": {**_CLEAN_LAP_BASE, "repair_correlated": True},
            "previous_flag_state": None,
        },
        {
            "case": "several-reasons-at-once",
            "lap": {
                **_CLEAN_LAP_BASE,
                "complete": False,
                "flag_state": STATE_CAUTION,
                "pit_time_s": 30.0,
            },
            "previous_flag_state": None,
        },
    ]
    for channel in CLEAN_LAP_REFINING_CHANNELS:
        absent = dict(_CLEAN_LAP_BASE)
        absent.pop(channel)
        cases.append(
            {
                "case": f"missing-refining-channel-{channel}",
                "lap": absent,
                "previous_flag_state": None,
            }
        )
    for channel in CLEAN_LAP_REQUIRED_CHANNELS:
        absent = dict(_CLEAN_LAP_BASE)
        absent[channel] = None
        cases.append(
            {
                "case": f"missing-required-channel-{channel}",
                "lap": absent,
                "previous_flag_state": None,
            }
        )
    for case in cases:
        case["expected"] = clean_lap_verdict(
            case["lap"], previous_flag_state=case["previous_flag_state"]
        ).to_payload()
    return cases


def conformance_vectors() -> dict[str, Any]:
    """The whole policy as cases with expected outputs.

    A second implementation proves parity by reproducing every expected value
    here. Nothing in this structure is derived at read time, so a consumer in
    another language needs no interpreter for it.
    """
    return {
        "policy_version": LIVE_TRUTH_POLICY_VERSION,
        "caution_mask": CAUTION_MASK,
        "dotnet_legacy_caution_mask": DOTNET_LEGACY_CAUTION_MASK,
        "caution_bits_missing_from_dotnet": CAUTION_BITS_MISSING_FROM_DOTNET,
        "session_flag_bits": {name: mask for name, mask in SESSION_FLAG_BITS},
        "racing_state_precedence": [name for name, _ in RACING_STATE_PRECEDENCE],
        "clean_lap_thresholds": {
            "pit_time_exclusion_s": PIT_TIME_EXCLUSION_S,
            "minimum_racing_state_fraction": MINIMUM_RACING_STATE_FRACTION,
            "minimum_on_track_fraction": MINIMUM_ON_TRACK_FRACTION,
            "maximum_traffic_proximity_fraction": MAXIMUM_TRAFFIC_PROXIMITY_FRACTION,
        },
        "repair_time_is_not_derivable": REPAIR_TIME_IS_NOT_DERIVABLE,
        "flag_vectors": _flag_vectors(),
        "lap_distance_percent_vectors": _missing_value_vectors(),
        "clean_lap_vectors": _clean_lap_vectors(),
    }


def check_conformance(
    decoded: Sequence[Mapping[str, Any]], *, kind: str = "flag_vectors"
) -> list[str]:
    """Compare a second implementation's results with the frozen expectations.

    Returns the disagreements. An empty list means parity for the supplied
    cases - not for the whole policy, because a caller that submits three cases
    proves three cases. The count is checked here so a partial submission
    cannot read as a pass.
    """
    expected = {case["case"]: case for case in conformance_vectors()[kind]}
    problems: list[str] = []
    seen = set()
    for row in decoded:
        name = row.get("case")
        seen.add(name)
        reference = expected.get(name)
        if reference is None:
            problems.append(f"{name}: not a declared case")
            continue
        for key, value in reference.items():
            if key in ("case", "lap", "session_flags", "value", "previous_flag_state"):
                continue
            if row.get(key) != value:
                problems.append(f"{name}: {key} expected {value!r}, got {row.get(key)!r}")
    for missing in sorted(set(expected) - seen):
        problems.append(f"{missing}: case was not submitted")
    return problems
