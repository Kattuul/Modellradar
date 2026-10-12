# SPDX-License-Identifier: GPL-3.0-or-later
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

    def test_hardware_eignung_verwendet_denselben_lokalen_cli(self):
        command = logic.recommendations_command(Path("/tmp/config.json"))
        self.assertEqual(Path(command[1]).name, "model_radar.py")
        self.assertEqual(command[-1], "--recommendations")

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

    def test_berichtskurzueberblick_zaehlt_lebenszyklusereignisse(self):
        report = """Prüfzeitpunkt (UTC): 2026-10-12T08:30:00+00:00
Passende Repositories: 7
Erkannte Lebenszyklusänderungen: 4
[NEU] Qwen/Neu
[AKTUALISIERT] Qwen/Aktuell
[ENTFERNT] Qwen/Alt
[WIEDER ERSCHIENEN] Qwen/Zurueck
"""
        self.assertEqual(logic.report_summary(report), {
            "checked_at": "2026-10-12T08:30:00+00:00", "repositories": "7", "changes": "4",
            "new": "1", "updated": "1", "removed": "1", "returned": "1",
        })

    def test_gui_praeferenzen_sind_getrennt_und_fehlertolerant(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state" / "gui-preferences.json"
            logic.save_gui_preferences({"page": 1, "width": 1000, "height": 700, "ignored": 9}, path)
            self.assertEqual(logic.load_gui_preferences(path), {"width": 1000, "height": 700})
            path.write_text("kein JSON", encoding="utf-8")
            self.assertEqual(logic.load_gui_preferences(path), {})

    def test_einfuehrungsstand_wird_gespeichert_und_begrenzt(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gui-preferences.json"
            logic.save_gui_preferences({"width": 1000, "onboarding_version": 1}, path)
            self.assertEqual(logic.load_gui_preferences(path), {"width": 1000, "onboarding_version": 1})
            path.write_text('{"onboarding_version": -3}', encoding="utf-8")
            self.assertEqual(logic.load_gui_preferences(path), {})

    def test_grafikzusammenfassung_in_alltagssprache(self):
        title, detail = logic.graphics_summary({"adapters": [], "limitations": ["Flatpak-Sandbox"]})
        self.assertEqual(title, "Keine Grafikkarte erkannt")
        self.assertIn("Hinweis: Flatpak-Sandbox", detail)

        title, detail = logic.graphics_summary({"adapters": [
            {"vendor": "Intel", "kind": "integriert", "vram_bytes": None},
            {"vendor": "NVIDIA", "kind": "dediziert", "vram_bytes": 12 * 1024 ** 3},
        ]})
        self.assertEqual(title, "Grafikkarte mit 12,0 GiB Grafikspeicher erkannt")
        self.assertEqual(detail, "Intel · im Prozessor eingebaut · Grafikspeicher unbekannt\n"
                                 "NVIDIA · eigene Grafikkarte · 12,0 GiB Grafikspeicher")

        title, _detail = logic.graphics_summary({"adapters": [{"vendor": "Intel", "kind": "integriert"}]})
        self.assertEqual(title, "Grafik erkannt, Grafikspeicher unbekannt")


if __name__ == "__main__":
    unittest.main()
