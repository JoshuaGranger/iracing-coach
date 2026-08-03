"""Small, dependency-free client for Garage61's documented v1 API.

The adapter intentionally uses only public endpoints and preserves Garage61 CSV
headers verbatim. Standard Garage61 applications can access personal and team
driving data; searching every otherwise-visible community lap requires separate
approval from Garage61 and is reported explicitly by :meth:`health_check`.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:  # Support both package imports and direct execution from the scripts folder.
    from .secure_store import load_token
except ImportError:  # pragma: no cover - exercised by skill-script execution.
    from secure_store import load_token


DEFAULT_BASE_URL = "https://garage61.net/api/v1"
_PRODUCTION_HOST = "garage61.net"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
GLOBAL_VISIBLE_LAPS_NOTICE = (
    "Garage61 limits standard applications to the authenticated driver's and "
    "teammates' laps. Searching all community laps visible on the website "
    "requires Garage61 to approve the application for global visible-lap access."
)


class Garage61Error(RuntimeError):
    """Base class for safe Garage61 adapter errors."""


class Garage61AuthError(Garage61Error):
    """Raised when the token is absent, invalid, expired, or revoked."""


class Garage61PermissionError(Garage61Error):
    """Raised when Garage61 denies an operation for this user or application."""


class Garage61TransportError(Garage61Error):
    """Raised for DNS, TLS, timeout, and other transport failures."""


class Garage61ResponseError(Garage61Error):
    """Raised when Garage61 returns an unexpected response."""


class Garage61CapabilityError(Garage61Error):
    """Raised when an operation requires a capability not known to be approved."""


def _url_origin(url: str) -> tuple[str, str, int]:
    """Return a normalized HTTP(S) origin, including its effective port."""

    parsed = urllib.parse.urlsplit(url)
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    if scheme not in {"http", "https"} or not hostname:
        raise ValueError("URL must be an absolute HTTP(S) URL")
    try:
        explicit_port = parsed.port
    except ValueError as exc:
        raise ValueError("URL contains an invalid port") from exc
    port = explicit_port if explicit_port is not None else (443 if scheme == "https" else 80)
    return scheme, hostname, port


def _validate_authenticated_base_url(base_url: str) -> tuple[str, tuple[str, str, int]]:
    """Allow the official API origin, plus literal loopback origins for tests."""

    normalized = base_url.rstrip("/")
    parsed = urllib.parse.urlsplit(normalized)
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("base_url must not contain URL credentials")
    origin = _url_origin(normalized)
    scheme, hostname, port = origin
    production = scheme == "https" and hostname == _PRODUCTION_HOST and port == 443
    loopback = hostname in _LOOPBACK_HOSTS and scheme in {"http", "https"}
    if not production and not loopback:
        raise ValueError(
            "base_url must use the exact HTTPS Garage61 origin "
            "https://garage61.net, or an HTTP(S) loopback origin for tests"
        )
    return normalized, origin


class _SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects before urllib can copy Authorization across origins."""

    def __init__(self, allowed_origin: tuple[str, str, int]) -> None:
        super().__init__()
        self._allowed_origin = allowed_origin

    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> urllib.request.Request | None:
        try:
            redirect_origin = _url_origin(new_url)
        except ValueError as exc:
            raise Garage61ResponseError(
                "Garage61 returned a redirect with an invalid destination."
            ) from exc
        if redirect_origin != self._allowed_origin:
            raise Garage61ResponseError(
                "Garage61 refused a cross-origin redirect to protect the stored credential."
            )
        return super().redirect_request(
            request, file_pointer, code, message, headers, new_url
        )


@dataclass(frozen=True)
class TelemetryCSV:
    """Parsed CSV with the original header names, order, and row values intact."""

    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]

    def as_dicts(self) -> list[dict[str, str]]:
        """Return rows as dictionaries when all header names are unique."""

        if len(set(self.headers)) != len(self.headers):
            raise Garage61ResponseError(
                "Telemetry CSV contains duplicate headers; use positional rows instead."
            )
        output: list[dict[str, str]] = []
        for values in self.rows:
            padded = values + ("",) * max(0, len(self.headers) - len(values))
            output.append(dict(zip(self.headers, padded[: len(self.headers)])))
        return output


@dataclass(frozen=True)
class RankedLap:
    """A Garage61 lap plus a transparent comparability score and explanation."""

    lap: Mapping[str, Any]
    score: float
    pace_delta_seconds: float | None
    setup_type: str | None
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["lap"] = dict(self.lap)
        return result


@dataclass(frozen=True)
class ContentCatalog:
    """Garage61/iRacing content maps used to resolve stable external IDs."""

    cars: tuple[Mapping[str, Any], ...]
    tracks: tuple[Mapping[str, Any], ...]
    seasons: tuple[Mapping[str, Any], ...]

    @property
    def cars_by_id(self) -> dict[int, Mapping[str, Any]]:
        return {
            int(item["id"]): item
            for item in self.cars
            if _is_number(item.get("id"))
        }

    @property
    def cars_by_platform_id(self) -> dict[str, Mapping[str, Any]]:
        return {
            str(item["platform_id"]): item
            for item in self.cars
            if item.get("platform_id") not in (None, "")
        }

    @property
    def tracks_by_id(self) -> dict[int, Mapping[str, Any]]:
        return {
            int(item["id"]): item
            for item in self.tracks
            if _is_number(item.get("id"))
        }

    @property
    def tracks_by_platform_id(self) -> dict[str, tuple[Mapping[str, Any], ...]]:
        grouped: dict[str, list[Mapping[str, Any]]] = {}
        for item in self.tracks:
            platform_id = item.get("platform_id")
            if platform_id in (None, ""):
                continue
            grouped.setdefault(str(platform_id), []).append(item)
        return {key: tuple(values) for key, values in grouped.items()}

    @property
    def seasons_by_id(self) -> dict[int, Mapping[str, Any]]:
        return {
            int(item["id"]): item
            for item in self.seasons
            if _is_number(item.get("id"))
        }


class Garage61Client:
    """Authenticated Garage61 API client using a Bearer personal access token.

    Args:
        token: Garage61 PAT. If omitted, load it from the DPAPI secure store.
        base_url: Garage61 v1 API root, overridable for tests.
        timeout: Per-request timeout in seconds.
        global_visible_laps_approved: Set to ``True`` only after Garage61 has
            approved this application for global visible-lap search. ``None``
            means unknown and is the safe default.
    """

    def __init__(
        self,
        token: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        *,
        timeout: float = 30.0,
        global_visible_laps_approved: bool | None = None,
        user_agent: str = "iRacingCoach/1.0",
    ) -> None:
        resolved_token = token if token is not None else load_token()
        if not isinstance(resolved_token, str) or not resolved_token.strip():
            raise Garage61AuthError("Garage61 token is empty; configure it again.")
        if any(character in resolved_token for character in ("\r", "\n", "\x00")):
            raise Garage61AuthError("Garage61 token contains an invalid control character.")
        if timeout <= 0 or not math.isfinite(timeout):
            raise ValueError("timeout must be a positive finite number")
        normalized_base_url, authenticated_origin = _validate_authenticated_base_url(base_url)

        self._token = resolved_token.strip()
        self.base_url = normalized_base_url
        self.timeout = float(timeout)
        self.global_visible_laps_approved = global_visible_laps_approved
        self.user_agent = user_agent
        self._opener = urllib.request.build_opener(
            _SameOriginRedirectHandler(authenticated_origin)
        )

    def me(self) -> dict[str, Any]:
        """Return Garage61 identity and permissions for the current token."""

        return self._request_json("/me")

    def health_check(self) -> dict[str, Any]:
        """Validate authentication and report data-scope capabilities clearly."""

        identity = self.me()
        permissions = tuple(str(value) for value in identity.get("apiPermissions", ()))
        driving_data = "driving_data" in permissions
        if self.global_visible_laps_approved is True:
            global_status = "approved_by_configuration"
        elif self.global_visible_laps_approved is False:
            global_status = "not_approved"
        else:
            global_status = "unknown_requires_garage61_approval"
        return {
            "ok": True,
            "identity": {
                key: identity.get(key)
                for key in ("id", "slug", "firstName", "lastName", "subscriptionPlan")
                if key in identity
            },
            "api_permissions": list(permissions),
            "capabilities": {
                "driving_data": {
                    "available": driving_data,
                    "status": "granted" if driving_data else "not_granted",
                },
                "personal_and_team_laps": {
                    "available": driving_data,
                    "status": "available" if driving_data else "not_available",
                },
                "global_visible_laps": {
                    "available": self.global_visible_laps_approved is True,
                    "status": global_status,
                    "diagnostic": GLOBAL_VISIBLE_LAPS_NOTICE,
                },
            },
        }

    def require_global_visible_laps(self) -> None:
        """Fail safely unless global visible-lap approval is explicitly configured."""

        if self.global_visible_laps_approved is not True:
            raise Garage61CapabilityError(GLOBAL_VISIBLE_LAPS_NOTICE)

    def list_cars(self) -> list[dict[str, Any]]:
        """Return Garage61 cars, including iRacing platform IDs when available."""

        return self._collection("/cars")

    def list_tracks(self) -> list[dict[str, Any]]:
        """Return exact Garage61 track layouts and external platform IDs."""

        return self._collection("/tracks")

    def list_platforms(self) -> list[dict[str, Any]]:
        """Return platforms and their season IDs/date ranges."""

        return self._collection("/platforms")

    def content_catalog(self) -> ContentCatalog:
        """Load Garage61 cars, tracks, and flattened season mappings."""

        cars = self.list_cars()
        tracks = self.list_tracks()
        seasons: list[Mapping[str, Any]] = []
        for platform in self.list_platforms():
            platform_id = platform.get("id")
            for raw_season in platform.get("seasons", ()) or ():
                if not isinstance(raw_season, Mapping):
                    continue
                season = dict(raw_season)
                season.setdefault("platform", platform_id)
                seasons.append(season)
        return ContentCatalog(tuple(cars), tuple(tracks), tuple(seasons))

    def find_laps(self, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Search Garage61 laps with documented query parameters.

        Garage61 requires at least one track ID as of 2026-02-15. Sequence
        values are serialized as comma-separated values, matching its OpenAPI
        declaration. Unknown parameters are preserved so the adapter remains
        forward-compatible with additions to the API.
        """

        if not isinstance(params, Mapping):
            raise TypeError("params must be a mapping")
        tracks = params.get("tracks")
        if tracks is None or tracks == "" or (
            isinstance(tracks, Sequence)
            and not isinstance(tracks, (str, bytes, bytearray))
            and len(tracks) == 0
        ):
            raise ValueError("Garage61 lap searches require the 'tracks' parameter.")
        return self._collection("/laps", params=params)

    def find_comparable_laps(
        self,
        target_lap: Mapping[str, Any],
        *,
        setup_type: str | int | None = None,
        session_types: Sequence[int] | None = None,
        top_n: int = 8,
        search_limit: int = 500,
        require_global_visible: bool = False,
    ) -> list[RankedLap]:
        """Find and rank telemetry laps comparable to *target_lap*.

        If ``setup_type`` is omitted, fixed and open searches are performed
        separately because Garage61 accepts setup type as a filter but does not
        include it in the returned Lap object. Each candidate is tagged locally
        before ranking.
        """

        if require_global_visible:
            self.require_global_visible_laps()
        car_id = _nested_id(target_lap.get("car"))
        track_id = _nested_id(target_lap.get("track"))
        if car_id is None or track_id is None:
            raise ValueError("target_lap must include Garage61 car.id and track.id")
        if top_n <= 0:
            return []
        if search_limit <= 0 or search_limit > 1000:
            raise ValueError("search_limit must be between 1 and 1000")

        setup_ids = [_setup_type_id(setup_type)] if setup_type is not None else [1, 2]
        candidates: dict[str, dict[str, Any]] = {}
        for setup_id in setup_ids:
            params: dict[str, Any] = {
                "cars": [car_id],
                "tracks": [track_id],
                "sessionSetupTypes": [setup_id],
                "lapTypes": [1],
                "unclean": False,
                "seeTelemetry": True,
                "group": "none",
                "limit": search_limit,
            }
            season_id = _nested_id(target_lap.get("season"))
            if season_id is not None:
                params["seasons"] = [season_id]
            if session_types:
                params["sessionTypes"] = list(session_types)
            elif _is_number(target_lap.get("sessionType")):
                params["sessionTypes"] = [int(target_lap["sessionType"])]

            setup_name = "open" if setup_id == 1 else "fixed"
            for raw_lap in self.find_laps(params):
                candidate = dict(raw_lap)
                candidate["_comparisonSetupType"] = setup_name
                if season_id is not None:
                    # Lap responses do not currently carry the season ID, but
                    # this batch was explicitly filtered to one season.
                    candidate["_comparisonSeasonId"] = season_id
                lap_id = str(candidate.get("id", ""))
                if lap_id:
                    candidates[lap_id] = candidate

        return rank_comparable_laps(
            candidates.values(),
            target_lap,
            setup_type=setup_type,
            top_n=top_n,
        )

    def get_lap_csv(self, lap_id: str) -> TelemetryCSV:
        """Download and parse a lap CSV without normalizing or dropping columns."""

        return parse_telemetry_csv(self._download_lap_csv_bytes(lap_id))

    def download_lap_csv(
        self,
        lap_id: str,
        dest: str | os.PathLike[str],
        *,
        overwrite: bool = False,
    ) -> Path:
        """Download a lap CSV atomically while preserving the original bytes."""

        destination = Path(dest).expanduser().resolve(strict=False)
        if destination.exists() and not overwrite:
            raise FileExistsError(f"Refusing to overwrite existing telemetry CSV: {destination}")
        data = self._download_lap_csv_bytes(lap_id)
        # Validate that the response is parseable before it replaces a cache file.
        parse_telemetry_csv(data)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(data)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_name = temporary.name
            os.replace(temporary_name, destination)
            temporary_name = None
        finally:
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass
        return destination

    def _download_lap_csv_bytes(self, lap_id: str) -> bytes:
        normalized_id = _safe_identifier(lap_id, "lap_id")
        return self._request_bytes(f"/laps/{urllib.parse.quote(normalized_id, safe='')}/csv")

    def _collection(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        payload = self._request_json(path, params=params)
        items = payload.get("items")
        if not isinstance(items, list):
            raise Garage61ResponseError(
                f"Garage61 returned an invalid collection for {path}."
            )
        if not all(isinstance(item, dict) for item in items):
            raise Garage61ResponseError(
                f"Garage61 returned a collection with invalid items for {path}."
            )
        return items

    def _request_json(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = self._request_bytes(path, params=params, accept="application/json")
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise Garage61ResponseError("Garage61 returned invalid JSON.") from exc
        if not isinstance(payload, dict):
            raise Garage61ResponseError("Garage61 returned an unexpected JSON value.")
        return payload

    def _request_bytes(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        accept: str = "*/*",
    ) -> bytes:
        url = self._url(path, params)
        request = urllib.request.Request(
            url,
            headers={
                "Accept": accept,
                "Authorization": f"Bearer {self._token}",
                "User-Agent": self.user_agent,
            },
            method="GET",
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            detail = _http_error_detail(exc, sensitive_values=(self._token,))
            if exc.code == 401:
                raise Garage61AuthError(
                    "Garage61 rejected the credential. Re-run configure-garage61.ps1."
                ) from exc
            if exc.code == 403:
                raise Garage61PermissionError(
                    f"Garage61 denied this operation{detail}. Check privacy, plan, and API permissions."
                ) from exc
            raise Garage61ResponseError(
                f"Garage61 returned HTTP {exc.code}{detail}."
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise Garage61TransportError(
                "Could not reach Garage61. Check the internet connection and try again."
            ) from exc

    def _url(self, path: str, params: Mapping[str, Any] | None) -> str:
        if not path.startswith("/"):
            raise ValueError("Garage61 API path must start with '/'.")
        url = self.base_url + path
        encoded = _encode_query(params or {})
        return f"{url}?{encoded}" if encoded else url


def parse_telemetry_csv(data: bytes | str) -> TelemetryCSV:
    """Parse Garage61 CSV while retaining every original header and value."""

    if isinstance(data, bytes):
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise Garage61ResponseError("Garage61 telemetry CSV is not UTF-8.") from exc
    elif isinstance(data, str):
        text = data
    else:
        raise TypeError("telemetry CSV data must be bytes or str")
    try:
        reader = csv.reader(io.StringIO(text, newline=""))
        header_row = next(reader)
        rows = tuple(tuple(value for value in row) for row in reader)
    except (csv.Error, StopIteration) as exc:
        raise Garage61ResponseError("Garage61 telemetry CSV is empty or malformed.") from exc
    headers = tuple(value for value in header_row)
    if not headers or not any(value.strip() for value in headers):
        raise Garage61ResponseError("Garage61 telemetry CSV has no headers.")
    return TelemetryCSV(headers=headers, rows=rows)


def rank_comparable_laps(
    laps: Iterable[Mapping[str, Any]],
    target_lap: Mapping[str, Any],
    *,
    setup_type: str | int | None = None,
    top_n: int = 8,
    require_telemetry: bool = True,
    require_clean: bool = True,
) -> list[RankedLap]:
    """Rank representative laps against a target using documented metadata.

    Higher scores are better. The ranking favors a modestly faster lap with
    matching season, session, weather, rubber, fuel, tire compound, and BoP over
    an incomparable world-record lap. Invalid, missing, or inaccessible laps are
    excluded by default.
    """

    if top_n <= 0:
        return []
    target_car = _nested_id(target_lap.get("car"))
    target_track = _nested_id(target_lap.get("track"))
    if target_car is None or target_track is None:
        raise ValueError("target_lap must include Garage61 car.id and track.id")
    desired_setup = _setup_type_name(setup_type) if setup_type is not None else None
    target_time = _float_or_none(target_lap.get("lapTime"))
    desired_gap = (
        max(0.25, min(1.5, target_time * 0.02))
        if target_time is not None and target_time > 0
        else None
    )

    ranked: list[RankedLap] = []
    for raw_lap in laps:
        if not isinstance(raw_lap, Mapping):
            continue
        if _nested_id(raw_lap.get("car")) != target_car:
            continue
        if _nested_id(raw_lap.get("track")) != target_track:
            continue
        if require_telemetry and raw_lap.get("canViewTelemetry") is not True:
            continue
        if require_clean and (
            raw_lap.get("clean") is not True
            or raw_lap.get("missing") is True
            or raw_lap.get("incomplete") is True
            or raw_lap.get("discontinuity") is True
        ):
            continue

        candidate_setup = _setup_type_name(raw_lap.get("_comparisonSetupType"))
        if desired_setup is not None and candidate_setup not in (None, desired_setup):
            continue
        score = 100.0
        reasons: list[str] = []

        target_season = _nested_id(target_lap.get("season"))
        candidate_season = _nested_id(raw_lap.get("season"))
        if candidate_season is None:
            candidate_season = _nested_id(raw_lap.get("_comparisonSeasonId"))
        if target_season is not None and candidate_season is not None:
            if candidate_season != target_season:
                score -= 25.0
                reasons.append("different season")
            else:
                reasons.append("same season")
        target_session_type = target_lap.get("sessionType")
        candidate_session_type = raw_lap.get("sessionType")
        if (
            target_session_type is not None
            and candidate_session_type is not None
            and target_session_type != candidate_session_type
        ):
            score -= 10.0
            reasons.append("different session type")
        target_event_type = target_lap.get("eventType")
        candidate_event_type = raw_lap.get("eventType")
        if (
            target_event_type is not None
            and candidate_event_type is not None
            and target_event_type != candidate_event_type
        ):
            score -= 5.0
            reasons.append("different event type")

        score = _penalize_numeric(score, reasons, target_lap, raw_lap, "trackTemp", 8.0, 12.0)
        score = _penalize_numeric(score, reasons, target_lap, raw_lap, "airTemp", 8.0, 6.0)
        score = _penalize_numeric(score, reasons, target_lap, raw_lap, "trackUsage", 20.0, 10.0)
        score = _penalize_numeric(score, reasons, target_lap, raw_lap, "trackWetness", 10.0, 18.0)
        score = _penalize_numeric(score, reasons, target_lap, raw_lap, "fuelLevel", 10.0, 8.0)
        score = _penalize_numeric(score, reasons, target_lap, raw_lap, "driverRating", 1000.0, 6.0)
        score = _penalize_numeric(score, reasons, target_lap, raw_lap, "weightPenalty", 10.0, 12.0)
        score = _penalize_numeric(score, reasons, target_lap, raw_lap, "powerAdjust", 5.0, 12.0)

        target_compound = target_lap.get("tireCompound")
        candidate_compound = raw_lap.get("tireCompound")
        if (
            target_compound is not None
            and candidate_compound is not None
            and target_compound != candidate_compound
        ):
            score -= 15.0
            reasons.append("different tire compound")

        candidate_time = _float_or_none(raw_lap.get("lapTime"))
        pace_delta: float | None = None
        if target_time is not None and candidate_time is not None:
            pace_delta = target_time - candidate_time
            if pace_delta < 0:
                score -= min(30.0, 10.0 + abs(pace_delta) * 8.0)
                reasons.append(f"{abs(pace_delta):.3f}s slower than target")
            elif desired_gap is not None:
                pace_penalty = min(24.0, abs(pace_delta - desired_gap) * 8.0)
                score -= pace_penalty
                reasons.append(f"{pace_delta:.3f}s faster than target")

        score = round(max(0.0, min(100.0, score)), 3)
        ranked.append(
            RankedLap(
                lap=dict(raw_lap),
                score=score,
                pace_delta_seconds=pace_delta,
                setup_type=candidate_setup,
                reasons=tuple(reasons),
            )
        )

    def sort_key(item: RankedLap) -> tuple[float, float, float, str]:
        if item.pace_delta_seconds is None or desired_gap is None:
            pace_distance = float("inf")
        else:
            pace_distance = abs(item.pace_delta_seconds - desired_gap)
        lap_time = _float_or_none(item.lap.get("lapTime"))
        return (-item.score, pace_distance, lap_time or float("inf"), str(item.lap.get("id", "")))

    ranked.sort(key=sort_key)
    return ranked[:top_n]


def _penalize_numeric(
    score: float,
    reasons: list[str],
    target: Mapping[str, Any],
    candidate: Mapping[str, Any],
    field: str,
    tolerance: float,
    maximum_penalty: float,
) -> float:
    target_value = _float_or_none(target.get(field))
    candidate_value = _float_or_none(candidate.get(field))
    if target_value is None or candidate_value is None:
        return score
    difference = abs(target_value - candidate_value)
    penalty = min(maximum_penalty, difference / tolerance * maximum_penalty)
    if penalty >= 1.0:
        reasons.append(f"{field} differs by {difference:.2f}")
    return score - penalty


def _encode_query(params: Mapping[str, Any]) -> str:
    encoded: list[tuple[str, str]] = []
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            serialized = "true" if value else "false"
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            serialized = ",".join(_scalar_query_value(item) for item in value)
        else:
            serialized = _scalar_query_value(value)
        encoded.append((str(key), serialized))
    return urllib.parse.urlencode(encoded)


def _scalar_query_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value)
    raise TypeError(f"Unsupported Garage61 query value type: {type(value).__name__}")


def _http_error_detail(
    error: urllib.error.HTTPError,
    *,
    sensitive_values: Sequence[str] = (),
) -> str:
    try:
        body = error.read(64 * 1024)
        payload = json.loads(body.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, Mapping):
        return ""
    code = payload.get("code")
    message = payload.get("message")
    pieces: list[str] = []
    for value in (code, message):
        if not value:
            continue
        safe_value = str(value).replace("\r", " ").replace("\n", " ")
        for sensitive in sensitive_values:
            if sensitive:
                safe_value = safe_value.replace(sensitive, "[REDACTED]")
        pieces.append(safe_value[:160])
    return ": " + " - ".join(pieces) if pieces else ""


def _safe_identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    normalized = value.strip()
    if any(character in normalized for character in ("\r", "\n", "\x00")):
        raise ValueError(f"{name} contains an invalid control character")
    return normalized


def _nested_id(value: Any) -> int | None:
    if isinstance(value, Mapping):
        value = value.get("id")
    if _is_number(value):
        return int(value)
    return None


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _float_or_none(value: Any) -> float | None:
    if _is_number(value):
        return float(value)
    return None


def _setup_type_id(value: str | int) -> int:
    if isinstance(value, bool):
        raise ValueError("setup_type must be 'open', 'fixed', 1, or 2")
    if isinstance(value, int) and value in (1, 2):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "open":
            return 1
        if normalized == "fixed":
            return 2
    raise ValueError("setup_type must be 'open', 'fixed', 1, or 2")


def _setup_type_name(value: Any) -> str | None:
    if value in (1, "1", "open", "Open"):
        return "open"
    if value in (2, "2", "fixed", "Fixed"):
        return "fixed"
    return None


__all__ = [
    "ContentCatalog",
    "DEFAULT_BASE_URL",
    "GLOBAL_VISIBLE_LAPS_NOTICE",
    "Garage61AuthError",
    "Garage61CapabilityError",
    "Garage61Client",
    "Garage61Error",
    "Garage61PermissionError",
    "Garage61ResponseError",
    "Garage61TransportError",
    "RankedLap",
    "TelemetryCSV",
    "parse_telemetry_csv",
    "rank_comparable_laps",
]
