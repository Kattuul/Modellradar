import json
import sys
import tempfile
import unittest
from pathlib import Path

import model_radar
import radar_gui_logic as logic


ROOT = Path(__file__).resolve().parents[1]


class RadarGuiLogicTests(unittest.TestCase):
    def test_mapping_roundtrip(self):
        original = {"Qwen": ["Qwen"], "ggml-org": ["Qwen", "Gemma", "gpt-oss"]}
        self.assertEqual(logic.text_to_mapping(logic.mapping_to_text(original), "Test"), original)

    def test_mapping_fehler_enthaelt_zeile(self):
        with self.assertRaisesRegex(model_radar.RadarError, "Zeile 2"):
            logic.text_to_mapping("Qwen: Qwen\nungültig", "Herausgeber")

    def test_leere_optionale_liste(self):
        self.assertEqual(logic.text_to_list("", "Ausschlüsse", allow_empty=True), [])

    def test_radarbefehl_verwendet_cli_aus_demselben_verzeichnis(self):
        config_path = Path("/tmp/model-radar-test-config.json")
        command = logic.radar_command(config_path, "--force")
        expected_script = Path(logic.__file__).resolve().with_name("model_radar.py")
        self.assertEqual(command, [
            sys.executable, str(expected_script), "--config", str(config_path), "--force",
        ])

    def test_config_wird_atomar_gespeichert(self):
        config = model_radar.load_json(ROOT / "config.json", "Konfiguration")
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "config.json"
            logic.save_config(target, config)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), config)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_datenpfad_bleibt_im_erlaubten_verzeichnis(self):
        good = "~/.local/share/qwen35-model-radar/custom/state.json"
        self.assertEqual(logic.validate_data_path(good, "Zustand"), good)
        with self.assertRaises(model_radar.RadarError):
            logic.validate_data_path("~/state.json", "Zustand")

    def test_pruefstatus_zeigt_letzten_erfolg_und_echte_faelligkeit(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            model_radar.atomic_write(state_path, json.dumps({
                "schema_version": 1,
                "last_successful_check": "2026-09-10T12:00:00+00:00",
                "repositories": {},
            }))
            config = {"state_file": str(state_path), "check_interval_days": 7}
            before_due = logic.check_status_text(
                config,
                model_radar.datetime(2026, 9, 15, 12, tzinfo=model_radar.timezone.utc),
            )
            self.assertIn("10.09.2026 12:00 UTC", before_due)
            self.assertIn("17.09.2026 12:00 UTC", before_due)
            due = logic.check_status_text(
                config,
                model_radar.datetime(2026, 9, 17, 12, tzinfo=model_radar.timezone.utc),
            )
            self.assertIn("jetzt fällig", due)

    def test_pruefstatus_ohne_zustand_ist_sofort_faellig(self):
        with tempfile.TemporaryDirectory() as directory:
            config = {"state_file": str(Path(directory) / "fehlt.json"), "check_interval_days": 7}
            self.assertIn("noch keine", logic.check_status_text(config))
            self.assertIn("jetzt fällig", logic.check_status_text(config))


if __name__ == "__main__":
    unittest.main()
