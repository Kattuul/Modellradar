#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Native GTK4/libadwaita-Oberfläche für den LLM-Modellradar."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from xml.sax.saxutils import escape

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402
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
from radar_gui_components import (  # noqa: E402
    ActionsPage,
    CommandResult,
    CommandRunner,
    SettingsPage,
    TimerController,
    TimerStatus,
)

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
        self.preferences = logic.load_gui_preferences()
        self.set_default_size(
            self.preferences.get("width", 920), self.preferences.get("height", 720),
        )
        self.set_size_request(700, 560)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        header.set_title_widget(Gtk.Label(label="LLM-Modellradar"))
        self.main_menu = Gio.Menu()
        primary = Gio.Menu()
        primary.append("Einstellungen", "win.preferences")
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
        outer.append(header)

        self.settings_page = SettingsPage(self.config)
        self.settings_window = self._build_settings_window()
        self.actions_page = ActionsPage(
            lambda *_: self.run_radar("force"),
            self.cancel_run,
            self.show_recommendations,
            lambda *_: self.set_timer(True),
            lambda *_: self.set_timer(False),
            self.show_diagnostics,
            self.open_settings,
            notify=self.show_toast,
        )
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        # Adw.Banner gibt es erst ab libadwaita 1.3; ältere Systeme zeigen nur den Status.
        self.banner = Adw.Banner(button_label="Diagnose") if hasattr(Adw, "Banner") else None
        if self.banner is not None:
            self.banner.connect("button-clicked", self.show_diagnostics)
            body.append(self.banner)
        body.append(self.actions_page)
        self.toast_overlay = Adw.ToastOverlay(child=body, vexpand=True)
        outer.append(self.toast_overlay)
        self.set_content(outer)
        self._install_shortcuts()
        self.connect("close-request", self._save_preferences)
        self.refresh_timer_status()
        self.refresh_check_status()
        self.load_report(announce=False)

    def _install_shortcuts(self) -> None:
        for name, callback, shortcuts in (
            ("preferences", self.open_settings, ("<Ctrl>comma",)),
            ("save", lambda *_: self.on_save(), ("<Ctrl>s",)),
            ("run-check", lambda *_: self.run_radar("force"), ("<Ctrl>r",)),
            ("dry-run", lambda *_: self.run_radar("dry"), ()),
            ("diagnostics", self.show_diagnostics, ()),
            ("shortcuts", self.show_shortcuts, ("<Ctrl>question",)),
            ("about", self.show_about, ()),
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
        self.actions_page.set_busy(busy)
        if message:
            self.actions_page.set_status(message)

    def run_command(self, command: list[str], description: str, callback=None) -> None:
        if self._busy:
            return
        self.set_busy(True, description)
        self.actions_page.clear_error()

        def finish(result: CommandResult) -> None:
            self.actions_page.set_output(result.output)
            self.set_busy(False, result.status_text())
            if result.returncode != 0 and not result.cancelled:
                self.actions_page.set_error(result.status_text(), result.output)
            if callback:
                callback(result)

        self.command_runner.run(command, finish)

    def run_radar(self, mode: str) -> None:
        arguments = ("--dry-run",) if mode == "dry" else ("--force",)
        label = "Vorschau läuft …" if mode == "dry" else "Prüfung läuft …"
        def finished(result: CommandResult) -> None:
            self.refresh_check_status()
            if mode == "force" and result.returncode == 0 and not result.cancelled:
                self.load_report()

        self.run_command(logic.radar_command(self.config_path, *arguments), label, finished)

    def cancel_run(self, _button=None) -> None:
        self.command_runner.cancel()
        self.actions_page.set_status("Abbruch angefordert …")

    def show_recommendations(self, _button=None) -> None:
        self.run_command(
            logic.recommendations_command(self.config_path),
            "Hardware-Eignung wird anhand öffentlicher Metadaten berechnet …",
        )

    def show_diagnostics(self, *_args) -> None:
        def finished(result: CommandResult) -> None:
            if result.returncode != 0 or result.cancelled:
                return
            try:
                self.actions_page.set_diagnostics(result.output)
                self.actions_page.set_status("Dienstdiagnose ausgewertet.")
                self.show_toast("Dienstdiagnose ausgewertet")
            except model_radar.RadarError as exc:
                self.actions_page.set_status(f"Dienstdiagnose kann nicht ausgewertet werden: {exc}")

        self.run_command(
            self.timer_controller.diagnostic_command(),
            "Dienstdiagnose wird geladen …",
            finished,
        )

    def open_settings(self, *_args) -> None:
        self.settings_window.present()

    def load_report(self, announce: bool = True) -> None:
        """Lädt den Bericht; beim Start fehlt er vor der ersten Prüfung regulär."""
        path = Path(self.config["report_file"]).expanduser()
        if not announce and not path.is_file():
            return
        try:
            report = path.read_text(encoding="utf-8")
            self.actions_page.set_report(report, logic.report_summary(report))
            self.actions_page.clear_error()
            if announce:
                self.show_toast("Bericht aktualisiert")
        except (model_radar.RadarError, OSError) as exc:
            self.actions_page.set_error("Bericht kann nicht geladen werden.", str(exc))

    def refresh_timer_status(self) -> None:
        self.timer_controller.refresh(self.apply_timer_status)

    def apply_timer_status(self, status: TimerStatus) -> None:
        self.actions_page.set_timer_status(status)
        if self.banner is not None:
            self.banner.set_title(f"Automatische Prüfungen nicht verfügbar: {status.error}" if status.error else "")
            self.banner.set_revealed(bool(status.error))

    def refresh_check_status(self) -> None:
        try:
            text = logic.check_status_text(self.config, persist_migration=True)
        except (model_radar.RadarError, OSError) as exc:
            text = f"API-Prüfstatus nicht verfügbar: {exc}"
        self.actions_page.set_check_status(text)

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
