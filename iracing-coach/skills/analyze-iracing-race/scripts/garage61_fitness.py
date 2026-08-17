"""Garage61 provider status and claim-specific record fitness.

`G61-ISOLATION-001`, `G61-STATUS-001`, `G61-FITNESS-VECTOR-001` in its clarified
`G61-FITNESS-SHAPE-001` form, and the producer half of `G61-PRESENTATION-001`.

Two separate defects live here, and they fail in opposite directions.

**The status is one word for four different facts.** Startup awaits provider
health, and an *unavailable* validation can still finish by telling Joshua he is
connected. Those are not the same question. Whether a credential is *saved*,
whether it *authenticates*, whether the account has *permission*, and whether the
service is *reachable right now* are four independent axes, and no one of them
implies another. A DNS failure says nothing whatever about whether the stored
token is valid, so it must not be allowed to answer that question - in either
direction. :class:`Garage61Status` therefore keeps the four axes apart and makes
:attr:`Garage61Status.connected` a conjunction that only affirmative evidence can
satisfy. This is what "beta status never lies" reduces to in the producer.

**The fitness is one word for every claim.** The ranking path filters a lap out
entirely when its car or track does not match, which throws away records that
remain perfectly good support for a different claim. Joshua's position is that
Garage61 data stays potentially useful even under context mismatch. A record
whose corner geometry cannot be aligned may still carry an honest lap-time
reference or fuel estimate, provided the qualification travels with it. So
fitness is evaluated *per claim* and returns the qualifications alongside the
verdict; there is deliberately no global ``record.usable`` property to read,
because that property is the defect.

Nothing here performs I/O, opens a socket, or reads a clock. The provider probe
belongs to the transport, and the consumer phase owns lifecycle and rendering;
:func:`unprobed_status` exists precisely so a caller can publish local state
before any network work has happened.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

#: Version of the provider status and fitness contract.
GARAGE61_FITNESS_VERSION = 1

CREDENTIAL_ABSENT = "absent"
CREDENTIAL_SAVED = "saved"

#: Whether a credential exists locally. Saved is not valid: the whole point of
#: the separation is that the only thing a stored token proves is that it was
#: stored.
CREDENTIAL_STATES = (CREDENTIAL_ABSENT, CREDENTIAL_SAVED)

AUTH_UNVERIFIED = "unverified"
AUTH_VALID = "valid"
AUTH_REJECTED = "rejected"

#: Whether the credential authenticated. `unverified` is the honest answer
#: whenever nothing has actually authenticated it yet, including every case
#: where the attempt failed for a reason that was not authentication.
AUTHENTICATION_STATES = (AUTH_UNVERIFIED, AUTH_VALID, AUTH_REJECTED)

PERMISSION_UNVERIFIED = "unverified"
PERMISSION_GRANTED = "granted"
PERMISSION_DENIED = "denied"
PERMISSION_INSUFFICIENT_SCOPE = "insufficient_scope"

#: Whether the authenticated account may do the thing. Kept apart from
#: authentication because a valid token with a missing scope is a different
#: problem with a different remedy, and telling Joshua to re-authenticate would
#: be the wrong instruction.
PERMISSION_STATES = (
    PERMISSION_UNVERIFIED,
    PERMISSION_GRANTED,
    PERMISSION_DENIED,
    PERMISSION_INSUFFICIENT_SCOPE,
)

AVAILABILITY_UNVERIFIED = "unverified"
AVAILABILITY_AVAILABLE = "available"
AVAILABILITY_UNREACHABLE = "unreachable"
AVAILABILITY_TIMED_OUT = "timed_out"
AVAILABILITY_THROTTLED = "throttled"
AVAILABILITY_MALFORMED = "malformed"
AVAILABILITY_CANCELLED = "cancelled"

#: Whether the service answered usefully just now. Every non-available state is
#: transient in principle, which is exactly why none of them may be written back
#: onto the credential axes.
AVAILABILITY_STATES = (
    AVAILABILITY_UNVERIFIED,
    AVAILABILITY_AVAILABLE,
    AVAILABILITY_UNREACHABLE,
    AVAILABILITY_TIMED_OUT,
    AVAILABILITY_THROTTLED,
    AVAILABILITY_MALFORMED,
    AVAILABILITY_CANCELLED,
)

#: Probe outcomes the transport can report, named for what happened rather than
#: for what it might mean.
PROBE_OK = "ok"
PROBE_UNAUTHORIZED = "unauthorized"
PROBE_FORBIDDEN = "forbidden"
PROBE_INSUFFICIENT_SCOPE = "insufficient_scope"
PROBE_THROTTLED = "throttled"
PROBE_DNS_FAILURE = "dns_failure"
PROBE_CONNECT_FAILURE = "connect_failure"
PROBE_TIMEOUT = "timeout"
PROBE_CANCELLED = "cancelled"
PROBE_MALFORMED = "malformed"
PROBE_OVERSIZED = "oversized"

PROBE_OUTCOMES = (
    PROBE_OK,
    PROBE_UNAUTHORIZED,
    PROBE_FORBIDDEN,
    PROBE_INSUFFICIENT_SCOPE,
    PROBE_THROTTLED,
    PROBE_DNS_FAILURE,
    PROBE_CONNECT_FAILURE,
    PROBE_TIMEOUT,
    PROBE_CANCELLED,
    PROBE_MALFORMED,
    PROBE_OVERSIZED,
)

CLAIM_CORNER_COMPARISON = "corner_comparison"
CLAIM_RACING_LINE = "racing_line"
CLAIM_LAP_TIME_REFERENCE = "lap_time_reference"
CLAIM_FUEL_ESTIMATE = "fuel_estimate"
CLAIM_SETUP_DIRECTION = "setup_direction"

#: The claims an external record might be asked to support. Fitness is only
#: ever answered for one of these, never for the record as a whole.
FITNESS_CLAIMS = (
    CLAIM_CORNER_COMPARISON,
    CLAIM_RACING_LINE,
    CLAIM_LAP_TIME_REFERENCE,
    CLAIM_FUEL_ESTIMATE,
    CLAIM_SETUP_DIRECTION,
)

ALIGNMENT_ALIGNED = "aligned"
ALIGNMENT_APPROXIMATE = "approximate"
ALIGNMENT_UNALIGNED = "unaligned"
ALIGNMENT_UNKNOWN = "unknown"

#: How well the record's corner geometry lines up with ours. This is the axis
#: the clarified finding is really about: poor alignment is fatal to a corner
#: claim and irrelevant to a fuel claim, and one global verdict cannot say both.
ALIGNMENT_STATES = (
    ALIGNMENT_ALIGNED,
    ALIGNMENT_APPROXIMATE,
    ALIGNMENT_UNALIGNED,
    ALIGNMENT_UNKNOWN,
)

__all__ = [
    "ALIGNMENT_STATES",
    "AUTHENTICATION_STATES",
    "AVAILABILITY_STATES",
    "CREDENTIAL_STATES",
    "ClaimFitness",
    "FITNESS_CLAIMS",
    "GARAGE61_FITNESS_VERSION",
    "Garage61FitnessError",
    "Garage61Status",
    "PERMISSION_STATES",
    "PROBE_OUTCOMES",
    "RecordContext",
    "assess_claim",
    "assess_record",
    "status_after_probe",
    "unprobed_status",
]


class Garage61FitnessError(ValueError):
    """A status or fitness value violated the provider contract."""


@dataclass(frozen=True)
class Garage61Status:
    """Four independent facts about the provider, kept independent.

    There is no constructor shortcut that sets several axes at once, because
    every such shortcut in the previous shape was a place where one observation
    silently answered a question it had not asked.
    """

    credential: str
    authentication: str
    permission: str
    availability: str
    detail: str = ""

    def __post_init__(self) -> None:
        if self.credential not in CREDENTIAL_STATES:
            raise Garage61FitnessError(f"unknown credential state: {self.credential!r}")
        if self.authentication not in AUTHENTICATION_STATES:
            raise Garage61FitnessError(
                f"unknown authentication state: {self.authentication!r}"
            )
        if self.permission not in PERMISSION_STATES:
            raise Garage61FitnessError(f"unknown permission state: {self.permission!r}")
        if self.availability not in AVAILABILITY_STATES:
            raise Garage61FitnessError(
                f"unknown availability state: {self.availability!r}"
            )
        if self.credential == CREDENTIAL_ABSENT and self.authentication == AUTH_VALID:
            # Nothing can have authenticated if nothing was stored to
            # authenticate with.
            raise Garage61FitnessError(
                "a credential that is not saved cannot have authenticated"
            )
        if self.authentication != AUTH_VALID and self.permission == PERMISSION_GRANTED:
            # Permission is a fact about an authenticated account. Granting it
            # without authentication is how a hopeful default becomes a claim.
            raise Garage61FitnessError(
                "permission cannot be granted without a valid authentication"
            )

    @property
    def connected(self) -> bool:
        """Whether the app may state that Garage61 is working.

        A conjunction of affirmative evidence on every axis. Anything unverified
        is not connected, which is the direction the previous shape got wrong.
        """
        return (
            self.credential == CREDENTIAL_SAVED
            and self.authentication == AUTH_VALID
            and self.permission == PERMISSION_GRANTED
            and self.availability == AVAILABILITY_AVAILABLE
        )

    @property
    def blocks_local_startup(self) -> bool:
        """Always false. Local state never waits on this provider.

        Present as an explicit property rather than an omission so that a
        consumer cannot reintroduce the startup await without deleting a
        statement that says it must not.
        """
        return False

    @property
    def remedy(self) -> str:
        """The one action worth offering, or an empty string.

        Distinguishing these is the practical payoff of keeping the axes apart:
        telling Joshua to sign in again when the real problem is a missing scope
        or a service outage wastes his time and hides the actual fault.
        """
        if self.credential == CREDENTIAL_ABSENT:
            return "save_credential"
        if self.authentication == AUTH_REJECTED:
            return "replace_credential"
        if self.permission == PERMISSION_INSUFFICIENT_SCOPE:
            return "grant_scope"
        if self.permission == PERMISSION_DENIED:
            return "check_account_access"
        if self.availability in (
            AVAILABILITY_UNREACHABLE,
            AVAILABILITY_TIMED_OUT,
            AVAILABILITY_THROTTLED,
            AVAILABILITY_MALFORMED,
        ):
            return "retry_later"
        return ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "version": GARAGE61_FITNESS_VERSION,
            "credential": self.credential,
            "authentication": self.authentication,
            "permission": self.permission,
            "availability": self.availability,
            "connected": self.connected,
            "remedy": self.remedy,
            "detail": self.detail,
        }


def unprobed_status(credential_saved: bool) -> Garage61Status:
    """The status before anything has been asked of the network.

    This is the value startup publishes. Every axis except the purely local one
    is unverified, so the app can render provider state immediately without
    either waiting for a probe or guessing its result.
    """
    if not isinstance(credential_saved, bool):
        raise Garage61FitnessError("credential_saved must be a boolean")
    return Garage61Status(
        credential=CREDENTIAL_SAVED if credential_saved else CREDENTIAL_ABSENT,
        authentication=AUTH_UNVERIFIED,
        permission=PERMISSION_UNVERIFIED,
        availability=AVAILABILITY_UNVERIFIED,
        detail="not probed",
    )


def status_after_probe(previous: Garage61Status, outcome: str, detail: str = "") -> Garage61Status:
    """Fold one probe outcome into the status without over-claiming.

    The rule that matters is what each outcome is *silent* about. A timeout
    reports availability and nothing else, so the authentication axis keeps
    whatever it already honestly held rather than being downgraded to rejected
    or promoted to valid.
    """
    if outcome not in PROBE_OUTCOMES:
        raise Garage61FitnessError(f"unknown probe outcome: {outcome!r}")
    if previous.credential == CREDENTIAL_ABSENT and outcome != PROBE_CANCELLED:
        # A probe without a credential is a programming error, not a status.
        raise Garage61FitnessError("cannot probe the provider without a saved credential")

    authentication = previous.authentication
    permission = previous.permission

    if outcome == PROBE_OK:
        authentication = AUTH_VALID
        permission = PERMISSION_GRANTED
        availability = AVAILABILITY_AVAILABLE
    elif outcome == PROBE_UNAUTHORIZED:
        # The service answered, so it is reachable; the credential is the fault.
        authentication = AUTH_REJECTED
        permission = PERMISSION_UNVERIFIED
        availability = AVAILABILITY_AVAILABLE
    elif outcome == PROBE_FORBIDDEN:
        # A 403 means the credential authenticated and the account was refused.
        authentication = AUTH_VALID
        permission = PERMISSION_DENIED
        availability = AVAILABILITY_AVAILABLE
    elif outcome == PROBE_INSUFFICIENT_SCOPE:
        authentication = AUTH_VALID
        permission = PERMISSION_INSUFFICIENT_SCOPE
        availability = AVAILABILITY_AVAILABLE
    elif outcome == PROBE_THROTTLED:
        availability = AVAILABILITY_THROTTLED
    elif outcome in (PROBE_DNS_FAILURE, PROBE_CONNECT_FAILURE):
        availability = AVAILABILITY_UNREACHABLE
    elif outcome == PROBE_TIMEOUT:
        availability = AVAILABILITY_TIMED_OUT
    elif outcome == PROBE_CANCELLED:
        availability = AVAILABILITY_CANCELLED
    else:  # PROBE_MALFORMED, PROBE_OVERSIZED
        # The bytes arrived and could not be believed. Reachable, but the
        # response proves nothing about the account behind it.
        availability = AVAILABILITY_MALFORMED

    if permission == PERMISSION_GRANTED and authentication != AUTH_VALID:
        permission = PERMISSION_UNVERIFIED

    return Garage61Status(
        credential=previous.credential,
        authentication=authentication,
        permission=permission,
        availability=availability,
        detail=detail,
    )


@dataclass(frozen=True)
class RecordContext:
    """The context an external record was produced in, or is wanted for.

    Deliberately small and entirely non-identifying: car and layout keys, the
    session shape, and how well the geometry aligns. No driver, account, lap
    ownership or any other field that would make this a record about a person.
    """

    car_key: str
    track_layout_key: str
    session_type: str = ""
    tire_compound: str = ""
    corner_alignment: str = ALIGNMENT_UNKNOWN
    telemetry_readable: bool = True
    lap_is_clean: bool = True

    def __post_init__(self) -> None:
        if not self.car_key:
            raise Garage61FitnessError("a record context needs a car key")
        if not self.track_layout_key:
            raise Garage61FitnessError("a record context needs a track layout key")
        if self.corner_alignment not in ALIGNMENT_STATES:
            raise Garage61FitnessError(
                f"unknown corner alignment: {self.corner_alignment!r}"
            )
        if not isinstance(self.telemetry_readable, bool):
            raise Garage61FitnessError("telemetry_readable must be a boolean")
        if not isinstance(self.lap_is_clean, bool):
            raise Garage61FitnessError("lap_is_clean must be a boolean")


@dataclass(frozen=True)
class ClaimFitness:
    """Whether one record may support one claim, and under what qualification.

    ``usable`` is never true without the qualifications travelling with it. A
    consumer that renders the boolean and drops the strings has produced exactly
    the unqualified transfer this contract exists to prevent, which is why
    :meth:`to_payload` emits them together and never emits the boolean alone.
    """

    claim: str
    usable: bool
    blocking: tuple[str, ...] = ()
    qualifications: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.claim not in FITNESS_CLAIMS:
            raise Garage61FitnessError(f"unknown fitness claim: {self.claim!r}")
        if self.usable and self.blocking:
            raise Garage61FitnessError("a usable claim cannot carry a blocking reason")
        if not self.usable and not self.blocking:
            raise Garage61FitnessError("an unusable claim must say what blocked it")

    def to_payload(self) -> dict[str, Any]:
        return {
            "claim": self.claim,
            "usable": self.usable,
            "blocking": list(self.blocking),
            "qualifications": list(self.qualifications),
        }


#: Which context dimensions each claim cannot survive a mismatch in. Written as
#: data so the asymmetry between claims is inspectable rather than buried in
#: branches: a fuel estimate tolerates unaligned corners, a corner comparison
#: does not.
_REQUIRED_MATCHES: Mapping[str, tuple[str, ...]] = {
    CLAIM_CORNER_COMPARISON: ("car", "layout"),
    CLAIM_RACING_LINE: ("car", "layout"),
    CLAIM_LAP_TIME_REFERENCE: ("car", "layout"),
    CLAIM_FUEL_ESTIMATE: ("car", "layout"),
    CLAIM_SETUP_DIRECTION: ("car",),
}

#: The weakest alignment each claim can still be stated from. Claims absent
#: from this map do not read the geometry at all.
_MINIMUM_ALIGNMENT: Mapping[str, tuple[str, ...]] = {
    CLAIM_CORNER_COMPARISON: (ALIGNMENT_ALIGNED, ALIGNMENT_APPROXIMATE),
    CLAIM_RACING_LINE: (ALIGNMENT_ALIGNED,),
}

#: Claims that cannot be stated without reading the record's telemetry.
_NEEDS_TELEMETRY = (CLAIM_CORNER_COMPARISON, CLAIM_RACING_LINE)

#: Claims a dirty lap destroys outright, as opposed to merely qualifying.
_NEEDS_CLEAN_LAP = (CLAIM_LAP_TIME_REFERENCE, CLAIM_RACING_LINE)


def assess_claim(record: RecordContext, request: RecordContext, claim: str) -> ClaimFitness:
    """Decide whether one record supports one claim in one requested context."""

    if claim not in FITNESS_CLAIMS:
        raise Garage61FitnessError(f"unknown fitness claim: {claim!r}")
    if not isinstance(record, RecordContext) or not isinstance(request, RecordContext):
        raise Garage61FitnessError("fitness needs two record contexts")

    blocking: list[str] = []
    qualifications: list[str] = []

    required = _REQUIRED_MATCHES[claim]
    car_matches = record.car_key == request.car_key
    layout_matches = record.track_layout_key == request.track_layout_key

    if not car_matches:
        if "car" in required:
            blocking.append("the record is from a different car")
        else:
            qualifications.append("the record is from a different car")
    if not layout_matches:
        if "layout" in required:
            blocking.append("the record is from a different track layout")
        else:
            qualifications.append("the record is from a different track layout")

    permitted_alignment = _MINIMUM_ALIGNMENT.get(claim)
    if permitted_alignment is not None:
        if record.corner_alignment not in permitted_alignment:
            blocking.append(
                f"corner geometry is {record.corner_alignment} and this claim needs "
                + " or ".join(permitted_alignment)
            )
        elif record.corner_alignment == ALIGNMENT_APPROXIMATE:
            qualifications.append("corner geometry is only approximately aligned")
    elif record.corner_alignment in (ALIGNMENT_UNALIGNED, ALIGNMENT_UNKNOWN):
        # Explicitly recorded rather than ignored, so the surface can show why
        # this claim survived a mismatch that killed the corner claim beside it.
        qualifications.append(
            "stated without corner alignment, which this claim does not depend on"
        )

    if claim in _NEEDS_TELEMETRY and not record.telemetry_readable:
        blocking.append("the record's telemetry is not readable")
    if not record.lap_is_clean:
        if claim in _NEEDS_CLEAN_LAP:
            blocking.append("the record's lap is not clean")
        else:
            qualifications.append("the record's lap is not clean")

    if record.session_type and request.session_type and record.session_type != request.session_type:
        qualifications.append(
            f"recorded in a {record.session_type} session rather than {request.session_type}"
        )
    if (
        record.tire_compound
        and request.tire_compound
        and record.tire_compound != request.tire_compound
    ):
        qualifications.append(
            f"recorded on {record.tire_compound} rather than {request.tire_compound}"
        )

    return ClaimFitness(
        claim=claim,
        usable=not blocking,
        blocking=tuple(blocking),
        qualifications=tuple(qualifications),
    )


def assess_record(
    record: RecordContext,
    request: RecordContext,
    claims: Iterable[str] = FITNESS_CLAIMS,
) -> dict[str, Any]:
    """Assess one record against every requested claim.

    Returns the per-claim verdicts and a count, and deliberately does not return
    an overall verdict. Callers that want to know whether a record is worth
    keeping should ask which claims it supports.
    """
    assessed = [assess_claim(record, request, claim) for claim in claims]
    if not assessed:
        raise Garage61FitnessError("fitness needs at least one claim to assess")
    return {
        "version": GARAGE61_FITNESS_VERSION,
        "claims": [item.to_payload() for item in assessed],
        "usable_claims": sorted(item.claim for item in assessed if item.usable),
    }
