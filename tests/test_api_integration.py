import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import model_radar as radar


ROOT = Path(__file__).resolve().parents[1]
CONFIG = radar.validate_config(radar.load_json(ROOT / "config.json", "Konfiguration"))


class JsonResponse(io.BytesIO):
    def __init__(self, payload=None, *, raw=None, link=None):
        body = raw if raw is not None else json.dumps(payload).encode("utf-8")
        super().__init__(body)
        self.headers = {"Link": link} if link else {}

    def __enter__(self):
        return self

    def __exit__(self, _type, _value, _traceback):
        self.close()


class ApiIntegrationTests(unittest.TestCase):
    def test_mehrseitige_api_antwort_wird_vollstaendig_zusammengefuehrt(self):
        config = dict(CONFIG, publishers={"Qwen": ["Qwen"]})
        responses = (
            JsonResponse(
                [{"id": "Qwen/Modell-A"}],
                link='<https://huggingface.co/api/models?page=2>; rel="next"',
            ),
            JsonResponse([{"id": "Qwen/Modell-B"}]),
        )
        with mock.patch("urllib.request.urlopen", side_effect=responses) as opener:
            models = radar.fetch_models(config)
        self.assertEqual([model["id"] for model in models], ["Qwen/Modell-A", "Qwen/Modell-B"])
        self.assertEqual(opener.call_count, 2)

    def test_timeout_wird_wiederholt_und_danach_erfolgreich(self):
        config = dict(CONFIG, request_retries=2, retry_backoff_seconds=0)
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=(TimeoutError("zu langsam"), JsonResponse([{"id": "Qwen/Modell"}])),
        ) as opener, mock.patch("time.sleep") as sleeper:
            payload, next_url = radar.fetch_json_page(CONFIG["api_url"], config)
        self.assertEqual(payload, [{"id": "Qwen/Modell"}])
        self.assertIsNone(next_url)
        self.assertEqual(opener.call_count, 2)
        sleeper.assert_called_once_with(0)

    def test_retry_after_wird_beachtet_und_abfrage_erholt_sich(self):
        config = dict(CONFIG, request_retries=2, retry_backoff_seconds=0)
        error = urllib.error.HTTPError(
            CONFIG["api_url"], 429, "Too Many Requests", {"Retry-After": "7"}, None
        )
        self.addCleanup(error.close)
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=(error, JsonResponse([])),
        ) as opener, mock.patch("time.sleep") as sleeper:
            payload, next_url = radar.fetch_json_page(CONFIG["api_url"], config)
        self.assertEqual(payload, [])
        self.assertIsNone(next_url)
        self.assertEqual(opener.call_count, 2)
        sleeper.assert_called_once_with(7.0)

    def test_pagination_zyklus_wird_am_seitenlimit_abgebrochen(self):
        config = dict(
            CONFIG,
            publishers={"Qwen": ["Qwen"]},
            max_pages_per_query=2,
        )
        repeated_link = '<https://huggingface.co/api/models?page=2>; rel="next"'
        responses = (
            JsonResponse([{"id": "Qwen/Modell-A"}], link=repeated_link),
            JsonResponse([{"id": "Qwen/Modell-A"}], link=repeated_link),
        )
        with mock.patch("urllib.request.urlopen", side_effect=responses):
            with self.assertRaisesRegex(radar.RadarError, "unvollständiger Zustand"):
                radar.fetch_models(config)

    def test_ungueltiges_json_wird_kontrolliert_gemeldet(self):
        config = dict(CONFIG, request_retries=1)
        with mock.patch("urllib.request.urlopen", return_value=JsonResponse(raw=b"{")):
            with self.assertRaisesRegex(radar.RadarError, "API nicht erreichbar"):
                radar.fetch_json_page(CONFIG["api_url"], config)

    def test_nicht_array_antwort_wird_abgelehnt(self):
        config = dict(CONFIG, request_retries=1)
        with mock.patch("urllib.request.urlopen", return_value=JsonResponse({"error": "kaputt"})):
            with self.assertRaisesRegex(radar.RadarError, "kein JSON-Array"):
                radar.fetch_json_page(CONFIG["api_url"], config)

    def test_anfrage_enthaelt_keinen_authorization_header(self):
        captured = []

        def answer(request, **_kwargs):
            captured.append(request)
            return JsonResponse([])

        with mock.patch("urllib.request.urlopen", side_effect=answer):
            radar.fetch_json_page(CONFIG["api_url"], dict(CONFIG, request_retries=1))
        headers = {key.lower(): value for key, value in captured[0].header_items()}
        self.assertNotIn("authorization", headers)
        self.assertEqual(headers["accept"], "application/json")

    def test_teilfehler_speichert_keinen_unvollstaendigen_lauf(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = dict(
                CONFIG,
                publishers={"Qwen": ["Qwen", "Gemma"]},
                state_file=str(base / "state.json"),
                report_file=str(base / "report.txt"),
            )
            config_path = base / "config.json"
            radar.atomic_write(config_path, json.dumps(config))
            answers = (
                ([{"id": "Qwen/Qwen4-4B"}], None),
                radar.RadarError("zweite Teilabfrage fehlgeschlagen"),
            )
            with mock.patch("model_radar.fetch_json_page", side_effect=answers):
                with mock.patch("sys.stderr", new=io.StringIO()) as errors:
                    returncode = radar.main(["--config", str(config_path), "--force"])
            self.assertEqual(returncode, 2)
            self.assertIn("zweite Teilabfrage fehlgeschlagen", errors.getvalue())
            self.assertFalse((base / "state.json").exists())
            self.assertFalse((base / "report.txt").exists())


if __name__ == "__main__":
    unittest.main()
