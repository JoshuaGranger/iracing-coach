"""Focused stdlib tests for the Garage61 API and secure-store adapters."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "analyze-iracing-race"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

import garage61_client  # noqa: E402
import secure_store  # noqa: E402


class _Garage61Handler(BaseHTTPRequestHandler):
    token = "test-pat"
    requests: list[tuple[str, dict[str, list[str]]]] = []
    redirect_me_to: str | None = None

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        if self.headers.get("Authorization") != f"Bearer {self.token}":
            self._json(401, {"code": "unauthorized", "message": "bad token"})
            return
        parsed = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        self.requests.append((parsed.path, query))
        if parsed.path == "/api/v1/me" and self.redirect_me_to is not None:
            self.send_response(302)
            self.send_header("Location", self.redirect_me_to)
            self.end_headers()
        elif parsed.path in {"/api/v1/me", "/api/v1/redirected-me"}:
            self._json(
                200,
                {
                    "id": "user-1",
                    "slug": "driver",
                    "firstName": "Test",
                    "lastName": "Driver",
                    "subscriptionPlan": "pro",
                    "apiPermissions": ["driving_data"],
                    "teams": [],
                },
            )
        elif parsed.path == "/api/v1/cars":
            self._json(200, {"items": [{"id": 10, "name": "Cup", "platform_id": "99"}], "total": 1})
        elif parsed.path == "/api/v1/tracks":
            self._json(
                200,
                {
                    "items": [
                        {"id": 20, "name": "Iowa", "variant": "Oval", "platform_id": "88"}
                    ],
                    "total": 1,
                },
            )
        elif parsed.path == "/api/v1/platforms":
            self._json(
                200,
                {
                    "items": [
                        {
                            "id": "iracing",
                            "name": "iRacing",
                            "seasons": [{"id": 263, "name": "2026 Season 3"}],
                        }
                    ],
                    "total": 1,
                },
            )
        elif parsed.path == "/api/v1/laps":
            setup_id = query.get("sessionSetupTypes", [""])[0]
            setup_offset = 0.0 if setup_id == "2" else 0.2
            self._json(
                200,
                {
                    "items": [
                        _lap(
                            lap_id=f"lap-{setup_id}",
                            lap_time=29.4 + setup_offset,
                            track_temp=32.0,
                        )
                    ],
                    "total": 1,
                },
            )
        elif parsed.path == "/api/v1/laps/lap-2/csv":
            body = (
                "Time,LapDistPct,Speed,FutureUnknown,PositionType\r\n"
                "0.0,0.0,50.0,kept,3\r\n"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._json(404, {"code": "not_found", "message": "missing"})

    def _json(self, status: int, value: object) -> None:
        body = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _CredentialSinkHandler(BaseHTTPRequestHandler):
    authorizations: list[str | None] = []

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        self.authorizations.append(self.headers.get("Authorization"))
        body = b'{}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _lap(lap_id: str, lap_time: float, track_temp: float) -> dict[str, object]:
    return {
        "id": lap_id,
        "car": {"id": 10},
        "track": {"id": 20},
        "season": {"id": 263},
        "eventType": 1,
        "sessionType": 3,
        "lapTime": lap_time,
        "clean": True,
        "missing": False,
        "incomplete": False,
        "discontinuity": False,
        "canViewTelemetry": True,
        "trackTemp": track_temp,
        "airTemp": 24.0,
        "trackUsage": 60,
        "trackWetness": 0,
        "fuelLevel": 35.0,
        "driverRating": 2500,
        "weightPenalty": 0.0,
        "powerAdjust": 0.0,
        "tireCompound": 0,
    }


class Garage61ClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _Garage61Handler.requests = []
        _Garage61Handler.redirect_me_to = None
        _CredentialSinkHandler.authorizations = []
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Garage61Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}/api/v1"
        cls.sink_server = ThreadingHTTPServer(("127.0.0.1", 0), _CredentialSinkHandler)
        cls.sink_thread = threading.Thread(
            target=cls.sink_server.serve_forever, daemon=True
        )
        cls.sink_thread.start()
        cls.sink_url = f"http://127.0.0.1:{cls.sink_server.server_port}/capture"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        cls.sink_server.shutdown()
        cls.sink_server.server_close()
        cls.sink_thread.join(timeout=2)

    def setUp(self) -> None:
        _Garage61Handler.requests.clear()
        _Garage61Handler.redirect_me_to = None
        _CredentialSinkHandler.authorizations.clear()
        self.client = garage61_client.Garage61Client(
            "test-pat", base_url=self.base_url, timeout=2
        )

    def test_health_check_reports_global_restriction(self) -> None:
        report = self.client.health_check()
        self.assertTrue(report["ok"])
        self.assertTrue(report["capabilities"]["personal_and_team_laps"]["available"])
        self.assertFalse(report["capabilities"]["global_visible_laps"]["available"])
        self.assertIn("requires_garage61_approval", report["capabilities"]["global_visible_laps"]["status"])

    def test_content_catalog_maps_external_ids(self) -> None:
        catalog = self.client.content_catalog()
        self.assertEqual(catalog.cars_by_platform_id["99"]["name"], "Cup")
        self.assertEqual(catalog.tracks_by_platform_id["88"][0]["variant"], "Oval")
        self.assertEqual(catalog.seasons_by_id[263]["platform"], "iracing")

    def test_comparable_search_queries_open_and_fixed_separately(self) -> None:
        target = _lap("mine", 30.0, 32.0)
        ranked = self.client.find_comparable_laps(target, top_n=2)
        self.assertEqual({item.setup_type for item in ranked}, {"open", "fixed"})
        lap_queries = [query for path, query in _Garage61Handler.requests if path == "/api/v1/laps"]
        self.assertEqual({query["sessionSetupTypes"][0] for query in lap_queries}, {"1", "2"})
        self.assertTrue(all(query["tracks"] == ["20"] for query in lap_queries))
        self.assertTrue(all(query["seeTelemetry"] == ["true"] for query in lap_queries))
        self.assertTrue(all("same season" in item.reasons for item in ranked))

    def test_missing_target_session_metadata_does_not_create_constant_penalties(self) -> None:
        target = _lap("mine", 30.0, 32.0)
        target.pop("sessionType")
        target.pop("eventType")
        ranked = garage61_client.rank_comparable_laps(
            [_lap("candidate", 29.7, 32.0)],
            target,
            top_n=1,
        )
        self.assertEqual(len(ranked), 1)
        self.assertNotIn("different session type", ranked[0].reasons)
        self.assertNotIn("different event type", ranked[0].reasons)
        self.assertGreaterEqual(ranked[0].score, 95.0)

    def test_csv_preserves_unknown_headers_and_original_download(self) -> None:
        telemetry = self.client.get_lap_csv("lap-2")
        self.assertEqual(
            telemetry.headers,
            ("Time", "LapDistPct", "Speed", "FutureUnknown", "PositionType"),
        )
        self.assertEqual(telemetry.as_dicts()[0]["FutureUnknown"], "kept")
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "lap.csv"
            result = self.client.download_lap_csv("lap-2", destination)
            self.assertEqual(result, destination.resolve())
            self.assertIn(b"FutureUnknown", destination.read_bytes())
            with self.assertRaises(FileExistsError):
                self.client.download_lap_csv("lap-2", destination)

    def test_find_laps_requires_track(self) -> None:
        with self.assertRaisesRegex(ValueError, "tracks"):
            self.client.find_laps({"cars": [10]})

    def test_nonlocal_http_base_url_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            garage61_client.Garage61Client(
                "test-pat", base_url="http://garage61.example/api/v1"
            )

    def test_nonofficial_https_base_url_is_rejected(self) -> None:
        for base_url in (
            "https://garage61.example/api/v1",
            "https://garage61.net.example/api/v1",
            "https://garage61.net:444/api/v1",
        ):
            with self.subTest(base_url=base_url), self.assertRaisesRegex(
                ValueError, "garage61.net"
            ):
                garage61_client.Garage61Client("test-pat", base_url=base_url)

    def test_official_and_loopback_origins_are_accepted(self) -> None:
        for base_url in (
            "https://garage61.net/api/v1",
            "https://garage61.net:443/api/v1",
            "http://localhost:1234/api/v1",
            "https://127.0.0.1:1234/api/v1",
            "http://[::1]:1234/api/v1",
        ):
            with self.subTest(base_url=base_url):
                garage61_client.Garage61Client("test-pat", base_url=base_url)

    def test_same_origin_redirect_preserves_authenticated_request(self) -> None:
        _Garage61Handler.redirect_me_to = f"{self.base_url}/redirected-me"
        self.assertEqual(self.client.me()["id"], "user-1")
        self.assertEqual(
            [path for path, _ in _Garage61Handler.requests],
            ["/api/v1/me", "/api/v1/redirected-me"],
        )

    def test_cross_origin_redirect_is_blocked_before_token_forwarding(self) -> None:
        _Garage61Handler.redirect_me_to = self.sink_url
        with self.assertRaisesRegex(
            garage61_client.Garage61ResponseError, "cross-origin redirect"
        ):
            self.client.me()
        self.assertEqual(_CredentialSinkHandler.authorizations, [])


class SecureStoreTests(unittest.TestCase):
    def test_portable_settings_key_is_never_used_as_a_credential(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "settings.json").write_text(
                json.dumps({"garage61ApiKey": "portable-test-secret"}),
                encoding="utf-8",
            )
            with (
                mock.patch.dict(os.environ, {"IRACING_COACH_HOME": str(home)}),
                mock.patch.object(secure_store, "_is_windows", return_value=False),
                mock.patch.object(secure_store, "DEFAULT_CREDENTIAL_PATH", home / "missing.dpapi"),
            ):
                self.assertFalse(secure_store.credential_exists())
                with self.assertRaises(secure_store.SecureStoreError):
                    secure_store.load_token()
            self.assertNotIn("portable-test-secret", os.environ.values())

    def test_store_uses_stdin_not_arguments(self) -> None:
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with (
            mock.patch.object(secure_store, "_is_windows", return_value=True),
            mock.patch.object(secure_store, "_powershell_executable", return_value="powershell.exe"),
            mock.patch.object(secure_store, "_configuration_script", return_value=Path("configure.ps1")),
            mock.patch.object(secure_store.subprocess, "run", return_value=completed) as run,
            tempfile.TemporaryDirectory() as directory,
        ):
            token = "super-secret-pat"
            secure_store.store_token(token, Path(directory) / "credential")
        arguments = run.call_args.args[0]
        self.assertNotIn(token, " ".join(str(value) for value in arguments))
        self.assertEqual(run.call_args.kwargs["input"], token + "\n")

    def test_load_does_not_place_secret_in_arguments(self) -> None:
        completed = subprocess.CompletedProcess([], 0, stdout="loaded-secret", stderr="")
        with tempfile.TemporaryDirectory() as directory:
            credential = Path(directory) / "credential"
            credential.write_text("encrypted", encoding="utf-8")
            with (
                mock.patch.object(secure_store, "_is_windows", return_value=True),
                mock.patch.object(secure_store, "_powershell_executable", return_value="powershell.exe"),
                mock.patch.object(secure_store, "_configuration_script", return_value=Path("configure.ps1")),
                mock.patch.object(secure_store.subprocess, "run", return_value=completed) as run,
            ):
                self.assertEqual(secure_store.load_token(credential), "loaded-secret")
        self.assertNotIn("loaded-secret", " ".join(str(value) for value in run.call_args.args[0]))


if __name__ == "__main__":
    unittest.main()
