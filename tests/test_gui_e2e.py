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
from radar_gui_components import CommandResult, NavigationHost, TimerStatus


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
        return window.report_page.text()

    def test_fortschritt_und_automatisches_berichtsladen(self):
        with mock.patch("radar_gui_logic.validate_data_path", side_effect=lambda value, _label: value):
            self.window.dashboard.run_button.emit("clicked")
            self.assertTrue(self.window._busy)
            self.assertTrue(self.window.dashboard.progress.get_visible())
            self.assertNotEqual(self.window.dashboard._pulse_source, 0)
            self.assertTrue(self.window.dashboard.cancel_button.get_sensitive())
            self.runner.callback(CommandResult(0, "Prüfung abgeschlossen.\n"))
        self.assertFalse(self.window._busy)
        self.assertFalse(self.window.dashboard.progress.get_visible())
        self.assertFalse(self.window.dashboard.cancel_button.get_visible())
        self.assertEqual(self.output_text(self.window), "AUTOMATISCH GELADENER BERICHT\n")
        self.assertTrue(self.window.dashboard.report_row.get_sensitive())
        self.assertTrue(self.window.report_page.copy_button.get_sensitive())

    def test_abgebrochener_lauf_lädt_keinen_alten_bericht(self):
        with mock.patch("radar_gui_logic.validate_data_path", side_effect=lambda value, _label: value):
            self.window.dashboard.run_button.emit("clicked")
        self.window.dashboard.cancel_button.emit("clicked")
        self.assertTrue(self.runner.cancelled)
        self.assertIn("Abbruch angefordert", self.window.dashboard.status_label.get_text())
        self.runner.callback(CommandResult(-15, "PROZESS ABGEBROCHEN\n", cancelled=True))
        self.assertEqual(self.output_text(self.window), "AUTOMATISCH GELADENER BERICHT\n")
        self.assertEqual(self.window.dashboard.status_label.get_text(), "Abgebrochen.")
        self.assertFalse(self.window.dashboard.error_details.get_visible())

    def test_ohne_systemd_bleiben_pruefung_und_bericht_bedienbar(self):
        page = self.window.dashboard
        self.window.apply_timer_status(TimerStatus("", "", error="Kein Benutzerdienst"))
        for busy in (True, False):
            page.set_busy(busy)
            self.assertFalse(page.timer_switch.get_sensitive())
        self.assertTrue(page.run_button.get_sensitive())
        self.assertIn("Manuelle Prüfungen", page.timer_row.get_subtitle())
        self.assertTrue(self.window.banner.get_revealed())
        self.assertIn("Kein Benutzerdienst", self.window.banner.get_title())

    def test_timerschalter_folgt_dem_gemeldeten_zustand(self):
        page = self.window.dashboard
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
        page = self.window.diagnostics_page
        self.assertEqual(self.window.navigation.visible_tag(), "diagnostics")
        self.assertEqual(page.state(), "result")
        self.assertIn("1 Warnung", page.diagnostic_title.get_text())
        self.assertTrue(page.diagnostic_action_buttons["enable_timer"].get_visible())
        self.assertIn("Nächster Schritt: Timer aktivieren.", page.diagnostic_text)
        self.assertIn("Timer: loaded / inactive / disabled", page.diagnostic_text)

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
        self.assertTrue(page.copy_button.get_sensitive())

    def test_leerzustand_und_fehlerdetails_sind_verstaendlich(self):
        page = self.window.dashboard
        self.window.run_command(["unbekannt"], "Test")
        self.runner.callback(CommandResult(2, "Konkretes technisches Detail"))
        self.assertTrue(page.error_details.get_visible())
        self.assertIn("Exitcode 2", page.status_label.get_text())
        self.assertIn("Konkretes", page.error_label.get_text())
        # Der nächste Schritt führt direkt aus der Fehlermeldung zur Diagnose.
        page.diagnose_button.emit("clicked")
        self.assertEqual(self.window.navigation.visible_tag(), "diagnostics")
        self.assertEqual(self.window.diagnostics_page.state(), "loading")

    def test_timeraktivierung_braucht_bestaetigung(self):
        dialog = mock.Mock()
        with mock.patch.object(self.window, "_confirmation_dialog", return_value=dialog):
            self.window.set_timer(True)
        self.assertEqual(self.runner.command, None)
        self.assertIn(mock.call("enable", "_Einschalten"), dialog.add_response.call_args_list)
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
        page = self.window.diagnostics_page
        self.assertIn("keine Fehler", page.diagnostic_title.get_text())
        self.assertFalse(any(
            button.get_visible() for button in page.diagnostic_action_buttons.values()
        ))

    def test_ungueltige_diagnose_zeigt_fehler_ohne_alte_aktionen(self):
        self.window.lookup_action("diagnostics").activate(None)
        self.runner.callback(CommandResult(0, "[]"))
        self.assertIn("kann nicht ausgewertet werden", self.window.dashboard.status_label.get_text())
        self.assertEqual(self.window.diagnostics_page.state(), "error")

    def test_hauptansicht_priorisiert_aktionen_und_menu(self):
        page = self.window.dashboard
        self.assertFalse(page.cancel_button.get_visible())
        for action in ("preferences", "onboarding", "dry-run", "diagnostics", "shortcuts", "about"):
            self.assertIsNotNone(self.window.lookup_action(action))
        self.assertIsNone(self.window.lookup_action("reload-report"))

        self.window.lookup_action("preferences").activate(None)
        self.assertTrue(self.window.settings_window.get_visible())

    def test_bericht_wird_beim_start_still_geladen(self):
        self.assertEqual(self.output_text(self.window), "AUTOMATISCH GELADENER BERICHT\n")
        self.assertFalse(self.window.dashboard.error_details.get_visible())

    def test_fehlender_bericht_beim_start_ist_kein_fehler(self):
        self.window.close()
        self.report_path.unlink()
        with mock.patch("radar_gui_components.TimerController.refresh"):
            self.window = RadarWindow(self.app, self.config_path)
        self.assertEqual(self.window.report_page.state(), "empty")
        self.assertFalse(self.window.dashboard.report_row.get_sensitive())
        self.assertFalse(self.window.dashboard.error_details.get_visible())

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

    def test_bericht_ist_eigene_unteransicht_mit_kopieraktion(self):
        nav = self.window.navigation
        self.assertEqual(nav.visible_tag(), "dashboard")
        self.window.dashboard.report_row.emit("activated")
        self.assertEqual(nav.visible_tag(), "report")
        self.assertEqual(self.window.report_page.state(), "result")
        self.assertFalse(self.window.report_page.preview_label.get_visible())
        with mock.patch.object(self.window.report_page, "notify") as toast:
            self.window.report_page.copy_button.emit("clicked")
        toast.assert_called_once_with("Bericht in die Zwischenablage kopiert.")
        nav.pop()
        self.assertEqual(nav.visible_tag(), "dashboard")

    def test_vorschau_erscheint_als_ungespeicherter_bericht(self):
        self.window.lookup_action("dry-run").activate(None)
        self.assertIn("--dry-run", self.runner.command)
        self.assertEqual(self.window.navigation.visible_tag(), "report")
        self.assertEqual(self.window.report_page.state(), "loading")
        self.runner.callback(CommandResult(0, "VORSCHAU\n[NEU] org/modell\n"))
        self.assertEqual(self.output_text(self.window), "VORSCHAU\n[NEU] org/modell\n")
        self.assertTrue(self.window.report_page.preview_label.get_visible())
        self.assertIn("Neu: 1", self.window.report_page.summary_label.get_text())

        # Über das Dashboard erscheint wieder der gespeicherte Bericht.
        self.window.navigation.pop()
        self.window.open_report()
        self.assertEqual(self.output_text(self.window), "AUTOMATISCH GELADENER BERICHT\n")
        self.assertFalse(self.window.report_page.preview_label.get_visible())

    def test_hardware_eignung_rechnet_einmal_und_auf_wunsch_neu(self):
        page = self.window.hardware_page
        self.window.apply_graphics_summary(("Grafikkarte mit 8,0 GiB Grafikspeicher erkannt", "AMD"))
        self.assertIn("8,0 GiB", self.window.dashboard.hardware_row.get_subtitle())
        self.window.dashboard.hardware_row.emit("activated")
        self.assertEqual(self.window.navigation.visible_tag(), "hardware")
        self.assertEqual(self.runner.command, logic.recommendations_command(self.config_path))
        self.assertEqual(page.state(), "loading")
        self.assertFalse(page.refresh_button.get_sensitive())
        self.runner.callback(CommandResult(0, "EIGNUNG\n"))
        self.assertEqual(page.state(), "result")
        self.assertTrue(page.copy_button.get_sensitive())

        # Erneutes Öffnen zeigt das vorhandene Ergebnis, ohne neu abzufragen.
        self.window.navigation.pop()
        self.runner.command = None
        self.window.open_hardware()
        self.assertIsNone(self.runner.command)

        # Ein abgebrochenes Neuberechnen stellt das alte Ergebnis wieder her.
        page.refresh_button.emit("clicked")
        self.runner.callback(CommandResult(-15, "", cancelled=True))
        self.assertEqual(page.state(), "result")
        page.refresh_button.emit("clicked")
        self.runner.callback(CommandResult(1, "Netzwerkfehler"))
        self.assertEqual(page.state(), "error")
        self.assertIn("Netzwerkfehler", page.error_label.get_text())

    def test_pruefung_aus_diagnose_kehrt_zur_uebersicht_zurueck(self):
        self.window.lookup_action("diagnostics").activate(None)
        self.runner.callback(CommandResult(0, json.dumps({
            "schema_version": 1, "service": {}, "timer": {},
            "findings": [{"severity": "error", "title": "Fehlgeschlagen", "actions": ["run_check"]}],
        })))
        self.window.diagnostics_page.diagnostic_action_buttons["run_check"].emit("clicked")
        self.assertEqual(self.window.navigation.visible_tag(), "dashboard")
        self.assertIn("--force", self.runner.command)

    def test_navigation_ohne_navigationview_nutzt_stack_mit_zurueck(self):
        host = NavigationHost(native=False)
        self.assertIsInstance(host.widget, Gtk.Stack)
        host.add("dashboard", "Start", Adw.HeaderBar(), Gtk.Label())
        sub_header = Adw.HeaderBar()
        host.add("report", "Bericht", sub_header, Gtk.Label())
        self.assertEqual(host.visible_tag(), "dashboard")
        host.push("report")
        self.assertEqual(host.widget.get_visible_child_name(), "report")
        host.pop()
        self.assertEqual(host.visible_tag(), "dashboard")
        host.pop()
        self.assertEqual(host.visible_tag(), "dashboard")


    # --- Barrierefreiheit -------------------------------------------------

    @staticmethod
    def descendants(widget):
        stack = [widget]
        while stack:
            current = stack.pop()
            yield current
            child = current.get_last_child()
            while child is not None:
                stack.append(child)
                child = child.get_prev_sibling()

    def present_and_settle(self, window=None):
        window = window or self.window
        window.present()
        self.spin_until(window.get_mapped)

    def test_startfokus_liegt_auf_hauptaktion_und_folgt_dem_lauf(self):
        self.present_and_settle()
        page = self.window.dashboard
        self.assertIs(self.window.get_focus(), page.run_button)
        page.run_button.emit("clicked")
        # Der deaktivierte Startbutton darf den Fokus nicht ins Leere fallen lassen.
        self.assertIs(self.window.get_focus(), page.cancel_button)
        self.runner.callback(CommandResult(0, ""))
        self.assertIs(self.window.get_focus(), page.run_button)

    def test_zugriffstasten_sind_je_ansicht_eindeutig(self):
        def mnemonics(root):
            keys = []
            for widget in self.descendants(root):
                if not widget.get_visible():
                    continue
                if isinstance(widget, (Gtk.Button, Gtk.Expander)):
                    label = widget.get_label()
                elif isinstance(widget, Adw.PreferencesRow):
                    label = widget.get_title()
                else:
                    continue
                if label and widget.get_use_underline() and "_" in label:
                    keys.append(label[label.index("_") + 1].lower())
            return keys

        dashboard = mnemonics(self.window.dashboard)
        for expected in ("j", "u", "l", "p"):
            self.assertIn(expected, dashboard)
        self.assertEqual(len(dashboard), len(set(dashboard)), dashboard)
        self.window.lookup_action("onboarding").activate(None)
        view = self.window.onboarding_view
        for position in range(3):
            view.go_to(position)
            keys = mnemonics(view) + mnemonics(view.skip_button)
            self.assertEqual(len(keys), len(set(keys)), keys)
            self.assertIn("b", keys)

    def test_bedienelemente_haben_namen_fuer_screenreader(self):
        controls = (Gtk.Button, Gtk.MenuButton, Gtk.Switch, Gtk.SpinButton, Gtk.TextView, Gtk.ProgressBar, Gtk.Spinner)
        # Interne Teile von GTK/libadwaita benennen diese Bibliotheken selbst.
        internal = (Gtk.SpinButton, Gtk.MenuButton, Gtk.WindowControls, Gtk.Expander, Adw.Banner, Adw.EntryRow)
        roots = (self.window.navigation.widget, self.window.report_page, self.window.hardware_page,
                 self.window.diagnostics_page, self.window.settings_page)
        unnamed = []
        for root in roots:
            for widget in self.descendants(root):
                if not isinstance(widget, controls) or any(
                    isinstance(ancestor, internal) for ancestor in self.ancestors(widget, root)
                ):
                    continue
                named = isinstance(widget, Gtk.Button) and bool(widget.get_label())
                if not named and not Gtk.test_accessible_has_property(widget, Gtk.AccessibleProperty.LABEL):
                    unnamed.append(f"{type(widget).__name__} in {type(widget.get_parent()).__name__}")
        self.assertEqual(unnamed, [])
        self.assertTrue(self.window.menu_button.get_primary())

    @staticmethod
    def ancestors(widget, stop):
        parent = widget.get_parent()
        while parent is not None and parent is not stop:
            yield parent
            parent = parent.get_parent()

    def test_escape_schliesst_einstellungen(self):
        self.window.open_settings()
        settings = self.window.settings_window
        self.assertTrue(settings.get_visible())
        for controller in settings.observe_controllers():
            if isinstance(controller, Gtk.ShortcutController):
                for shortcut in controller:
                    if shortcut.get_trigger().to_string() == "Escape":
                        shortcut.get_action().activate(Gtk.ShortcutActionFlags.EXCLUSIVE, settings, None)
        self.assertFalse(settings.get_visible())

    def test_fokus_folgt_dem_zustand_einer_unterseite(self):
        self.present_and_settle()
        page = self.window.hardware_page
        self.window.navigation.push("hardware")
        self.spin_until(page.get_mapped)
        page.refresh_button.grab_focus()
        page.refresh_button.emit("clicked")
        # Der Abbrechen-Button der Ladeansicht übernimmt den Fokus des deaktivierten Buttons.
        focus = self.window.get_focus()
        self.assertIsNotNone(focus)
        self.assertTrue(focus.is_ancestor(page.stack.get_child_by_name("loading")))
        self.runner.callback(CommandResult(1, "Netzwerkfehler"))
        self.assertIs(self.window.get_focus(), page.error_details)

    def test_unterseiten_ohne_navigationview_uebernehmen_den_fokus(self):
        window = Gtk.Window()
        self.addCleanup(window.destroy)
        host = NavigationHost(native=False)
        target = Gtk.Button(label="Ziel")
        host.add("dashboard", "Start", Adw.HeaderBar(), Gtk.Button(label="Start"))
        host.add("report", "Bericht", Adw.HeaderBar(show_start_title_buttons=False), target)
        window.set_child(host.widget)
        self.present_and_settle(window)
        host.push("report")
        focus = window.get_focus()
        self.assertIsNotNone(focus)
        self.assertTrue(focus.is_ancestor(host.widget.get_child_by_name("report")))

    def test_diagnosezustand_ist_nicht_nur_farbcodiert(self):
        self.window.lookup_action("diagnostics").activate(None)
        self.runner.callback(CommandResult(0, json.dumps({
            "schema_version": 1, "service": {}, "timer": {},
            "findings": [{"severity": "warning", "title": "Timer aus"}],
        })))
        labels = [w.get_text() for w in self.descendants(self.window.diagnostics_page.diagnostic_findings)
                  if isinstance(w, Gtk.Label)]
        self.assertTrue(any(text.startswith("WARNUNG: Timer aus") for text in labels), labels)

    def test_fenster_passen_auf_1024x600(self):
        logic.save_gui_preferences({"width": 1600, "height": 1000})
        small = mock.patch.object(RadarWindow, "_screen_size", return_value=(1024, 600))
        small.start()
        self.addCleanup(small.stop)
        with mock.patch("radar_gui_components.TimerController.refresh"):
            window = RadarWindow(self.app, self.config_path)
        self.addCleanup(window.close)
        width, height = window.get_default_size()
        self.assertLessEqual(width, 1024 - 64)
        self.assertLessEqual(height, 600 - 64)
        # Auch die Mindestgröße muss neben Panels und Docks Platz finden.
        self.assertLessEqual(window.get_size_request()[1], 600 - 64)
        window.open_settings()
        self.assertLessEqual(window.settings_window.get_default_size()[1], 600 - 64)
        window.settings_window.close()

    def test_einfuehrung_haelt_hauptaktion_auf_kleinen_bildschirmen_sichtbar(self):
        self.window.present()
        self.window.show_onboarding()
        view = self.window.onboarding_view
        view.go_to(2)
        # Die Hauptaktion liegt in der festen Leiste, nicht im scrollbaren Seiteninhalt.
        self.assertTrue(view.start_button.get_visible())
        self.assertFalse(view.start_button.is_ancestor(view.carousel))
        view.set_layout(compact=True, narrow=True)
        self.assertTrue(all(page.get_icon_name() is None for page, _icon in view._status_pages))
        self.assertFalse(view.dots.get_visible())
        view.set_layout(compact=False, narrow=False)
        self.assertEqual([page.get_icon_name() for page, _icon in view._status_pages],
                         [icon for _page, icon in view._status_pages])
        self.assertTrue(view.dots.get_visible())


if __name__ == "__main__":
    unittest.main()
