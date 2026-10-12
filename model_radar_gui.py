#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Native GTK4/libadwaita-Oberfläche für den LLM-Modellradar."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

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
        self.save_button = Gtk.Button(label="Speichern")
        self.save_button.add_css_class("suggested-action")
        self.save_button.connect("clicked", self.on_save)
        header.pack_end(self.save_button)
        outer.append(header)

        self.settings_page = SettingsPage(self.config)
        self.actions_page = ActionsPage(
            lambda *_: self.run_radar("dry"),
            lambda *_: self.run_radar("force"),
            self.cancel_run,
            self.load_report,
            lambda *_: self.set_timer(True),
            lambda *_: self.set_timer(False),
            self.show_diagnostics,
            self.open_settings,
        )
        self.notebook = Gtk.Notebook(hexpand=True, vexpand=True)
        self.notebook.append_page(self.settings_page, Gtk.Label(label="Einstellungen"))
        self.notebook.append_page(self.actions_page, Gtk.Label(label="Prüfung und Bericht"))
        self.notebook.set_current_page(self.preferences.get("page", 0))
        self.notebook.connect("switch-page", self._remember_page)
        outer.append(self.notebook)
        self.set_content(outer)
        self._install_shortcuts()
        self.connect("close-request", self._save_preferences)
        self.refresh_timer_status()
        self.refresh_check_status()

    def _install_shortcuts(self) -> None:
        for name, callback, shortcuts in (
            ("save", lambda *_: self.on_save(), ("<Ctrl>s",)),
            ("run-check", lambda *_: self.run_radar("force"), ("<Ctrl>r",)),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
            self.get_application().set_accels_for_action(f"win.{name}", list(shortcuts))

    def _remember_page(self, _notebook, _page, page_num: int) -> None:
        self.preferences["page"] = page_num

    def _save_preferences(self, *_args) -> bool:
        width, height = self.get_width(), self.get_height()
        if width < 700 or height < 560:
            width, height = self.get_default_size()
        self.preferences.update(page=self.notebook.get_current_page(), width=width, height=height)
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
            self.actions_page.set_status(f"Gespeichert: {self.config_path}")
            self.refresh_check_status()
            return True
        except (model_radar.RadarError, OSError) as exc:
            self.actions_page.set_status(f"Fehler: {exc}")
            self.notebook.set_current_page(0)
            return False

    def set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        self.save_button.set_sensitive(not busy)
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
        if not self.on_save():
            return
        arguments = ("--dry-run",) if mode == "dry" else ("--force",)
        label = "Dry-Run läuft …" if mode == "dry" else "Prüfung läuft …"
        def finished(result: CommandResult) -> None:
            self.refresh_check_status()
            if mode == "force" and result.returncode == 0 and not result.cancelled:
                self.load_report()

        self.run_command(logic.radar_command(self.config_path, *arguments), label, finished)

    def cancel_run(self, _button=None) -> None:
        self.command_runner.cancel()
        self.actions_page.set_status("Abbruch angefordert …")

    def show_diagnostics(self, _button=None) -> None:
        def finished(result: CommandResult) -> None:
            if result.returncode != 0 or result.cancelled:
                return
            try:
                self.actions_page.set_diagnostics(result.output)
                self.actions_page.set_status("Dienstdiagnose ausgewertet.")
            except model_radar.RadarError as exc:
                self.actions_page.set_status(f"Dienstdiagnose kann nicht ausgewertet werden: {exc}")

        self.run_command(
            self.timer_controller.diagnostic_command(),
            "Dienstdiagnose wird geladen …",
            finished,
        )

    def open_settings(self, _button=None) -> None:
        self.notebook.set_current_page(0)

    def load_report(self, _button=None) -> None:
        try:
            path = Path(self.settings_page.collect_config()["report_file"]).expanduser()
            report = path.read_text(encoding="utf-8")
            self.actions_page.set_report(report, logic.report_summary(report))
            self.actions_page.clear_error()
            self.actions_page.set_status(f"Bericht geladen: {path}")
        except (model_radar.RadarError, OSError) as exc:
            self.actions_page.set_error("Bericht kann nicht geladen werden.", str(exc))

    def refresh_timer_status(self) -> None:
        self.timer_controller.refresh(self.actions_page.set_timer_status)

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

    def _confirm_timer_enable(self) -> None:
        dialog = Adw.MessageDialog.new(
            self,
            "Automatische Prüfungen aktivieren?",
            "Der Timer startet künftig im Hintergrund regelmäßige Netzwerkprüfungen. "
            "Modelle werden dabei weder heruntergeladen noch ausgeführt.",
        )
        dialog.add_response("cancel", "Abbrechen")
        dialog.add_response("enable", "Timer aktivieren")
        dialog.set_response_appearance("enable", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _dialog, response: self._change_timer(True) if response == "enable" else None)
        dialog.present()

    def _change_timer(self, enable: bool) -> None:
        self.run_command(
            self.timer_controller.change_command(enable),
            "Timer wird geändert …",
            lambda _result: self.refresh_timer_status(),
        )


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
