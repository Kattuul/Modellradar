# SPDX-License-Identifier: GPL-3.0-or-later
"""Wiederverwendbare GTK-Seiten und Hintergrunddienste der Radar-GUI."""

from __future__ import annotations

import json
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable, Sequence

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

import model_radar  # noqa: E402
import radar_gui_logic as logic  # noqa: E402
from radar_platform import SystemdScheduler, TimerStatus  # noqa: E402


@dataclass(frozen=True)
class CommandResult:
    """Vollständiges, für die Oberfläche geeignetes Prozessergebnis."""

    returncode: int
    output: str
    cancelled: bool = False

    def status_text(self) -> str:
        if self.cancelled:
            return "Abgebrochen."
        if self.returncode == 0:
            return "Erfolgreich abgeschlossen."
        lines = [line.strip() for line in self.output.splitlines() if line.strip()]
        detail = lines[-1] if lines else "Keine Fehlerdetails ausgegeben."
        if len(detail) > 240:
            detail = detail[:237] + "…"
        return f"Fehlgeschlagen (Exitcode {self.returncode}): {detail}"


class CommandRunner:
    """Führt feste Argumentlisten ohne Shell außerhalb des GTK-Threads aus."""

    def __init__(self, timeout_seconds: int = 900):
        self.timeout_seconds = timeout_seconds
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._cancel_requested = False

    @staticmethod
    def execute(command: Sequence[str], timeout_seconds: int = 900) -> CommandResult:
        try:
            completed = subprocess.run(
                list(command),
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
            output = completed.stdout
            if completed.stderr:
                output += ("" if not output or output.endswith("\n") else "\n") + completed.stderr
            return CommandResult(completed.returncode, output)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return CommandResult(1, f"Fehler: {exc}")

    def run(self, command: Sequence[str], callback: Callable[[CommandResult], None]) -> None:
        def worker() -> None:
            process: subprocess.Popen[str] | None = None
            try:
                process = subprocess.Popen(
                    list(command),
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
                with self._lock:
                    self._process = process
                    cancelled = self._cancel_requested
                if cancelled:
                    process.terminate()
                try:
                    output, _ = process.communicate(timeout=self.timeout_seconds)
                except subprocess.TimeoutExpired:
                    process.kill()
                    output, _ = process.communicate()
                    output += ("" if not output or output.endswith("\n") else "\n") + "Fehler: Zeitlimit überschritten."
                with self._lock:
                    cancelled = self._cancel_requested
                result = CommandResult(process.returncode or 0, output, cancelled)
            except OSError as exc:
                result = CommandResult(1, f"Fehler: {exc}")
            finally:
                with self._lock:
                    if self._process is process:
                        self._process = None
                    self._cancel_requested = False

            def deliver() -> bool:
                callback(result)
                return GLib.SOURCE_REMOVE

            GLib.idle_add(deliver)

        threading.Thread(target=worker, daemon=True).start()

    def cancel(self) -> bool:
        """Fordert einen sanften Abbruch des aktuell laufenden Prozesses an."""
        with self._lock:
            self._cancel_requested = True
            process = self._process
        if process is None or process.poll() is not None:
            return False
        try:
            process.terminate()
            return True
        except OSError:
            return False


class TimerController(SystemdScheduler):
    """Liefert den GUI-unabhängigen Dienststatus im GTK-Thread zurück."""

    def refresh(self, callback: Callable[[TimerStatus], None]) -> None:
        def worker() -> None:
            status = self.query_status()

            def deliver() -> bool:
                callback(status)
                return GLib.SOURCE_REMOVE

            GLib.idle_add(deliver)

        threading.Thread(target=worker, daemon=True).start()


class SettingsPage(Gtk.ScrolledWindow):
    """Konfigurationsformular samt Übersetzung in die geprüfte JSON-Struktur."""

    def __init__(self, config: dict):
        super().__init__()
        self.config = config
        grid = Gtk.Grid(
            column_spacing=24,
            row_spacing=16,
            margin_top=24,
            margin_bottom=24,
            margin_start=24,
            margin_end=24,
        )
        grid.set_hexpand(True)

        self.api_entry = Gtk.Entry(text=config["api_url"], editable=False, hexpand=True)
        self._add_row(grid, 0, "API-Adresse", self.api_entry, "Aus Sicherheitsgründen fest auf Hugging Face begrenzt.")

        self.publishers_view = self._text_view(logic.mapping_to_text(config["publishers"]), True, 125)
        self._add_row(grid, 1, "Herausgeber", self.publishers_view, "Eine Zeile je Namespace: Herausgeber: Familie1, Familie2")

        self.families_view = self._text_view(logic.mapping_to_text(config["families"]), True, 100)
        self._add_row(grid, 2, "Modellfamilien", self.families_view, "Eine Zeile je Familie: Anzeigename: Suchbegriff1, Suchbegriff2")

        range_box = Gtk.Box(spacing=12)
        self.min_spin = Gtk.SpinButton.new_with_range(0.1, 1000, 0.1)
        self.min_spin.set_value(float(config["parameter_min_b"]))
        self.max_spin = Gtk.SpinButton.new_with_range(0.1, 1000, 0.1)
        self.max_spin.set_value(float(config["parameter_max_b"]))
        range_box.append(Gtk.Label(label="Minimum (B)"))
        range_box.append(self.min_spin)
        range_box.append(Gtk.Label(label="Maximum (B)"))
        range_box.append(self.max_spin)
        self._add_row(grid, 3, "Parameterbereich", range_box)

        self.pipeline_entry = Gtk.Entry(text=logic.list_to_text(config.get("preferred_pipeline_tags", [])), hexpand=True)
        self._add_row(grid, 4, "Bevorzugte Pipelines", self.pipeline_entry, "Kommagetrennt")

        self.filter_modes = ("conservative", "strict_text")
        self.filter_mode = Gtk.DropDown.new_from_strings([
            "Konservativer Hinweis-Modus",
            "Strikter Textmodell-Modus",
        ])
        self.filter_mode.set_selected(self.filter_modes.index(config.get("filter_mode", "conservative")))
        self._add_row(
            grid, 5, "Filtermodus", self.filter_mode,
            "Strikt akzeptiert nur eine der bevorzugten Text-Pipelines.",
        )

        self.excluded_view = self._text_view(logic.list_to_text(config.get("excluded_name_terms", [])), False, 75)
        self._add_row(grid, 6, "Ausgeschlossene Namensbestandteile", self.excluded_view, "Kommagetrennt; Groß-/Kleinschreibung wird ignoriert.")

        self.interval_spin = Gtk.SpinButton.new_with_range(1, 365, 1)
        self.interval_spin.set_value(int(config.get("check_interval_days", 7)))
        self._add_row(grid, 7, "Prüfintervall in Tagen", self.interval_spin, "Der tägliche systemd-Timer fragt nur bei Fälligkeit die API ab.")

        self.retention_spin = Gtk.SpinButton.new_with_range(0, 36500, 1)
        self.retention_spin.set_value(int(config.get("absent_retention_days", 365)))
        self._add_row(
            grid, 8, "Aufbewahrung entfernter Modelle",
            self.retention_spin, "Tage; 0 bewahrt abwesende Modelle unbegrenzt auf.",
        )

        network_box = Gtk.Box(spacing=10)
        self.timeout_spin = Gtk.SpinButton.new_with_range(1, 300, 1)
        self.timeout_spin.set_value(int(config.get("request_timeout_seconds", 20)))
        self.retries_spin = Gtk.SpinButton.new_with_range(1, 10, 1)
        self.retries_spin.set_value(int(config.get("request_retries", 3)))
        self.backoff_spin = Gtk.SpinButton.new_with_range(0.1, 60, 0.5)
        self.backoff_spin.set_value(float(config.get("retry_backoff_seconds", 2)))
        for label, widget in (("Timeout", self.timeout_spin), ("Versuche", self.retries_spin), ("Backoff", self.backoff_spin)):
            network_box.append(Gtk.Label(label=label))
            network_box.append(widget)
        self._add_row(grid, 9, "Netzwerk", network_box, "Sekunden beziehungsweise Anzahl der Versuche")

        paging_box = Gtk.Box(spacing=10)
        self.page_size_spin = Gtk.SpinButton.new_with_range(1, 1000, 1)
        self.page_size_spin.set_value(int(config.get("page_size", 100)))
        self.pages_spin = Gtk.SpinButton.new_with_range(1, 100, 1)
        self.pages_spin.set_value(int(config.get("max_pages_per_query", 10)))
        paging_box.append(Gtk.Label(label="Seitengröße"))
        paging_box.append(self.page_size_spin)
        paging_box.append(Gtk.Label(label="Max. Seiten"))
        paging_box.append(self.pages_spin)
        self._add_row(grid, 10, "API-Pagination", paging_box)

        self.first_run_switch = Gtk.Switch(active=bool(config.get("notify_on_first_run", False)), halign=Gtk.Align.START)
        self._add_row(grid, 11, "Benachrichtigung beim Erstlauf", self.first_run_switch)
        self.state_entry = Gtk.Entry(text=str(config["state_file"]), hexpand=True)
        self._add_row(grid, 12, "Zustandsdatei", self.state_entry)
        self.report_entry = Gtk.Entry(text=str(config["report_file"]), hexpand=True)
        self._add_row(grid, 13, "Berichtsdatei", self.report_entry)
        self.set_child(grid)

    @staticmethod
    def _text_view(text: str = "", monospace: bool = False, min_height: int = 90) -> Gtk.TextView:
        view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, monospace=monospace)
        view.set_size_request(-1, min_height)
        view.get_buffer().set_text(text)
        return view

    @staticmethod
    def _add_row(grid: Gtk.Grid, row: int, label: str, widget: Gtk.Widget, hint: str = "") -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(label=label, xalign=0)
        title.add_css_class("heading")
        box.append(title)
        if hint:
            detail = Gtk.Label(label=hint, xalign=0, wrap=True)
            detail.add_css_class("dim-label")
            box.append(detail)
        grid.attach(box, 0, row, 1, 1)
        grid.attach(widget, 1, row, 1, 1)

    @staticmethod
    def _buffer_text(view: Gtk.TextView) -> str:
        buffer = view.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)

    def collect_config(self) -> dict:
        config = dict(self.config)
        config.update({
            "api_url": self.api_entry.get_text(),
            "publishers": logic.text_to_mapping(self._buffer_text(self.publishers_view), "Herausgeber"),
            "families": logic.text_to_mapping(self._buffer_text(self.families_view), "Modellfamilien"),
            "parameter_min_b": self.min_spin.get_value(),
            "parameter_max_b": self.max_spin.get_value(),
            "preferred_pipeline_tags": logic.text_to_list(self.pipeline_entry.get_text(), "Pipelines", allow_empty=True),
            "filter_mode": self.filter_modes[self.filter_mode.get_selected()],
            "excluded_name_terms": logic.text_to_list(self._buffer_text(self.excluded_view), "Ausschlüsse", allow_empty=True),
            "check_interval_days": self.interval_spin.get_value_as_int(),
            "absent_retention_days": self.retention_spin.get_value_as_int(),
            "request_timeout_seconds": self.timeout_spin.get_value_as_int(),
            "request_retries": self.retries_spin.get_value_as_int(),
            "retry_backoff_seconds": self.backoff_spin.get_value(),
            "page_size": self.page_size_spin.get_value_as_int(),
            "max_pages_per_query": self.pages_spin.get_value_as_int(),
            "notify_on_first_run": self.first_run_switch.get_active(),
            "state_file": logic.validate_data_path(self.state_entry.get_text(), "Zustandsdatei"),
            "report_file": logic.validate_data_path(self.report_entry.get_text(), "Berichtsdatei"),
        })
        return model_radar.validate_config(config)

    def accept_config(self, config: dict) -> None:
        """Merkt sich die erfolgreich gespeicherte Fassung als neue Basis."""
        self.config = config


class ActionsPage(Gtk.Box):
    """Bedienseite für Prüfungen, Berichtsausgabe und Timersteuerung."""

    def __init__(
        self, on_dry_run, on_force_run, on_cancel, on_load_report,
        on_enable_timer, on_disable_timer, on_diagnostics, on_open_settings,
    ):
        super().__init__(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=14,
            margin_top=24,
            margin_bottom=24,
            margin_start=24,
            margin_end=24,
        )
        button_box = Gtk.Box(spacing=10)
        self.dry_button = Gtk.Button(label="Dry-Run starten")
        self.dry_button.connect("clicked", on_dry_run)
        self.run_button = Gtk.Button(label="Jetzt prüfen")
        self.run_button.add_css_class("suggested-action")
        self.run_button.connect("clicked", on_force_run)
        self.cancel_button = Gtk.Button(label="Lauf abbrechen", sensitive=False)
        self.cancel_button.connect("clicked", on_cancel)
        self.report_button = Gtk.Button(label="Gespeicherten Bericht laden")
        self.report_button.connect("clicked", on_load_report)
        for button in (self.dry_button, self.run_button, self.cancel_button, self.report_button):
            button_box.append(button)
        self.append(button_box)

        timer_box = Gtk.Box(spacing=10)
        self.timer_label = Gtk.Label(label="Timerstatus wird geladen …", xalign=0, hexpand=True)
        self.enable_button = Gtk.Button(label="Timer aktivieren")
        self.enable_button.connect("clicked", on_enable_timer)
        self.disable_button = Gtk.Button(label="Timer deaktivieren")
        self.disable_button.connect("clicked", on_disable_timer)
        self.diagnostic_button = Gtk.Button(label="Dienstdiagnose")
        self.diagnostic_button.connect("clicked", on_diagnostics)
        timer_box.append(self.timer_label)
        timer_box.append(self.enable_button)
        timer_box.append(self.disable_button)
        timer_box.append(self.diagnostic_button)
        self.append(timer_box)

        self.check_label = Gtk.Label(label="API-Prüfstatus wird geladen …", xalign=0, wrap=True)
        self.append(self.check_label)

        self.diagnostic_panel = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8, visible=False,
        )
        self.diagnostic_title = Gtk.Label(label="Dienstdiagnose", xalign=0)
        self.diagnostic_title.add_css_class("heading")
        self.diagnostic_panel.append(self.diagnostic_title)
        self.diagnostic_findings = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.diagnostic_panel.append(self.diagnostic_findings)
        diagnostic_actions = Gtk.Box(spacing=8)
        self.diagnostic_action_buttons = {
            "enable_timer": Gtk.Button(label="Timer aktivieren"),
            "open_settings": Gtk.Button(label="Konfiguration prüfen"),
            "run_check": Gtk.Button(label="Prüfung erneut starten"),
            "retry_diagnostics": Gtk.Button(label="Diagnose wiederholen"),
        }
        callbacks = {
            "enable_timer": on_enable_timer,
            "open_settings": on_open_settings,
            "run_check": on_force_run,
            "retry_diagnostics": on_diagnostics,
        }
        for action, button in self.diagnostic_action_buttons.items():
            button.connect("clicked", callbacks[action])
            button.set_visible(False)
            diagnostic_actions.append(button)
        self.diagnostic_panel.append(diagnostic_actions)
        self.append(self.diagnostic_panel)

        self.status_label = Gtk.Label(label="Bereit", xalign=0, wrap=True)
        self.status_label.add_css_class("dim-label")
        self.append(self.status_label)
        self.progress = Gtk.ProgressBar(show_text=True, text="Vorgang läuft …", visible=False)
        self.append(self.progress)
        self._pulse_source = 0
        self.output_view = SettingsPage._text_view("Noch keine Ausgabe.", True, 400)
        self.output_view.set_editable(False)
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_child(self.output_view)
        self.append(scroll)
        self._update_timer_buttons()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        for button in (
            self.dry_button, self.run_button, self.report_button, self.enable_button,
            self.disable_button, self.diagnostic_button,
            *self.diagnostic_action_buttons.values(),
        ):
            button.set_sensitive(not busy)
        self._update_timer_buttons()
        self.cancel_button.set_sensitive(busy)
        self.progress.set_visible(busy)
        if busy and not self._pulse_source:
            self._pulse_source = GLib.timeout_add(120, self._pulse)
        elif not busy and self._pulse_source:
            GLib.source_remove(self._pulse_source)
            self._pulse_source = 0
            self.progress.set_fraction(0)

    def _pulse(self) -> bool:
        self.progress.pulse()
        return GLib.SOURCE_CONTINUE

    def set_status(self, text: str) -> None:
        self.status_label.set_text(text)

    def set_output(self, text: str) -> None:
        self.output_view.get_buffer().set_text(text or "Keine Ausgabe.")

    def set_timer_status(self, status: TimerStatus) -> None:
        self.timer_label.set_text(status.display_text())
        self._timer_available = not bool(status.error)
        self._update_timer_buttons()

    def _update_timer_buttons(self) -> None:
        available = getattr(self, "_timer_available", False) and not getattr(self, "_busy", False)
        for button in (self.enable_button, self.disable_button, self.diagnostic_action_buttons["enable_timer"]):
            button.set_sensitive(available)

    def set_check_status(self, text: str) -> None:
        self.check_label.set_text(text)

    def set_diagnostics(self, output: str) -> None:
        """Zeigt JSON-Diagnose nach Schweregrad und mit passenden Aktionen an."""
        try:
            data = json.loads(output)
            if not isinstance(data, dict):
                raise TypeError("Diagnose ist kein JSON-Objekt")
            findings = data["findings"]
            if not isinstance(findings, list):
                raise TypeError("findings ist keine Liste")
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise model_radar.RadarError(f"Diagnoseformat ist ungültig: {exc}") from exc
        if data.get("schema_version") != 1:
            raise model_radar.RadarError("Unbekannte Diagnoseschema-Version.")

        child = self.diagnostic_findings.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.diagnostic_findings.remove(child)
            child = following
        for button in self.diagnostic_action_buttons.values():
            button.set_visible(False)

        severity_labels = {"error": "FEHLER", "warning": "WARNUNG", "info": "INFO"}
        counts = {"error": 0, "warning": 0, "info": 0}
        service = data.get("service", {})
        timer = data.get("timer", {})
        if not isinstance(service, dict) or not isinstance(timer, dict):
            raise model_radar.RadarError("Dienst- oder Timerstatus hat ein ungültiges Format.")
        detail_lines = [
            "LLM-Modellradar – Dienstdiagnose", "",
            f"Dienst: {service.get('LoadState', 'unbekannt')} / "
            f"{service.get('ActiveState', 'unbekannt')} / "
            f"{service.get('Result', 'unbekannt')}",
            f"Prozessstatus: {service.get('ExecMainCode', 'unbekannt')} / "
            f"{service.get('ExecMainStatus', 'unbekannt')}",
            f"Dienstdatei: {service.get('FragmentPath', 'unbekannt')}",
            f"Timer: {timer.get('LoadState', 'unbekannt')} / "
            f"{timer.get('ActiveState', 'unbekannt')} / "
            f"{timer.get('UnitFileState', 'unbekannt')}",
            f"Nächster Timerstart: {timer.get('NextElapseUSecRealtime', 'unbekannt')}",
            f"Timerdatei: {timer.get('FragmentPath', 'unbekannt')}", "",
        ]
        requested_actions: set[str] = set()
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            severity = str(finding.get("severity", "info"))
            if severity not in counts:
                severity = "info"
            counts[severity] += 1
            title = str(finding.get("title", "Befund"))
            detail = str(finding.get("detail", ""))
            recommendation = str(finding.get("recommendation", ""))
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            heading = Gtk.Label(
                label=f"{severity_labels[severity]}: {title}", xalign=0, wrap=True,
            )
            heading.add_css_class(severity)
            row.append(heading)
            description = Gtk.Label(
                label=f"{detail}\nNächster Schritt: {recommendation}", xalign=0, wrap=True,
            )
            row.append(description)
            self.diagnostic_findings.append(row)
            detail_lines.extend([
                f"[{severity_labels[severity]}] {title}: {detail}",
                f"Nächster Schritt: {recommendation}", "",
            ])
            actions = finding.get("actions", [])
            if isinstance(actions, list):
                requested_actions.update(str(action) for action in actions)

        if not findings:
            counts["info"] = 1
            ok = Gtk.Label(
                label="INFO: Keine offensichtlichen Dienst- oder Timerfehler erkannt.",
                xalign=0, wrap=True,
            )
            ok.add_css_class("success")
            self.diagnostic_findings.append(ok)
            detail_lines.append("Keine offensichtlichen Dienst- oder Timerfehler erkannt.")

        for action in requested_actions:
            button = self.diagnostic_action_buttons.get(action)
            if button is not None:
                button.set_visible(True)
        parts = []
        if counts["error"]:
            parts.append(f"{counts['error']} Fehler")
        if counts["warning"]:
            parts.append(f"{counts['warning']} Warnung(en)")
        if not parts:
            parts.append("keine Fehler")
        self.diagnostic_title.set_text("Dienstdiagnose: " + ", ".join(parts))
        self.diagnostic_panel.set_visible(True)
        journal = str(data.get("journal") or "Keine Journalzeilen verfügbar.")
        detail_lines.extend(["Letzte Journalzeilen", "-------------------", journal])
        self.set_output("\n".join(detail_lines) + "\n")
