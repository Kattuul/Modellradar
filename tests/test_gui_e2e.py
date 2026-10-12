# SPDX-License-Identifier: GPL-3.0-or-later
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402
except (ImportError, ValueError) as exc:
    raise unittest.SkipTest(f"GTK nicht installiert: {exc}") from exc

import model_radar
from model_radar_gui import RadarWindow
from radar_gui_components import CommandResult, TimerStatus


ROOT = Path(__file__).resolve().parents[1]


class FakeRunner:
    def __init__(self):
        self.callback = None
        self.command = None
        self.cancelled = False

    def run(self, command, callback):
        self.command = list(command)
        self.callback = callback

    def cancel(self):
        self.cancelled = True
        return True


class GtkWorkflowTests(unittest.TestCase):
    """Bedient echte GTK-Widgets; release_check.py startet dies unter Xvfb."""

    @classmethod
    def setUpClass(cls):
        if not Gtk.init_check() or Gdk.Display.get_default() is None:
            raise unittest.SkipTest("GTK-End-to-End-Test benötigt ein Display (z. B. xvfb-run).")
        cls.app = Adw.Application(
            application_id="de.local.Qwen35ModelRadar.Tests",
            flags=Gio.ApplicationFlags.NON_UNIQUE,
        )
        cls.app.register(None)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        base = Path(self.directory.name)
        self.report_path = base / "report.txt"
        self.report_path.write_text("AUTOMATISCH GELADENER BERICHT\n", encoding="utf-8")
        config = model_radar.validate_config(model_radar.load_json(ROOT / "config.json", "Konfiguration"))
        config = dict(
            config,
            state_file=str(base / "state.json"),
            report_file=str(self.report_path),
        )
        self.config_path = base / "config.json"
        self.config_path.write_text(json.dumps(config), encoding="utf-8")
        with mock.patch("radar_gui_components.TimerController.refresh"):
            self.window = RadarWindow(self.app, self.config_path)
        self.runner = FakeRunner()
        self.window.command_runner = self.runner

    def tearDown(self):
        self.window.close()
        self.directory.cleanup()

    @staticmethod
    def output_text(window):
        buffer = window.actions_page.output_view.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)

    def test_fortschritt_und_automatisches_berichtsladen(self):
        with mock.patch("radar_gui_logic.validate_data_path", side_effect=lambda value, _label: value):
            self.window.actions_page.run_button.emit("clicked")
            self.assertTrue(self.window._busy)
            self.assertTrue(self.window.actions_page.progress.get_visible())
            self.assertNotEqual(self.window.actions_page._pulse_source, 0)
            self.assertTrue(self.window.actions_page.cancel_button.get_sensitive())
            self.runner.callback(CommandResult(0, "Prüfung abgeschlossen.\n"))
        self.assertFalse(self.window._busy)
        self.assertFalse(self.window.actions_page.progress.get_visible())
        self.assertEqual(self.output_text(self.window), "AUTOMATISCH GELADENER BERICHT\n")

    def test_abgebrochener_lauf_lädt_keinen_alten_bericht(self):
        with mock.patch("radar_gui_logic.validate_data_path", side_effect=lambda value, _label: value):
            self.window.actions_page.run_button.emit("clicked")
        self.window.actions_page.cancel_button.emit("clicked")
        self.assertTrue(self.runner.cancelled)
        self.assertIn("Abbruch angefordert", self.window.actions_page.status_label.get_text())
        self.runner.callback(CommandResult(-15, "PROZESS ABGEBROCHEN\n", cancelled=True))
        self.assertEqual(self.output_text(self.window), "PROZESS ABGEBROCHEN\n")
        self.assertEqual(self.window.actions_page.status_label.get_text(), "Abgebrochen.")

    def test_ohne_systemd_bleiben_pruefung_und_bericht_bedienbar(self):
        page = self.window.actions_page
        page.set_timer_status(TimerStatus("", "", error="Kein Benutzerdienst"))
        for busy in (True, False):
            page.set_busy(busy)
            self.assertFalse(page.enable_button.get_sensitive())
            self.assertFalse(page.disable_button.get_sensitive())
        self.assertTrue(page.run_button.get_sensitive())
        self.assertTrue(page.report_button.get_sensitive())
        self.assertIn("Manuelle Prüfungen", page.timer_label.get_text())

    def test_diagnose_zeigt_schweregrad_und_fuehrt_aktionen_aus(self):
        self.window.actions_page.diagnostic_button.emit("clicked")
        self.assertEqual(self.runner.command[-1], "--json")
        self.runner.callback(CommandResult(0, json.dumps({
            "schema_version": 1,
            "service": {"LoadState": "loaded", "ActiveState": "inactive", "Result": "success"},
            "timer": {"LoadState": "loaded", "ActiveState": "inactive", "UnitFileState": "disabled"},
            "findings": [{
                "severity": "warning",
                "title": "Timer ist nicht aktiv",
                "detail": "inactive",
                "recommendation": "Timer aktivieren.",
                "actions": ["enable_timer", "open_settings"],
            }],
            "journal": "Testjournal",
        })))
        page = self.window.actions_page
        self.assertTrue(page.diagnostic_panel.get_visible())
        self.assertIn("1 Warnung", page.diagnostic_title.get_text())
        self.assertTrue(page.diagnostic_action_buttons["enable_timer"].get_visible())
        self.assertIn("Nächster Schritt: Timer aktivieren.", self.output_text(self.window))
        self.assertIn("Timer: loaded / inactive / disabled", self.output_text(self.window))

        self.window.notebook.set_current_page(1)
        page.diagnostic_action_buttons["open_settings"].emit("clicked")
        self.assertEqual(self.window.notebook.get_current_page(), 0)

        page.diagnostic_action_buttons["enable_timer"].emit("clicked")
        self.assertEqual(
            self.runner.command,
            ["systemctl", "--user", "enable", "--now", "qwen35-model-radar.timer"],
        )

    def test_diagnose_ohne_fehler_bietet_keine_reparaturaktion(self):
        self.window.actions_page.diagnostic_button.emit("clicked")
        self.runner.callback(CommandResult(0, json.dumps({
            "schema_version": 1,
            "service": {"Result": "success"},
            "timer": {"ActiveState": "active"},
            "findings": [],
            "journal": "",
        })))
        page = self.window.actions_page
        self.assertIn("keine Fehler", page.diagnostic_title.get_text())
        self.assertFalse(any(
            button.get_visible() for button in page.diagnostic_action_buttons.values()
        ))

    def test_ungueltige_diagnose_zeigt_fehler_ohne_alte_aktionen(self):
        self.window.actions_page.diagnostic_button.emit("clicked")
        self.runner.callback(CommandResult(0, "[]"))
        self.assertIn("kann nicht ausgewertet werden", self.window.actions_page.status_label.get_text())
        self.assertFalse(self.window.actions_page.diagnostic_panel.get_visible())

    def test_fensterstart_migriert_konfiguration_und_zustand_mit_sicherung(self):
        self.window.close()
        base = Path(self.directory.name)
        config = json.loads(self.config_path.read_text(encoding="utf-8"))
        config["schema_version"] = 1
        config.pop("absent_retention_days")
        original_config = json.dumps(config).encode("utf-8")
        self.config_path.write_bytes(original_config)
        state_path = base / "state.json"
        original_state = b'{"schema_version":1,"repositories":{}}\n'
        state_path.write_bytes(original_state)

        with mock.patch("radar_gui_components.TimerController.refresh"):
            self.window = RadarWindow(self.app, self.config_path)
        self.assertEqual(json.loads(self.config_path.read_text())["schema_version"], 2)
        self.assertEqual(json.loads(state_path.read_text())["schema_version"], 2)
        self.assertEqual(
            self.config_path.with_name("config.json.schema-v1.bak").read_bytes(),
            original_config,
        )
        self.assertEqual(
            state_path.with_name("state.json.schema-v1.bak").read_bytes(),
            original_state,
        )


if __name__ == "__main__":
    unittest.main()
