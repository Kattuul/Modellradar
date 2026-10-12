#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Native GTK4/libadwaita-Oberfläche für den LLM-Modellradar."""

from __future__ import annotations

import argparse
import os
import sys
import threading
from pathlib import Path
from xml.sax.saxutils import escape

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402
except (ImportError, ValueError) as exc:
    print(
        "GUI-Abhängigkeiten fehlen: Python/PyGObject, GTK 4 und libadwaita werden benötigt.\n"
        "Debian/Ubuntu: python3-gi gir1.2-gtk-4.0 gir1.2-adw-1\n"
        "Fedora: python3-gobject gobject-introspection gtk4 libadwaita\n"
        f"Details: {exc}", file=sys.stderr,
    )
    raise SystemExit(2) from exc

import model_radar  # noqa: E402
import radar_gui_logic as logic  # noqa: E402
import radar_platform  # noqa: E402
from radar_gui_components import (  # noqa: E402
    CommandResult,
    CommandRunner,
    DashboardPage,
    DiagnosticsPage,
    HardwarePage,
    NavigationHost,
    OnboardingView,
    ReportPage,
    SettingsPage,
    TimerController,
    TimerStatus,
)

# Erhöhen, wenn sich die Einführung so stark ändert, dass alle sie erneut sehen sollen.
ONBOARDING_VERSION = 1

SHORTCUTS = (
    ("Jetzt prüfen", "<Ctrl>r"),
    ("Einstellungen öffnen", "<Ctrl>comma"),
    ("Einstellungen speichern", "<Ctrl>s"),
    ("Tastenkürzel anzeigen", "<Ctrl>question"),
)


class RadarWindow(Adw.ApplicationWindow):
    """Koordiniert Seiten und Dienste, enthält aber keine Detail-Widgets."""

    def __init__(self, application: Adw.Application, config_path: Path):
        super().__init__(application=application, title="LLM-Modellradar")
        self.config_path = config_path.expanduser()
        self.config = model_radar.load_config(self.config_path, persist_migration=True)
        self.command_runner = CommandRunner()
        self.timer_controller = TimerController()
        self._busy = False
        self._onboarding_dialog = None
        self._timer_status: TimerStatus | None = None
        self._graphics_summary: tuple[str, str] | None = None
        self.preferences = logic.load_gui_preferences()
        self.set_default_size(
            self.preferences.get("width", 920), self.preferences.get("height", 720),
        )
        self.set_size_request(700, 560)

        header = Adw.HeaderBar()
        header.set_title_widget(Gtk.Label(label="LLM-Modellradar"))
        self.main_menu = Gio.Menu()
        primary = Gio.Menu()
        primary.append("Einstellungen", "win.preferences")
        primary.append("Einführung erneut anzeigen", "win.onboarding")
        more_checks = Gio.Menu()
        more_checks.append("Vorschau ohne Änderungen", "win.dry-run")
        primary.append_submenu("Weitere Prüfoptionen", more_checks)
        primary.append("Dienstdiagnose", "win.diagnostics")
        self.main_menu.append_section(None, primary)
        about = Gio.Menu()
        about.append("Tastenkürzel", "win.shortcuts")
        about.append("Über Modellradar", "win.about")
        self.main_menu.append_section(None, about)
        menu_button = Gtk.MenuButton(
            icon_name="open-menu-symbolic",
            menu_model=self.main_menu,
            tooltip_text="Hauptmenü",
        )
        header.pack_end(menu_button)

        self.settings_page = SettingsPage(self.config)
        self.settings_window = self._build_settings_window()
        self.dashboard = DashboardPage(
            lambda *_: self.run_radar("force"),
            self.cancel_run,
            lambda *_: self.open_report(),
            lambda *_: self.open_hardware(),
            lambda *_: self.set_timer(True),
            lambda *_: self.set_timer(False),
            self.show_diagnostics,
        )
        self.report_page = ReportPage(self.cancel_run, self.show_toast)
        self.hardware_page = HardwarePage(self.cancel_run, self.show_recommendations, self.show_toast)
        self.diagnostics_page = DiagnosticsPage(
            self.cancel_run,
            lambda *_: self.set_timer(True),
            self.open_settings,
            lambda *_: self._run_check_from_subpage(),
            self.show_diagnostics,
            self.show_toast,
        )
        # Adw.Banner gibt es erst ab libadwaita 1.3; ältere Systeme zeigen nur den Status.
        self.banner = Adw.Banner(button_label="Diagnose") if hasattr(Adw, "Banner") else None
        top_bars = ()
        if self.banner is not None:
            self.banner.connect("button-clicked", self.show_diagnostics)
            top_bars = (self.banner,)

        self.navigation = NavigationHost()
        self.navigation.add("dashboard", "LLM-Modellradar", header, self.dashboard, top_bars)
        for tag, title, page, buttons in (
            ("report", "Bericht", self.report_page, (self.report_page.copy_button,)),
            ("hardware", "Hardware-Eignung", self.hardware_page,
             (self.hardware_page.copy_button, self.hardware_page.refresh_button)),
            ("diagnostics", "Dienstdiagnose", self.diagnostics_page,
             (self.diagnostics_page.copy_button, self.diagnostics_page.refresh_button)),
        ):
            page_header = Adw.HeaderBar()
            for button in buttons:
                page_header.pack_end(button)
            self.navigation.add(tag, title, page_header, page)
        self.toast_overlay = Adw.ToastOverlay(child=self.navigation.widget, vexpand=True)
        self.set_content(self.toast_overlay)
        self._install_shortcuts()
        self.connect("close-request", self._save_preferences)
        self.refresh_timer_status()
        self.refresh_check_status()
        self.load_report(announce=False)
        self._detect_graphics()

    def _install_shortcuts(self) -> None:
        for name, callback, shortcuts in (
            ("preferences", self.open_settings, ("<Ctrl>comma",)),
            ("save", lambda *_: self.on_save(), ("<Ctrl>s",)),
            ("run-check", lambda *_: self.run_radar("force"), ("<Ctrl>r",)),
            ("dry-run", lambda *_: self.run_radar("dry"), ()),
            ("diagnostics", self.show_diagnostics, ()),
            ("shortcuts", self.show_shortcuts, ("<Ctrl>question",)),
            ("about", self.show_about, ()),
            ("onboarding", lambda *_: self.show_onboarding(), ()),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
            if shortcuts:
                self.get_application().set_accels_for_action(f"win.{name}", list(shortcuts))

    def _build_settings_window(self) -> Adw.Window:
        window = Adw.Window(title="Einstellungen", transient_for=self, modal=True)
        window.set_default_size(780, 680)
        window.set_hide_on_close(True)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        header.set_title_widget(Gtk.Label(label="Einstellungen"))
        save_button = Gtk.Button(label="Speichern")
        save_button.add_css_class("suggested-action")
        save_button.connect("clicked", self.on_save)
        header.pack_end(save_button)
        content.append(header)
        self.settings_toast_overlay = Adw.ToastOverlay(child=self.settings_page, vexpand=True)
        content.append(self.settings_toast_overlay)
        window.set_content(content)
        # Strg+S gilt sonst nur im Hauptfenster, nicht im Einstellungsfenster.
        save_shortcuts = Gtk.ShortcutController()
        save_shortcuts.add_shortcut(Gtk.Shortcut.new(
            Gtk.ShortcutTrigger.parse_string("<Ctrl>s"),
            Gtk.CallbackAction.new(lambda *_: self.on_save() or True),
        ))
        window.add_controller(save_shortcuts)
        self.settings_save_button = save_button
        return window

    def show_toast(self, text: str) -> None:
        self.toast_overlay.add_toast(Adw.Toast(title=text))

    def _save_preferences(self, *_args) -> bool:
        width, height = self.get_width(), self.get_height()
        if width < 700 or height < 560:
            width, height = self.get_default_size()
        self.preferences.update(width=width, height=height)
        try:
            logic.save_gui_preferences(self.preferences)
        except OSError:
            pass
        return False

    def on_save(self, _button=None) -> bool:
        try:
            config = self.settings_page.collect_config()
            logic.save_config(self.config_path, config)
            self.config = config
            self.settings_page.accept_config(config)
            self.refresh_check_status()
            self.settings_window.set_visible(False)
            self.show_toast("Einstellungen gespeichert")
            return True
        except (model_radar.RadarError, OSError) as exc:
            self.settings_window.present()
            self.settings_toast_overlay.add_toast(Adw.Toast(title=f"Nicht gespeichert: {exc}", timeout=0))
            return False

    def set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        self.settings_save_button.set_sensitive(not busy)
        for name in ("save", "run-check", "dry-run", "diagnostics"):
            self.lookup_action(name).set_enabled(not busy)
        for page in (self.dashboard, self.hardware_page, self.diagnostics_page):
            page.set_busy(busy)
        if message:
            self.dashboard.set_status(message)

    def run_command(self, command: list[str], description: str, callback=None) -> bool:
        """Startet einen Hintergrundbefehl; liefert False, wenn bereits einer läuft."""
        if self._busy:
            return False
        self.set_busy(True, description)
        self.dashboard.clear_error()

        def finish(result: CommandResult) -> None:
            self.set_busy(False, result.status_text())
            if result.returncode != 0 and not result.cancelled:
                self.dashboard.set_error(result.status_text(), result.output)
            if callback:
                callback(result)

        self.command_runner.run(command, finish)
        return True

    def run_radar(self, mode: str) -> None:
        arguments = ("--dry-run",) if mode == "dry" else ("--force",)
        label = "Vorschau läuft …" if mode == "dry" else "Prüfung läuft …"
        first_check = not self.report_page.report_text
        previous_state = self.report_page.state()

        def finished(result: CommandResult) -> None:
            self.refresh_check_status()
            if result.returncode != 0 or result.cancelled:
                if mode == "dry":
                    self._show_page_failure(self.report_page, result, previous_state)
                return
            if mode == "dry":
                self.report_page.set_preview(result.output, logic.report_summary(result.output))
            else:
                self.load_report(announce=not first_check)
                if first_check:
                    self._announce_first_report()

        if self.run_command(logic.radar_command(self.config_path, *arguments), label, finished) and mode == "dry":
            # Die Vorschau erscheint dort, wo auch der Bericht steht – klar als ungespeichert markiert.
            self.report_page.set_loading()
            self.navigation.push("report")

    @staticmethod
    def _show_page_failure(page, result: CommandResult, previous_state: str) -> None:
        """Abbruch stellt den vorigen Inhalt wieder her; Fehler werden auf der Seite erklärt."""
        if result.cancelled:
            page.restore(previous_state)
        else:
            page.set_error(result.status_text(), result.output)

    def _run_check_from_subpage(self) -> None:
        self.navigation.pop_to_root()
        self.run_radar("force")

    def cancel_run(self, _button=None) -> None:
        self.command_runner.cancel()
        self.dashboard.set_status("Abbruch angefordert …")

    def open_report(self) -> None:
        self.report_page.show_saved()
        self.navigation.push("report")

    def open_hardware(self) -> None:
        self.navigation.push("hardware")
        if self.hardware_page.state() == "empty":
            self.show_recommendations()

    def show_recommendations(self, *_args) -> None:
        previous_state = self.hardware_page.state()

        def finished(result: CommandResult) -> None:
            if result.returncode == 0 and not result.cancelled:
                self.hardware_page.set_result(result.output)
            else:
                self._show_page_failure(self.hardware_page, result, previous_state)

        if self.run_command(
            logic.recommendations_command(self.config_path),
            "Hardware-Eignung wird anhand öffentlicher Metadaten berechnet …",
            finished,
        ):
            self.hardware_page.set_loading()

    def show_diagnostics(self, *_args) -> None:
        previous_state = self.diagnostics_page.state()

        def finished(result: CommandResult) -> None:
            if result.returncode != 0 or result.cancelled:
                self._show_page_failure(self.diagnostics_page, result, previous_state)
                return
            try:
                self.diagnostics_page.set_diagnostics(result.output)
                self.dashboard.set_status("Dienstdiagnose ausgewertet.")
            except model_radar.RadarError as exc:
                message = f"Dienstdiagnose kann nicht ausgewertet werden: {exc}"
                self.dashboard.set_status(message)
                self.diagnostics_page.set_error(message, result.output)

        self.navigation.push("diagnostics")
        if self.run_command(
            self.timer_controller.diagnostic_command(),
            "Dienstdiagnose wird geladen …",
            finished,
        ):
            self.diagnostics_page.set_loading()

    def open_settings(self, *_args) -> None:
        self.settings_window.present()

    def load_report(self, announce: bool = True) -> None:
        """Lädt den Bericht; beim Start fehlt er vor der ersten Prüfung regulär."""
        path = Path(self.config["report_file"]).expanduser()
        if not announce and not path.is_file():
            return
        try:
            report = path.read_text(encoding="utf-8")
            summary = logic.report_summary(report)
            self.report_page.set_report(report, summary)
            self.dashboard.set_report_summary(summary)
            self.dashboard.clear_error()
            if announce:
                toast = Adw.Toast(title="Bericht aktualisiert", button_label="Ansehen")
                toast.connect("button-clicked", lambda *_: self.open_report())
                self.toast_overlay.add_toast(toast)
        except (model_radar.RadarError, OSError) as exc:
            self.dashboard.set_error("Bericht kann nicht geladen werden.", str(exc))

    def _announce_first_report(self) -> None:
        """Zeigt Einsteigern nach der ersten Prüfung, wo das Ergebnis steht."""
        toast = Adw.Toast(title="Erste Prüfung abgeschlossen", button_label="Bericht ansehen", timeout=8)
        toast.connect("button-clicked", lambda *_: self.open_report())
        self.toast_overlay.add_toast(toast)

    def maybe_show_onboarding(self) -> None:
        """Startet die Einführung nur, solange die aktuelle Fassung noch nicht gesehen wurde."""
        if self.preferences.get("onboarding_version", 0) < ONBOARDING_VERSION:
            self.show_onboarding()

    def show_onboarding(self) -> None:
        if self._onboarding_dialog is not None:
            return  # Bereits geöffnet und modal.
        view = OnboardingView(lambda start_check: self._finish_onboarding(start_check))
        header = Adw.HeaderBar(show_end_title_buttons=True)
        header.pack_start(view.skip_button)
        header.set_title_widget(view.step_label)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(header)
        content.append(view)
        # Adw.Dialog gibt es erst ab libadwaita 1.5; ältere Systeme erhalten ein modales Fenster.
        if hasattr(Adw, "Dialog"):
            dialog = Adw.Dialog(title="Einführung", content_width=560, content_height=620, child=content)
            dialog.connect("closed", lambda *_: self._onboarding_closed(view))
            dialog.present(self)
        else:
            dialog = Adw.Window(title="Einführung", transient_for=self, modal=True, content=content)
            dialog.set_default_size(560, 620)
            escape = Gtk.ShortcutController()
            escape.add_shortcut(Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string("Escape"),
                Gtk.CallbackAction.new(lambda *_: dialog.close() or True),
            ))
            dialog.add_controller(escape)
            dialog.connect("close-request", lambda *_: self._onboarding_closed(view) or False)
            dialog.present()
        self._onboarding_dialog = dialog
        self.onboarding_view = view
        self._sync_onboarding_timer()
        if self._graphics_summary is not None:
            view.set_hardware(*self._graphics_summary)

    def _sync_onboarding_timer(self) -> None:
        status = self._timer_status
        if self._onboarding_dialog is not None and status is not None:
            self.onboarding_view.set_timer_state(
                enabled=not status.error and status.enabled == "enabled",
                available=not status.error,
            )

    def _detect_graphics(self) -> None:
        """Liest die Grafikhardware einmal im Hintergrund, weil nvidia-smi einige Sekunden brauchen kann."""
        def worker() -> None:
            try:
                summary = logic.graphics_summary(radar_platform.detect_graphics())
            except Exception as exc:  # Die Oberfläche darf an der Hardwareerkennung nie scheitern.
                summary = ("Grafikkarte nicht ermittelbar", f"Hinweis: {exc}")
            GLib.idle_add(lambda: self.apply_graphics_summary(summary) or GLib.SOURCE_REMOVE)

        threading.Thread(target=worker, daemon=True).start()

    def apply_graphics_summary(self, summary: tuple[str, str]) -> None:
        self._graphics_summary = summary
        self.dashboard.set_graphics(summary[0])
        self.hardware_page.set_graphics(*summary)
        if self._onboarding_dialog is not None:
            self.onboarding_view.set_hardware(*summary)

    def _onboarding_closed(self, view: OnboardingView) -> None:
        self._onboarding_dialog = None
        view.finish(False)

    def _finish_onboarding(self, start_check: bool) -> None:
        """Merkt sich die Einführung sofort, damit sie auch nach einem Absturz nicht erneut erscheint."""
        dialog, self._onboarding_dialog = self._onboarding_dialog, None
        if dialog is not None:
            dialog.close()
        self.preferences["onboarding_version"] = ONBOARDING_VERSION
        try:
            logic.save_gui_preferences(self.preferences)
        except OSError:
            pass
        if start_check:
            self.run_radar("force")

    def refresh_timer_status(self) -> None:
        self.timer_controller.refresh(self.apply_timer_status)

    def apply_timer_status(self, status: TimerStatus) -> None:
        self._timer_status = status
        self.dashboard.set_timer_status(status)
        self.diagnostics_page.set_timer_available(not status.error)
        if self.banner is not None:
            self.banner.set_title(f"Automatische Prüfungen nicht verfügbar: {status.error}" if status.error else "")
            self.banner.set_revealed(bool(status.error))
        self._sync_onboarding_timer()

    def refresh_check_status(self) -> None:
        try:
            text = logic.check_status_text(self.config, persist_migration=True)
        except (model_radar.RadarError, OSError) as exc:
            text = f"API-Prüfstatus nicht verfügbar: {exc}"
        self.dashboard.set_check_status(text)

    def set_timer(self, enable: bool) -> None:
        if enable:
            self._confirm_timer_enable()
            return
        self._change_timer(False)

    def _confirmation_dialog(self, heading: str, body: str):
        """Adw.AlertDialog ab libadwaita 1.5, sonst das ältere Adw.MessageDialog."""
        if hasattr(Adw, "AlertDialog"):
            return Adw.AlertDialog(heading=heading, body=body)
        return Adw.MessageDialog(transient_for=self, modal=True, heading=heading, body=body)

    def _confirm_timer_enable(self) -> None:
        dialog = self._confirmation_dialog(
            "Automatische Prüfungen einschalten?",
            "Der Radar prüft künftig regelmäßig im Hintergrund über das Netzwerk. "
            "Modelle werden dabei weder heruntergeladen noch ausgeführt.",
        )
        dialog.add_response("cancel", "Abbrechen")
        dialog.add_response("enable", "Einschalten")
        dialog.set_response_appearance("enable", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def respond(_dialog, response: str) -> None:
            if response == "enable":
                self._change_timer(True)
            else:
                # Der Schalter springt auf den tatsächlichen Zustand zurück.
                self.refresh_timer_status()

        dialog.connect("response", respond)
        if isinstance(dialog, Adw.MessageDialog):
            dialog.present()
        else:
            dialog.present(self)

    def _change_timer(self, enable: bool) -> None:
        def finished(result: CommandResult) -> None:
            self.refresh_timer_status()
            if result.returncode == 0 and not result.cancelled:
                self.show_toast(
                    "Automatische Prüfungen eingeschaltet" if enable else "Automatische Prüfungen ausgeschaltet"
                )

        self.run_command(
            self.timer_controller.change_command(enable),
            "Automatische Prüfungen werden umgeschaltet …",
            finished,
        )

    def show_shortcuts(self, *_args) -> None:
        if hasattr(Adw, "ShortcutsDialog"):
            dialog = Adw.ShortcutsDialog()
            section = Adw.ShortcutsSection(title="Allgemein")
            for title, accelerator in SHORTCUTS:
                section.add(Adw.ShortcutsItem.new(title, accelerator))
            dialog.add(section)
            dialog.present(self)
            return
        shortcuts = "".join(
            f'<child><object class="GtkShortcutsShortcut"><property name="title">{escape(title)}</property>'
            f'<property name="accelerator">{escape(accelerator)}</property></object></child>'
            for title, accelerator in SHORTCUTS
        )
        builder = Gtk.Builder.new_from_string(
            '<interface><object class="GtkShortcutsWindow" id="window"><property name="modal">true</property>'
            '<child><object class="GtkShortcutsSection"><child><object class="GtkShortcutsGroup">'
            f'<property name="title">Allgemein</property>{shortcuts}'
            "</object></child></object></child></object></interface>",
            -1,
        )
        window = builder.get_object("window")
        window.set_transient_for(self)
        window.present()

    def show_about(self, *_args) -> None:
        details = dict(
            application_name="LLM-Modellradar",
            application_icon=os.environ.get("FLATPAK_ID", "io.github.Kattuul.Modellradar"),
            version=model_radar.VERSION,
            developer_name="Kattuul",
            website="https://github.com/Kattuul/Modellradar",
            issue_url="https://github.com/Kattuul/Modellradar/issues",
            license_type=Gtk.License.GPL_3_0,
            comments="Meldet neue und geänderte LLMs anhand öffentlicher Metadaten. "
            "Es werden keine Modelle heruntergeladen oder ausgeführt.",
        )
        if hasattr(Adw, "AboutDialog"):
            Adw.AboutDialog(**details).present(self)
        else:
            Adw.AboutWindow(transient_for=self, modal=True, **details).present()


class RadarApplication(Adw.Application):
    def __init__(self, config_path: Path):
        super().__init__(application_id=os.environ.get("FLATPAK_ID", "de.local.Qwen35ModelRadar"), flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.config_path = config_path
        self.connect("activate", self.on_activate)

    def on_activate(self, _application) -> None:
        window = self.props.active_window
        if window is None:
            try:
                window = RadarWindow(self, self.config_path)
            except (model_radar.RadarError, OSError) as exc:
                window = Adw.ApplicationWindow(application=self, title="LLM-Modellradar – Fehler")
                window.set_default_size(620, 180)
                window.set_content(
                    Gtk.Label(
                        label=f"Konfiguration kann nicht geladen werden:\n{exc}",
                        wrap=True,
                        margin_top=24,
                        margin_bottom=24,
                        margin_start=24,
                        margin_end=24,
                    )
                )
        window.present()
        if isinstance(window, RadarWindow):
            window.maybe_show_onboarding()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Grafische Einstellungen für den LLM-Modellradar")
    parser.add_argument("--config", type=Path, default=logic.default_gui_config_path())
    parser.add_argument("--check", action="store_true", help="Abhängigkeiten und Konfiguration ohne Fenster prüfen")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        model_radar.load_config(args.config.expanduser())
        if args.check:
            print("GUI-Abhängigkeiten und Konfiguration: OK")
            return 0
        Gtk.init_check()
        if Gdk.Display.get_default() is None:
            print("FEHLER: GTK kann keine grafische Sitzung erreichen.")
            return 2
        return RadarApplication(args.config).run(None)
    except model_radar.RadarError as exc:
        print(f"FEHLER: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
