import os
import urllib.parse
import unittest

import model_radar


@unittest.skipUnless(
    os.environ.get("MODEL_RADAR_LIVE_API") == "1",
    "Live-API-Test nur mit MODEL_RADAR_LIVE_API=1",
)
class LiveApiSmokeTests(unittest.TestCase):
    def test_hugging_face_liefert_den_erwarteten_modellvertrag(self):
        config = model_radar.validate_config({
            "schema_version": model_radar.CONFIG_SCHEMA_VERSION,
            "api_url": "https://huggingface.co/api/models",
            "publishers": {"Qwen": ["Qwen"]},
            "families": {"Qwen": ["qwen"]},
            "parameter_min_b": 2,
            "parameter_max_b": 24,
            "preferred_pipeline_tags": ["text-generation"],
            "filter_mode": "conservative",
            "absent_retention_days": 365,
            "state_file": "/tmp/model-radar-live-state.json",
            "report_file": "/tmp/model-radar-live-report.txt",
            "request_retries": 2,
            "request_timeout_seconds": 20,
        })
        query = urllib.parse.urlencode({
            "author": "Qwen", "search": "Qwen", "limit": 1,
            "full": "true", "config": "true",
        })
        payload, _next_url = model_radar.fetch_json_page(
            f"{config['api_url']}?{query}", config,
        )
        self.assertTrue(payload, "Die API lieferte kein Modell.")
        self.assertIsInstance(payload[0], dict)
        repo_id = payload[0].get("id") or payload[0].get("modelId")
        self.assertIsInstance(repo_id, str)
        self.assertTrue(repo_id.startswith("Qwen/"))


if __name__ == "__main__":
    unittest.main()
