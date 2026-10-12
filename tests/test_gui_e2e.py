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
    from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402
except (ImportError, ValueError) as exc:
    raise unittest.SkipTest(f"GTK nicht installiert: {exc}") from exc

import model_radar
import radar_gui_logic as logic
from model_radar_gui import ONBOARDING_VERSION, RadarWindow
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
        # GUI-Präferenzen (Fenstergröße, Einführungsstand) nie im echten Home ablegen.
        state_home = mock.patch.dict("os.environ", {"XDG_STATE_HOME": str(base / "state")})
        state_home.start()
        self.addCleanup(state_home.stop)
        graphics = mock.patch("radar_platform.detect_graphics", return_value={"adapters": [], "limitations": []})
        graphics.start()
        self.addCleanup(graphics.stop)
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
        self.assertFalse(self.window.actions_page.cancel_button.get_visible())
        self.assertEqual(self.output_text(self.window), "AUTOMATISCH GELADENER BERICHT\n")
        self.assertTrue(self.window.actions_page.summary_label.get_visible())
        self.assertTrue(self.window.actions_page.copy_report_button.get_sensitive())

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
        self.window.apply_timer_status(TimerStatus("", "", error="Kein Benutzerdienst"))
        for busy in (True, False):
            page.set_busy(busy)
            self.assertFalse(page.timer_switch.get_sensitive())
        self.assertTrue(page.run_button.get_sensitive())
        self.assertIn("Manuelle Prüfungen", page.timer_row.get_subtitle())
        self.assertTrue(self.window.banner.get_revealed())
        self.assertIn("Kein Benutzerdienst", self.window.banner.get_title())

    def test_timerschalter_folgt_dem_gemeldeten_zustand(self):
        page = self.window.actions_page
        self.window.apply_timer_status(TimerStatus("disabled", "inactive"))
        self.assertFalse(self.window.banner.get_revealed())
        self.assertFalse(page.timer_switch.get_active())
        self.assertIn("Ausgeschaltet", page.timer_row.get_subtitle())

        with mock.patch.object(self.window, "_confirm_timer_enable") as confirm:
            page.timer_switch.set_active(True)
        confirm.assert_called_once()
        self.assertFalse(page.timer_switch.get_state())

        self.window.apply_timer_status(TimerStatus("enabled", "active", "Mo 00:38"))
        self.assertTrue(page.timer_switch.get_active())
        self.assertTrue(page.timer_switch.get_state())
        self.assertIn("nächster Start: Mo 00:38", page.timer_row.get_subtitle())

        with mock.patch.object(self.window, "refresh_timer_status"):
            page.timer_switch.set_active(False)
        self.assertEqual(
            self.runner.command,
            ["systemctl", "--user", "disable", "--now", "qwen35-model-radar.timer"],
        )

    def test_diagnose_zeigt_schweregrad_und_fuehrt_aktionen_aus(self):
        self.window.lookup_action("diagnostics").activate(None)
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

        page.diagnostic_action_buttons["open_settings"].emit("clicked")
        self.assertTrue(self.window.settings_window.get_visible())

        with mock.patch.object(self.window, "_confirm_timer_enable") as confirm:
            page.diagnostic_action_buttons["enable_timer"].emit("clicked")
        confirm.assert_called_once()
        self.window._change_timer(True)
        self.assertEqual(
            self.runner.command,
            ["systemctl", "--user", "enable", "--now", "qwen35-model-radar.timer"],
        )
        self.assertTrue(page.copy_diagnostic_button.get_sensitive())

    def test_leerzustand_und_fehlerdetails_sind_verstaendlich(self):
        page = self.window.actions_page
        self.window.run_command(["unbekannt"], "Test")
        self.runner.callback(CommandResult(2, "Konkretes technisches Detail"))
        self.assertTrue(page.error_details.get_visible())
        self.assertIn("Exitcode 2", page.status_label.get_text())
        self.assertIn("Konkretes", page.error_label.get_text())

    def test_timeraktivierung_braucht_bestaetigung(self):
        dialog = mock.Mock()
        with mock.patch.object(self.window, "_confirmation_dialog", return_value=dialog):
            self.window.set_timer(True)
        self.assertEqual(self.runner.command, None)
        self.assertIn(mock.call("enable", "Einschalten"), dialog.add_response.call_args_list)
        response_callback = dialog.connect.call_args.args[1]
        with mock.patch.object(self.window, "refresh_timer_status") as refresh:
            response_callback(dialog, "cancel")
        refresh.assert_called_once()
        self.assertEqual(self.runner.command, None)
        response_callback(dialog, "enable")
        self.assertEqual(
            self.runner.command,
            ["systemctl", "--user", "enable", "--now", "qwen35-model-radar.timer"],
        )

    def test_diagnose_ohne_fehler_bietet_keine_reparaturaktion(self):
        self.window.lookup_action("diagnostics").activate(None)
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
        self.window.lookup_action("diagnostics").activate(None)
        self.runner.callback(CommandResult(0, "[]"))
        self.assertIn("kann nicht ausgewertet werden", self.window.actions_page.status_label.get_text())
        self.assertFalse(self.window.actions_page.diagnostic_panel.get_visible())

    def test_hauptansicht_priorisiert_aktionen_und_menu(self):
        page = self.window.actions_page
        self.assertFalse(page.cancel_button.get_visible())
        for action in ("preferences", "onboarding", "dry-run", "diagnostics", "shortcuts", "about"):
            self.assertIsNotNone(self.window.lookup_action(action))
        self.assertIsNone(self.window.lookup_action("reload-report"))

        self.window.lookup_action("preferences").activate(None)
        self.assertTrue(self.window.settings_window.get_visible())

    def test_bericht_wird_beim_start_still_geladen(self):
        self.assertEqual(self.output_text(self.window), "AUTOMATISCH GELADENER BERICHT\n")
        self.assertFalse(self.window.actions_page.error_details.get_visible())

    def test_fehlender_bericht_beim_start_ist_kein_fehler(self):
        self.window.close()
        self.report_path.unlink()
        with mock.patch("radar_gui_components.TimerController.refresh"):
            self.window = RadarWindow(self.app, self.config_path)
        self.assertEqual(self.window.actions_page.output_stack.get_visible_child_name(), "empty")
        self.assertFalse(self.window.actions_page.error_details.get_visible())

    def test_einstellungen_sind_gruppiert_und_speichern_mit_toast(self):
        page = self.window.settings_page
        titles = []
        stack = [page]
        while stack:
            widget = stack.pop()
            if isinstance(widget, Adw.PreferencesGroup):
                titles.append(widget.get_title())
            child = widget.get_last_child()
            while child is not None:
                stack.append(child)
                child = child.get_prev_sibling()
        self.assertEqual(titles, ["Modellquellen", "Automatische Prüfungen", "Netzwerk", "Erweitert"])

        page.interval_spin.set_value(3)
        with mock.patch.object(self.window, "show_toast") as toast, \
                mock.patch("radar_gui_logic.validate_data_path", side_effect=lambda value, _label: value):
            self.assertTrue(self.window.on_save())
        toast.assert_called_once_with("Einstellungen gespeichert")
        self.assertEqual(json.loads(self.config_path.read_text())["check_interval_days"], 3)

    def test_ungueltige_einstellungen_bleiben_offen(self):
        self.window.settings_page.min_spin.set_value(50)
        self.window.settings_page.max_spin.set_value(1)
        self.assertFalse(self.window.on_save())
        self.assertTrue(self.window.settings_window.get_visible())

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


    @staticmethod
    def spin_until(condition, seconds=5.0):
        """Verarbeitet GTK-Ereignisse, bis die Bedingung gilt; robust auch unter Last."""
        context = GLib.MainContext.default()
        deadline = GLib.get_monotonic_time() + int(seconds * 1_000_000)
        while not condition() and GLib.get_monotonic_time() < deadline:
            context.iteration(False)

    def test_einfuehrung_erscheint_beim_ersten_start_und_ueberspringen_merkt_sich_das(self):
        self.window.present()
        self.window.maybe_show_onboarding()
        view = self.window.onboarding_view
        self.assertIsNotNone(self.window._onboarding_dialog)
        self.assertEqual(view.step_label.get_text(), "Schritt 1 von 3: Willkommen")
        self.assertFalse(view.back_button.get_visible())

        view.skip_button.emit("clicked")
        self.assertIsNone(self.window._onboarding_dialog)
        self.assertIsNone(self.runner.command)
        self.assertEqual(logic.load_gui_preferences().get("onboarding_version"), ONBOARDING_VERSION)

        # Wer die Einführung kennt, sieht sie beim nächsten Start nicht erneut.
        self.window.maybe_show_onboarding()
        self.assertIsNone(self.window._onboarding_dialog)

    def test_einfuehrung_fuehrt_zur_ersten_pruefung(self):
        self.window.present()
        self.window.lookup_action("onboarding").activate(None)
        view = self.window.onboarding_view
        view.go_to(2)
        self.assertEqual(view.step_label.get_text(), "Schritt 3 von 3: Los geht’s")
        self.assertFalse(view.next_button.get_visible())
        view.start_button.emit("clicked")
        self.assertIn("--force", self.runner.command)
        self.assertIsNone(self.window._onboarding_dialog)

    def test_einfuehrung_zeigt_echten_timerzustand_und_hardware(self):
        self.window.present()
        self.window.show_onboarding()
        view = self.window.onboarding_view
        self.window.apply_timer_status(TimerStatus("enabled", "active"))
        self.assertEqual(view.timer_row.get_title(), "Automatische Prüfungen sind eingeschaltet")
        self.window.apply_timer_status(TimerStatus("", "", error="Kein Benutzerdienst"))
        self.assertIn("nicht verfügbar", view.timer_row.get_title())
        self.spin_until(lambda: view.hardware_row.get_title() != "Grafikkarte wird ermittelt …")
        self.assertEqual(view.hardware_row.get_title(), "Keine Grafikkarte erkannt")

    def test_erste_pruefung_zeigt_hinweis_auf_bericht(self):
        self.window.close()
        self.report_path.unlink()
        with mock.patch("radar_gui_components.TimerController.refresh"):
            self.window = RadarWindow(self.app, self.config_path)
        self.window.command_runner = self.runner
        self.window.run_radar("force")
        self.report_path.write_text("ERSTER BERICHT\n", encoding="utf-8")
        with mock.patch.object(self.window, "_announce_first_report") as announce, \
                mock.patch.object(self.window, "show_toast") as toast:
            self.runner.callback(CommandResult(0, "ok"))
        announce.assert_called_once()
        toast.assert_not_called()
        self.assertEqual(self.output_text(self.window), "ERSTER BERICHT\n")


if __name__ == "__main__":
    unittest.main()
