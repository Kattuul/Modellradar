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
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

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


class SettingsPage(Adw.PreferencesPage):
    """Gruppiertes Konfigurationsformular samt Übersetzung in die geprüfte JSON-Struktur."""

    def __init__(self, config: dict):
        super().__init__()
        self.config = config

        sources = self._group("Modellquellen", "Welche Modelle der Radar beobachtet.")
        self.publishers_view = self._text_view(logic.mapping_to_text(config["publishers"]), True, 125)
        self._text_row(sources, "Herausgeber", "Eine Zeile je Namespace: Herausgeber: Familie1, Familie2", self.publishers_view)
        self.families_view = self._text_view(logic.mapping_to_text(config["families"]), True, 100)
        self._text_row(sources, "Modellfamilien", "Eine Zeile je Familie: Anzeigename: Suchbegriff1, Suchbegriff2", self.families_view)
        self.min_spin = Gtk.SpinButton.new_with_range(0.1, 1000, 0.1)
        self.min_spin.set_value(float(config["parameter_min_b"]))
        self._widget_row(sources, "Kleinste Modellgröße", "Milliarden Parameter", self.min_spin)
        self.max_spin = Gtk.SpinButton.new_with_range(0.1, 1000, 0.1)
        self.max_spin.set_value(float(config["parameter_max_b"]))
        self._widget_row(sources, "Größte Modellgröße", "Milliarden Parameter", self.max_spin)
        self.filter_modes = ("conservative", "strict_text")
        self.filter_mode = Adw.ComboRow(
            title="Filtermodus",
            subtitle="Strikt akzeptiert nur eine der bevorzugten Text-Pipelines.",
            model=Gtk.StringList.new(["Konservativ", "Strikt: nur Textmodelle"]),
        )
        self.filter_mode.set_selected(self.filter_modes.index(config.get("filter_mode", "conservative")))
        sources.add(self.filter_mode)
        self.pipeline_entry = Adw.EntryRow(
            title="Bevorzugte Pipelines (kommagetrennt)",
            text=logic.list_to_text(config.get("preferred_pipeline_tags", [])),
        )
        sources.add(self.pipeline_entry)

        automation = self._group("Automatische Prüfungen", "Gilt, wenn automatische Prüfungen eingeschaltet sind.")
        self.interval_spin = Gtk.SpinButton.new_with_range(1, 365, 1)
        self.interval_spin.set_value(int(config.get("check_interval_days", 7)))
        self._widget_row(
            automation, "Prüfintervall", "Tage; die tägliche Hintergrundprüfung fragt nur bei Fälligkeit an.",
            self.interval_spin,
        )
        self.first_run_switch = Gtk.Switch(active=bool(config.get("notify_on_first_run", False)))
        self._widget_row(automation, "Benachrichtigung beim ersten Lauf", "", self.first_run_switch)
        self.retention_spin = Gtk.SpinButton.new_with_range(0, 36500, 1)
        self.retention_spin.set_value(int(config.get("absent_retention_days", 365)))
        self._widget_row(
            automation, "Entfernte Modelle aufbewahren",
            "Tage; 0 bewahrt abwesende Modelle unbegrenzt auf.", self.retention_spin,
        )

        network = self._group("Netzwerk", "Verhalten bei langsamen oder fehlerhaften Antworten.")
        self.timeout_spin = Gtk.SpinButton.new_with_range(1, 300, 1)
        self.timeout_spin.set_value(int(config.get("request_timeout_seconds", 20)))
        self._widget_row(network, "Zeitlimit", "Sekunden je Anfrage", self.timeout_spin)
        self.retries_spin = Gtk.SpinButton.new_with_range(1, 10, 1)
        self.retries_spin.set_value(int(config.get("request_retries", 3)))
        self._widget_row(network, "Versuche", "Anzahl je Anfrage", self.retries_spin)
        self.backoff_spin = Gtk.SpinButton.new_with_range(0.1, 60, 0.5)
        self.backoff_spin.set_value(float(config.get("retry_backoff_seconds", 2)))
        self._widget_row(network, "Wartezeit zwischen Versuchen", "Sekunden", self.backoff_spin)
        self.page_size_spin = Gtk.SpinButton.new_with_range(1, 1000, 1)
        self.page_size_spin.set_value(int(config.get("page_size", 100)))
        self._widget_row(network, "Seitengröße", "Treffer je API-Seite", self.page_size_spin)
        self.pages_spin = Gtk.SpinButton.new_with_range(1, 100, 1)
        self.pages_spin.set_value(int(config.get("max_pages_per_query", 10)))
        self._widget_row(network, "Maximale Seiten", "Je Suchanfrage", self.pages_spin)

        advanced = self._group("Erweitert", "Nur bei Bedarf ändern.")
        self.excluded_view = self._text_view(logic.list_to_text(config.get("excluded_name_terms", [])), False, 75)
        self._text_row(
            advanced, "Ausgeschlossene Namensbestandteile",
            "Kommagetrennt; Groß-/Kleinschreibung wird ignoriert.", self.excluded_view,
        )
        api_row = Adw.ActionRow(title="API-Adresse", subtitle=config["api_url"])
        api_row.set_subtitle_selectable(True)
        api_row.set_tooltip_text("Aus Sicherheitsgründen fest auf Hugging Face begrenzt.")
        describe(api_row, "API-Adresse", "Aus Sicherheitsgründen fest auf Hugging Face begrenzt.")
        advanced.add(api_row)
        self.state_entry = Adw.EntryRow(title="Zustandsdatei", text=str(config["state_file"]))
        advanced.add(self.state_entry)
        self.report_entry = Adw.EntryRow(title="Berichtsdatei", text=str(config["report_file"]))
        advanced.add(self.report_entry)

    def _group(self, title: str, description: str) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=title, description=description)
        self.add(group)
        return group

    @staticmethod
    def _widget_row(group: Adw.PreferencesGroup, title: str, subtitle: str, widget: Gtk.Widget) -> Adw.ActionRow:
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        widget.set_valign(Gtk.Align.CENTER)
        describe(widget, title, subtitle)
        row.add_suffix(widget)
        row.set_activatable_widget(widget)
        group.add(row)
        return row

    @staticmethod
    def _text_row(group: Adw.PreferencesGroup, title: str, hint: str, view: Gtk.TextView) -> None:
        """Mehrzeilige Felder brauchen eine eigene Zeile, damit sie in der Liste bleiben."""
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=6,
            margin_top=12, margin_bottom=12, margin_start=12, margin_end=12,
        )
        heading = Gtk.Label(label=title, xalign=0)
        box.append(heading)
        detail = Gtk.Label(label=hint, xalign=0, wrap=True)
        detail.add_css_class("dim-label")
        detail.add_css_class("caption")
        box.append(detail)
        # Umbrechende TextViews melden sonst eine überhöhte natürliche Höhe.
        scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True,
            min_content_height=view.get_size_request()[1],
            max_content_height=240,
            child=view,
        )
        view.set_size_request(-1, -1)
        for side in ("top", "bottom", "left", "right"):
            getattr(view, f"set_{side}_margin")(8)
        box.append(Gtk.Frame(child=scroll))
        describe(view, title, hint)
        group.add(Gtk.ListBoxRow(child=box, activatable=False))

    @staticmethod
    def _text_view(text: str = "", monospace: bool = False, min_height: int = 90) -> Gtk.TextView:
        view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, monospace=monospace)
        view.set_size_request(-1, min_height)
        view.get_buffer().set_text(text)
        return view

    @staticmethod
    def _buffer_text(view: Gtk.TextView) -> str:
        buffer = view.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)

    def collect_config(self) -> dict:
        config = dict(self.config)
        config.update({
            "api_url": self.config["api_url"],
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


def _plain(text: str) -> str:
    """Maskiert Fremdtext für Adwaita-Eigenschaften, die Pango-Markup auswerten."""
    return GLib.markup_escape_text(text)


def describe(widget: Gtk.Widget, label: str, description: str = "") -> None:
    """Setzt Name und optionale Beschreibung, die Screenreader vorlesen."""
    properties, values = [Gtk.AccessibleProperty.LABEL], [label]
    if description:
        properties.append(Gtk.AccessibleProperty.DESCRIPTION)
        values.append(description)
    widget.update_property(properties, values)


def announce(widget: Gtk.Widget, text: str, important: bool = False) -> None:
    """Liest Statuswechsel vor; erst ab GTK 4.14 möglich, ältere Systeme zeigen sie nur an."""
    if not text or not hasattr(widget, "announce"):
        return
    priority = Gtk.AccessibleAnnouncementPriority
    widget.announce(text, priority.HIGH if important else priority.MEDIUM)


def has_focus_within(widget: Gtk.Widget) -> bool:
    root = widget.get_root()
    focus = root.get_focus() if root is not None else None
    return focus is not None and (focus is widget or focus.is_ancestor(widget))


def copy_to_clipboard(widget: Gtk.Widget, text: str, label: str, notify: Callable[[str], None]) -> None:
    if not text:
        notify(f"Kein {label} zum Kopieren vorhanden.")
        return
    widget.get_clipboard().set(text)
    notify(f"{label} in die Zwischenablage kopiert.")


def _readonly_text_view(label: str) -> tuple[Gtk.TextView, Gtk.ScrolledWindow]:
    view = SettingsPage._text_view("", True, 300)
    describe(view, label)
    view.set_editable(False)
    view.set_cursor_visible(False)
    scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
    scroll.set_child(view)
    return view, scroll


def _icon_button(icon: str, tooltip: str, callback) -> Gtk.Button:
    button = Gtk.Button(icon_name=icon, tooltip_text=tooltip)
    describe(button, tooltip)
    button.connect("clicked", callback)
    return button


class NavigationHost:
    """Hierarchische Navigation: Adw.NavigationView ab libadwaita 1.4, sonst Gtk.Stack.

    Unterseiten erhalten automatisch einen Zurück-Button; mit NavigationView
    funktionieren zusätzlich Alt+←, die Zurück-Maustaste und Wischgesten.
    """

    def __init__(self, native: bool | None = None):
        supported = hasattr(Adw, "NavigationView") and hasattr(Adw, "ToolbarView")
        self.native = supported if native is None else native and supported
        if self.native:
            self.widget = Adw.NavigationView(vexpand=True)
        else:
            self.widget = Gtk.Stack(
                vexpand=True, transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT,
            )
        self._history: list[str] = []

    def add(self, tag: str, title: str, header: Adw.HeaderBar, content: Gtk.Widget,
            top_bars: Sequence[Gtk.Widget] = ()) -> None:
        if self.native:
            toolbar = Adw.ToolbarView(content=content)
            toolbar.add_top_bar(header)
            for bar in top_bars:
                toolbar.add_top_bar(bar)
            self.widget.add(Adw.NavigationPage(title=title, tag=tag, child=toolbar))
            return
        if self._history:
            header.pack_start(_icon_button("go-previous-symbolic", "Zurück", lambda *_: self.pop()))
        if header.get_title_widget() is None:
            header.set_title_widget(Adw.WindowTitle(title=title, subtitle=""))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for widget in (header, *top_bars, content):
            box.append(widget)
        self.widget.add_named(box, tag)
        if not self._history:
            self._history.append(tag)

    def push(self, tag: str) -> None:
        if self.visible_tag() == tag:
            return
        if self.native:
            self.widget.push_by_tag(tag)
            return
        self._history.append(tag)
        self._show(tag)

    def pop(self) -> None:
        if self.native:
            self.widget.pop()
        elif len(self._history) > 1:
            self._history.pop()
            self._show(self._history[-1])

    def _show(self, tag: str) -> None:
        # Gtk.Stack lässt den Fokus sonst auf der verborgenen Seite zurück.
        self.widget.set_visible_child_name(tag)
        if self.widget.get_mapped():
            self.widget.get_visible_child().child_focus(Gtk.DirectionType.TAB_FORWARD)

    def pop_to_root(self) -> None:
        while self.visible_tag() != "dashboard":
            self.pop()

    def visible_tag(self) -> str:
        if self.native:
            page = self.widget.get_visible_page()
            return page.get_tag() if page is not None else ""
        return self._history[-1] if self._history else ""


class _StatefulPage(Gtk.Box):
    """Unterseite mit eindeutigen Zuständen: leer, lädt, Ergebnis oder Fehler."""

    def __init__(self, empty_title: str, empty_text: str, loading_text: str, on_cancel):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self.stack = Gtk.Stack(vexpand=True)
        self.stack.add_named(
            Adw.StatusPage(icon_name="dialog-information-symbolic", title=empty_title, description=empty_text),
            "empty",
        )
        loading = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12,
            valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER,
        )
        self.spinner = Gtk.Spinner(width_request=32, height_request=32)
        describe(self.spinner, "Wird geladen")
        loading.append(self.spinner)
        self.loading_label = Gtk.Label(label=loading_text, wrap=True, justify=Gtk.Justification.CENTER)
        loading.append(self.loading_label)
        cancel = Gtk.Button(label="_Abbrechen", use_underline=True, halign=Gtk.Align.CENTER)
        cancel.connect("clicked", on_cancel)
        loading.append(cancel)
        self.stack.add_named(loading, "loading")
        self.error_page = Adw.StatusPage(icon_name="dialog-warning-symbolic", title="Das hat nicht geklappt")
        error_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.error_details = Gtk.Expander(label="_Technische Details anzeigen", use_underline=True)
        self.error_label = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.error_details.set_child(self.error_label)
        error_box.append(self.error_details)
        self.error_page.set_child(Adw.Clamp(maximum_size=600, child=error_box))
        self.stack.add_named(self.error_page, "error")
        self.append(self.stack)

    def add_result(self, widget: Gtk.Widget) -> None:
        self.stack.add_named(widget, "result")

    def state(self) -> str:
        return self.stack.get_visible_child_name()

    def _switch(self, state: str) -> None:
        """Wechselt den Zustand, ohne den Tastaturfokus im Nichts zu verlieren."""
        root = self.get_root()
        focus = root.get_focus() if root is not None else None
        # Ein deaktivierter Kopfleisten-Button verliert den Fokus bereits beim Start.
        follow = self.get_mapped() and (focus is None or has_focus_within(self.stack))
        self.stack.set_visible_child_name(state)
        if follow:
            self.stack.get_visible_child().child_focus(Gtk.DirectionType.TAB_FORWARD)

    def set_loading(self) -> None:
        self.spinner.start()
        self._switch("loading")
        announce(self, self.loading_label.get_text())

    def show_result(self) -> None:
        self.spinner.stop()
        self._switch("result")

    def restore(self, state: str) -> None:
        """Kehrt nach einem Abbruch zum vorher sichtbaren Zustand zurück."""
        self.spinner.stop()
        self._switch(state)

    def set_error(self, summary: str, details: str) -> None:
        self.spinner.stop()
        self.error_page.set_description(_plain(summary))
        self.error_label.set_text(details.strip() or "Keine technischen Details verfügbar.")
        self.error_details.set_expanded(False)
        self._switch("error")
        announce(self, f"Das hat nicht geklappt. {summary}", important=True)


class DashboardPage(Gtk.ScrolledWindow):
    """Übersicht: eine Hauptaktion, Status und Wege zu den Unteransichten."""

    def __init__(
        self, on_force_run, on_cancel, on_open_report, on_open_hardware,
        on_enable_timer, on_disable_timer, on_diagnostics,
    ):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=14,
            margin_top=24, margin_bottom=24, margin_start=24, margin_end=24,
        )
        self.set_child(Adw.Clamp(maximum_size=720, child=box))
        heading = Gtk.Label(label="Modellprüfung", xalign=0)
        heading.add_css_class("title-2")
        box.append(heading)
        box.append(Gtk.Label(
            label="Findet neue und geänderte Sprachmodelle anhand öffentlicher Metadaten. "
            "Es werden keine Modelle heruntergeladen oder ausgeführt.",
            xalign=0, wrap=True,
        ))
        self.check_label = Gtk.Label(label="API-Prüfstatus wird geladen …", xalign=0, wrap=True)
        self.check_label.add_css_class("dim-label")
        box.append(self.check_label)

        button_box = Gtk.Box(spacing=10)
        self.run_button = Gtk.Button(label="_Jetzt prüfen", use_underline=True)
        self.run_button.add_css_class("suggested-action")
        self.run_button.add_css_class("pill")
        self.run_button.set_tooltip_text("Startet eine reguläre Prüfung und aktualisiert danach den Bericht.")
        self.run_button.connect("clicked", on_force_run)
        self.cancel_button = Gtk.Button(label="Lauf _abbrechen", use_underline=True, sensitive=False, visible=False)
        self.cancel_button.add_css_class("pill")
        self.cancel_button.set_tooltip_text("Fordert den Abbruch der aktuell laufenden Prüfung an.")
        self.cancel_button.connect("clicked", on_cancel)
        button_box.append(self.run_button)
        button_box.append(self.cancel_button)
        box.append(button_box)

        self.progress = Gtk.ProgressBar(show_text=True, text="Vorgang läuft …", visible=False)
        describe(self.progress, "Fortschritt")
        box.append(self.progress)
        self._pulse_source = 0
        self.status_label = Gtk.Label(label="Bereit", xalign=0, wrap=True)
        self.status_label.add_css_class("dim-label")
        box.append(self.status_label)
        self.error_details = Gtk.Expander(label="_Technische Details anzeigen", use_underline=True, visible=False)
        error_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.error_label = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.error_label.add_css_class("error")
        error_box.append(self.error_label)
        # Bei Fehlern führt der nächste Schritt direkt zur Diagnose.
        self.diagnose_button = Gtk.Button(label="Dienst_diagnose öffnen", use_underline=True, halign=Gtk.Align.START)
        self.diagnose_button.connect("clicked", on_diagnostics)
        error_box.append(self.diagnose_button)
        self.error_details.set_child(error_box)
        box.append(self.error_details)

        overview = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, margin_top=10)
        overview.add_css_class("boxed-list")
        self._on_enable_timer = on_enable_timer
        self._on_disable_timer = on_disable_timer
        self._syncing_timer = False
        self._timer_available = False
        self._busy = False
        self.timer_switch = Gtk.Switch(valign=Gtk.Align.CENTER, sensitive=False)
        timer_hint = "Einschalten fragt nach, weil regelmäßige Netzwerkprüfungen im Hintergrund starten."
        self.timer_switch.set_tooltip_text(timer_hint)
        describe(self.timer_switch, "Automatische Prüfungen", timer_hint)
        self.timer_switch.connect("state-set", self._on_timer_switch)
        self.timer_row = Adw.ActionRow(
            title="A_utomatische Prüfungen", use_underline=True, subtitle="Status wird geladen …",
        )
        self.timer_row.add_suffix(self.timer_switch)
        self.timer_row.set_activatable_widget(self.timer_switch)
        overview.append(self.timer_row)
        self.report_row = self._navigation_row(
            "_Letztes Ergebnis", "Noch keine Prüfung durchgeführt – „Jetzt prüfen“ startet die erste.",
            on_open_report,
        )
        self.report_row.set_sensitive(False)
        overview.append(self.report_row)
        self.hardware_row = self._navigation_row(
            "_Passend zu diesem Rechner", "Grafikkarte wird ermittelt …", on_open_hardware,
        )
        overview.append(self.hardware_row)
        box.append(overview)
        self._update_timer_switch()

    @staticmethod
    def _navigation_row(title: str, subtitle: str, callback) -> Adw.ActionRow:
        row = Adw.ActionRow(title=title, subtitle=subtitle, activatable=True, subtitle_lines=0, use_underline=True)
        row.add_suffix(Gtk.Image(icon_name="go-next-symbolic", accessible_role=Gtk.AccessibleRole.PRESENTATION))
        row.connect("activated", callback)
        return row

    def set_busy(self, busy: bool) -> None:
        # Der Fokus folgt der jeweils verfügbaren Aktion, statt mit dem Button zu verschwinden.
        move_focus = has_focus_within(self.run_button if busy else self.cancel_button)
        self._busy = busy
        self.run_button.set_sensitive(not busy)
        self.diagnose_button.set_sensitive(not busy)
        self._update_timer_switch()
        self.cancel_button.set_sensitive(busy)
        self.cancel_button.set_visible(busy)
        self.progress.set_visible(busy)
        if busy and not self._pulse_source:
            self._pulse_source = GLib.timeout_add(120, self._pulse)
        elif not busy and self._pulse_source:
            GLib.source_remove(self._pulse_source)
            self._pulse_source = 0
            self.progress.set_fraction(0)
        if move_focus:
            (self.cancel_button if busy else self.run_button).grab_focus()

    def _pulse(self) -> bool:
        self.progress.pulse()
        return GLib.SOURCE_CONTINUE

    def set_status(self, text: str) -> None:
        self.status_label.set_text(text)
        announce(self.status_label, text)

    def set_error(self, summary: str, details: str) -> None:
        """Zeigt eine verständliche Meldung; Rohdetails bleiben einklappbar."""
        self.status_label.set_text(summary)
        announce(self.status_label, summary, important=True)
        self.error_label.set_text(details.strip() or "Keine technischen Details verfügbar.")
        self.error_details.set_expanded(False)
        self.error_details.set_visible(True)

    def clear_error(self) -> None:
        self.error_details.set_visible(False)

    def set_report_summary(self, summary: dict[str, str]) -> None:
        self.report_row.set_subtitle(_plain(
            "Letzte Prüfung: {checked_at}\nNeu: {new} · Aktualisiert: {updated} · "
            "Entfernt: {removed} · Wieder erschienen: {returned}".format(**summary)
        ))
        self.report_row.set_sensitive(True)

    def set_graphics(self, title: str) -> None:
        self.hardware_row.set_subtitle(_plain(f"{title} · Welche Modelldateien passen?"))

    def _on_timer_switch(self, switch: Gtk.Switch, state: bool) -> bool:
        if self._syncing_timer:
            return False
        (self._on_enable_timer if state else self._on_disable_timer)(switch)
        # Der Schalter folgt erst dem tatsächlich gemeldeten Timerstatus.
        return True

    def set_timer_status(self, status: TimerStatus) -> None:
        enabled = not status.error and status.enabled == "enabled"
        if status.error:
            subtitle = "Nicht verfügbar · Manuelle Prüfungen sind weiterhin möglich."
        elif enabled:
            subtitle = "Eingeschaltet" + (f" · nächster Start: {status.next_run}" if status.next_run else "")
        else:
            subtitle = "Ausgeschaltet · Prüfungen nur manuell"
        self.timer_row.set_subtitle(_plain(subtitle))
        self.timer_row.set_tooltip_text(status.display_text())
        self._syncing_timer = True
        try:
            self.timer_switch.set_active(enabled)
            self.timer_switch.set_state(enabled)
        finally:
            self._syncing_timer = False
        self._timer_available = not bool(status.error)
        self._update_timer_switch()

    def _update_timer_switch(self) -> None:
        self.timer_switch.set_sensitive(self._timer_available and not self._busy)

    def set_check_status(self, text: str) -> None:
        self.check_label.set_text(text)


class ReportPage(_StatefulPage):
    """Vollständiger Bericht mit Kurzbilanz; zeigt auf Wunsch auch eine Vorschau."""

    def __init__(self, on_cancel, notify: Callable[[str], None]):
        super().__init__(
            "Noch kein Bericht", "Nach der ersten Prüfung steht der Bericht hier.",
            "Vorschau wird erstellt …", on_cancel,
        )
        self.notify = notify
        self.report_text = ""
        self.shown_text = ""
        self.copy_button = _icon_button(
            "edit-copy-symbolic", "Bericht kopieren",
            lambda *_: copy_to_clipboard(self, self.shown_text, "Bericht", self.notify),
        )
        self.copy_button.set_sensitive(False)
        result = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                         margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        self.preview_label = Gtk.Label(
            label="Vorschau ohne Änderungen: Dieses Ergebnis wurde nicht gespeichert.",
            xalign=0, wrap=True, visible=False,
        )
        self.preview_label.add_css_class("warning")
        result.append(self.preview_label)
        self.summary_label = Gtk.Label(xalign=0, wrap=True)
        self.summary_label.add_css_class("heading")
        result.append(self.summary_label)
        self.text_view, scroll = _readonly_text_view("Berichtstext")
        result.append(scroll)
        self.add_result(result)

    def _show(self, text: str, summary: dict[str, str], preview: bool) -> None:
        self.shown_text = text
        self.preview_label.set_visible(preview)
        self.summary_label.set_text(
            "Passende Repositories: {repositories} · Änderungen: {changes}\n"
            "Neu: {new} · Aktualisiert: {updated} · Entfernt: {removed} · "
            "Wieder erschienen: {returned}".format(**summary)
        )
        self.text_view.get_buffer().set_text(text)
        self.copy_button.set_sensitive(bool(text))
        self.show_result()

    def set_report(self, report: str, summary: dict[str, str]) -> None:
        self.report_text = report
        self._show(report, summary, preview=False)

    def set_preview(self, text: str, summary: dict[str, str]) -> None:
        self._show(text, summary, preview=True)

    def show_saved(self) -> None:
        """Kehrt nach einer Vorschau zum gespeicherten Bericht zurück."""
        if self.report_text:
            self._show(self.report_text, logic.report_summary(self.report_text), preview=False)

    def text(self) -> str:
        buffer = self.text_view.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)


class HardwarePage(_StatefulPage):
    """Hardware-Eignung: welche Modelldateien voraussichtlich in den Grafikspeicher passen."""

    def __init__(self, on_cancel, on_refresh, notify: Callable[[str], None]):
        super().__init__(
            "Noch nicht berechnet", "„Neu berechnen“ vergleicht den Grafikspeicher mit den Modelldateien.",
            "Öffentliche Modellangaben werden abgerufen und mit diesem Rechner verglichen …", on_cancel,
        )
        self.notify = notify
        self.result_text = ""
        self.copy_button = _icon_button(
            "edit-copy-symbolic", "Ergebnis kopieren",
            lambda *_: copy_to_clipboard(self, self.result_text, "Ergebnis", self.notify),
        )
        self.copy_button.set_sensitive(False)
        self.refresh_button = _icon_button("view-refresh-symbolic", "Neu berechnen", on_refresh)
        intro = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                        margin_top=12, margin_start=12, margin_end=12)
        intro.append(Gtk.Label(
            label="Vergleicht den sichtbaren Grafikspeicher mit den öffentlichen Größenangaben der "
            "Modelldateien. Es wird nichts heruntergeladen; das Ergebnis ist ein vorsichtiger Richtwert.",
            xalign=0, wrap=True,
        ))
        graphics_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        graphics_list.add_css_class("boxed-list")
        self.graphics_row = Adw.ActionRow(
            title="Grafikkarte wird ermittelt …", subtitle="Das dauert nur einen Moment.", subtitle_lines=0,
        )
        self.graphics_row.add_prefix(Gtk.Image(
            icon_name="video-display-symbolic", accessible_role=Gtk.AccessibleRole.PRESENTATION,
        ))
        graphics_list.append(self.graphics_row)
        intro.append(graphics_list)
        self.prepend(intro)
        self.text_view, scroll = _readonly_text_view("Ergebnis der Hardware-Eignung")
        scroll.set_margin_top(12)
        scroll.set_margin_bottom(12)
        scroll.set_margin_start(12)
        scroll.set_margin_end(12)
        self.add_result(scroll)

    def set_graphics(self, title: str, detail: str) -> None:
        self.graphics_row.set_title(_plain(title))
        self.graphics_row.set_subtitle(_plain(detail))

    def set_busy(self, busy: bool) -> None:
        self.refresh_button.set_sensitive(not busy)

    def set_result(self, text: str) -> None:
        self.result_text = text
        self.text_view.get_buffer().set_text(text or "Keine Ausgabe.")
        self.copy_button.set_sensitive(bool(text))
        self.show_result()
        announce(self, "Hardware-Eignung berechnet.")


class DiagnosticsPage(_StatefulPage):
    """Dienstdiagnose: Befunde nach Schweregrad, passende Aktionen, Rohdaten einklappbar."""

    def __init__(self, on_cancel, on_enable_timer, on_open_settings, on_run_check, on_retry,
                 notify: Callable[[str], None]):
        super().__init__(
            "Noch keine Diagnose", "Die Diagnose prüft Dienst, Timer und sichtbare Grafikhardware.",
            "Dienst und Timer werden überprüft …", on_cancel,
        )
        self.notify = notify
        self.diagnostic_text = ""
        self.copy_button = _icon_button(
            "edit-copy-symbolic", "Diagnose kopieren",
            lambda *_: copy_to_clipboard(self, self.diagnostic_text, "Diagnose", self.notify),
        )
        self.copy_button.set_sensitive(False)
        self.refresh_button = _icon_button("view-refresh-symbolic", "Diagnose wiederholen", on_retry)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                          margin_top=18, margin_bottom=18, margin_start=18, margin_end=18)
        self.diagnostic_title = Gtk.Label(label="Dienstdiagnose", xalign=0, wrap=True)
        self.diagnostic_title.add_css_class("title-3")
        content.append(self.diagnostic_title)
        self.diagnostic_findings = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.append(self.diagnostic_findings)
        actions = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=8, row_spacing=8)
        self.diagnostic_action_buttons = {
            "enable_timer": Gtk.Button(label="Automatische Prüfungen _einschalten", use_underline=True),
            "open_settings": Gtk.Button(label="_Konfiguration prüfen", use_underline=True),
            "run_check": Gtk.Button(label="Prüfung erneut _starten", use_underline=True),
            "retry_diagnostics": Gtk.Button(label="Diagnose _wiederholen", use_underline=True),
        }
        callbacks = {
            "enable_timer": on_enable_timer,
            "open_settings": on_open_settings,
            "run_check": on_run_check,
            "retry_diagnostics": on_retry,
        }
        for action, button in self.diagnostic_action_buttons.items():
            button.connect("clicked", callbacks[action])
            button.set_visible(False)
            actions.append(button)
        content.append(actions)
        self.details = Gtk.Expander(label="_Technische Details anzeigen", use_underline=True)
        self.text_view, scroll = _readonly_text_view("Technische Diagnosedaten")
        scroll.set_min_content_height(240)
        self.details.set_child(scroll)
        content.append(self.details)
        result = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        result.set_child(Adw.Clamp(maximum_size=760, child=content))
        self.add_result(result)
        self._timer_available = False
        self._busy = False

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.refresh_button.set_sensitive(not busy)
        for button in self.diagnostic_action_buttons.values():
            button.set_sensitive(not busy)
        self.set_timer_available(self._timer_available)

    def set_timer_available(self, available: bool) -> None:
        self._timer_available = available
        self.diagnostic_action_buttons["enable_timer"].set_sensitive(available and not self._busy)


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
        graphics = data.get("graphics", {})
        if isinstance(graphics, dict):
            detail_lines.extend([
                "Grafikhardware (sichtbar für diesen Prozess)",
                "---------------------------------------------",
            ])
            adapters = graphics.get("adapters", [])
            if isinstance(adapters, list) and adapters:
                for adapter in adapters:
                    if not isinstance(adapter, dict):
                        continue
                    vram = adapter.get("vram_bytes")
                    vram_text = f"{int(vram) / (1024 ** 3):.1f} GiB VRAM" if isinstance(vram, int) else "VRAM unbekannt"
                    detail_lines.append(
                        f"- {adapter.get('card', 'GPU')}: {adapter.get('vendor', 'unbekannt')} "
                        f"({adapter.get('kind', 'unbekannt')}, Treiber: {adapter.get('driver', 'unbekannt')}, {vram_text})"
                    )
            else:
                detail_lines.append("- Keine sichtbaren Grafikadapter.")
            limitations = graphics.get("limitations", [])
            if isinstance(limitations, list):
                detail_lines.extend(f"  Hinweis: {item}" for item in limitations)
            detail_lines.append("")
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
        journal = str(data.get("journal") or "Keine Journalzeilen verfügbar.")
        detail_lines.extend(["Letzte Journalzeilen", "-------------------", journal])
        self.diagnostic_text = "\n".join(detail_lines) + "\n"
        self.copy_button.set_sensitive(True)
        self.text_view.get_buffer().set_text(self.diagnostic_text)
        self.details.set_expanded(False)
        self.show_result()
        announce(self, self.diagnostic_title.get_text(), important=bool(counts["error"]))



class OnboardingView(Gtk.Box):
    """Kurze, jederzeit überspringbare Einführung in drei Schritten.

    Der Inhalt ist bewusst unabhängig vom umgebenden Dialog, damit er unter
    neuen (Adw.Dialog) und älteren libadwaita-Versionen (Adw.Window) gleich
    funktioniert und sich ohne Fenster testen lässt.
    """

    STEP_TITLES = ("Willkommen", "Dieser Rechner", "Los geht’s")

    def __init__(self, on_finish: Callable[[bool], None]):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._on_finish = on_finish
        self._finished = False
        self._page_texts: list[str] = []
        self._status_pages: list[tuple[Adw.StatusPage, str]] = []

        self.carousel = Adw.Carousel(vexpand=True, allow_scroll_wheel=False)
        self.carousel.append(self._welcome_page())
        self.carousel.append(self._hardware_page())
        self.carousel.append(self._start_page())
        self.carousel.connect("page-changed", self._on_page_changed)
        self.append(self.carousel)

        navigation = Gtk.CenterBox(margin_top=6, margin_bottom=18, margin_start=18, margin_end=18)
        self.back_button = Gtk.Button(label="_Zurück", use_underline=True)
        self.back_button.connect("clicked", lambda *_: self.go_to(self.position() - 1))
        self.next_button = Gtk.Button(label="_Weiter", use_underline=True)
        self.next_button.add_css_class("suggested-action")
        self.next_button.connect("clicked", lambda *_: self.go_to(self.position() + 1))
        navigation.set_start_widget(self.back_button)
        # Die Punkte sind rein optisch; den Schritt nennt die Titelzeile auch in Worten.
        self.dots = Adw.CarouselIndicatorDots(
            carousel=self.carousel, accessible_role=Gtk.AccessibleRole.PRESENTATION,
        )
        navigation.set_center_widget(self.dots)
        # Die Hauptaktion der letzten Seite sitzt in der festen Leiste statt im scrollbaren Inhalt,
        # damit sie auch bei 1024×600 ohne Scrollen sichtbar bleibt.
        self.start_button = Gtk.Button(label="_Erste Prüfung starten", use_underline=True)
        self.start_button.add_css_class("suggested-action")
        self.start_button.connect("clicked", lambda *_: self.finish(True))
        end = Gtk.Box()
        end.append(self.next_button)
        end.append(self.start_button)
        navigation.set_end_widget(end)
        self.append(navigation)

        self.step_label = Gtk.Label()
        self.step_label.add_css_class("dim-label")
        self.skip_button = Gtk.Button(label="Ü_berspringen", use_underline=True)
        self.skip_button.set_tooltip_text("Schließt die Einführung. Sie bleibt über das Hauptmenü erreichbar.")
        self.skip_button.connect("clicked", lambda *_: self.finish(False))
        self._update_navigation()

    def _page(self, icon: str, title: str, description: str, *children: Gtk.Widget) -> Gtk.Widget:
        # Adw.StatusPage scrollt selbst; das hält auch kleine Bildschirme bedienbar.
        page = Adw.StatusPage(
            icon_name=icon, title=title, description=description, hexpand=True, vexpand=True,
        )
        page.add_css_class("compact")
        self._page_texts.append(f"{title}. {description}")
        self._status_pages.append((page, icon))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for child in children:
            box.append(child)
        page.set_child(Adw.Clamp(maximum_size=480, child=box))
        return page

    @staticmethod
    def _fact_list(*facts: tuple[str, str, str]) -> tuple[Gtk.ListBox, list[Adw.ActionRow]]:
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")
        rows = []
        for icon, title, subtitle in facts:
            row = Adw.ActionRow(title=title, subtitle=subtitle, subtitle_lines=0)
            row.add_prefix(Gtk.Image(icon_name=icon, accessible_role=Gtk.AccessibleRole.PRESENTATION))
            listbox.append(row)
            rows.append(row)
        return listbox, rows

    def _welcome_page(self) -> Gtk.Widget:
        facts, _rows = self._fact_list(
            ("folder-download-symbolic", "Lädt keine Modelle herunter",
             "Gelesen werden nur öffentliche Steckbriefe der Modelle (Metadaten), "
             "zum Beispiel Name, Größe und Änderungsdatum."),
            ("system-run-symbolic", "Führt nichts aus",
             "Auf diesem Rechner wird kein Modell installiert oder gestartet."),
            ("object-select-symbolic", "Fragt nur ausgewählte Quellen ab",
             "Welche Herausgeber berücksichtigt werden, lässt sich später in den Einstellungen ändern."),
        )
        return self._page(
            "system-search-symbolic", "Willkommen bei Modellradar",
            "Modellradar zeigt, welche KI-Sprachmodelle neu erschienen sind oder aktualisiert wurden – "
            "ohne dass man dafür selbst Webseiten durchsuchen muss.",
            facts,
        )

    def _hardware_page(self) -> Gtk.Widget:
        facts, rows = self._fact_list(
            ("video-display-symbolic", "Grafikkarte wird ermittelt …",
             "Das dauert nur einen Moment."),
            ("alarm-symbolic", "Automatische Prüfungen", "Status wird geladen …"),
        )
        self.hardware_row, self.timer_row = rows
        return self._page(
            "computer-symbolic", "Dieser Rechner",
            "Ob ein Modell hier laufen könnte, hängt vor allem vom Grafikspeicher (VRAM) "
            "der Grafikkarte ab. Diese Angabe wird nur lokal gelesen und nicht übertragen.",
            facts,
        )

    def _start_page(self) -> Gtk.Widget:
        steps, _rows = self._fact_list(
            ("media-playback-start-symbolic", "1. „Jetzt prüfen“ wählen",
             "Modellradar fragt die Modellquellen ab. Dafür wird eine Internetverbindung benötigt."),
            ("x-office-document-symbolic", "2. Ergebnis lesen",
             "Neue und geänderte Modelle erscheinen im Hauptfenster unter „Letztes Ergebnis“."),
            ("computer-symbolic", "3. Optional: „Hardware-Eignung“",
             "Zeigt, welche Modelldateien voraussichtlich in den Grafikspeicher passen."),
        )
        self.look_around_button = Gtk.Button(
            label="Erst einmal _umsehen", use_underline=True, halign=Gtk.Align.CENTER,
        )
        self.look_around_button.add_css_class("flat")
        self.look_around_button.connect("clicked", lambda *_: self.finish(False))
        return self._page(
            "media-playback-start-symbolic", "So geht es los",
            "Drei Schritte genügen für den Einstieg. Diese Einführung lässt sich "
            "jederzeit über das Hauptmenü (☰) erneut öffnen.",
            steps, self.look_around_button,
        )

    def set_hardware(self, title: str, detail: str) -> None:
        self.hardware_row.set_title(_plain(title))
        self.hardware_row.set_subtitle(_plain(detail))

    def set_timer_state(self, enabled: bool, available: bool) -> None:
        """Beschreibt den tatsächlichen Timerzustand, auch beim erneuten Aufruf."""
        if not available:
            title = "Automatische Prüfungen sind hier nicht verfügbar"
            subtitle = "Prüfungen lassen sich trotzdem jederzeit mit „Jetzt prüfen“ starten."
        elif enabled:
            title = "Automatische Prüfungen sind eingeschaltet"
            subtitle = ("Modellradar prüft regelmäßig im Hintergrund. "
                        "Das lässt sich im Hauptfenster jederzeit ausschalten.")
        else:
            title = "Automatische Prüfungen sind ausgeschaltet"
            subtitle = ("Geprüft wird nur, wenn „Jetzt prüfen“ gewählt wird. Regelmäßige Prüfungen im "
                        "Hintergrund lassen sich im Hauptfenster einschalten – Modellradar fragt vorher nach.")
        self.timer_row.set_title(title)
        self.timer_row.set_subtitle(subtitle)

    def set_layout(self, compact: bool, narrow: bool) -> None:
        """Passt die Einführung an kleine Bildschirme wie 1024×600 an.

        Niedrig: Die großen Symbole entfallen, sie sind reine Dekoration.
        Schmal: Die Seitenpunkte entfallen; den Schritt nennt weiter die Titelzeile.
        """
        for page, icon in self._status_pages:
            page.set_icon_name(None if compact else icon)
        self.dots.set_visible(not narrow)

    def position(self) -> int:
        return round(self.carousel.get_position())

    def go_to(self, index: int) -> None:
        index = max(0, min(index, self.carousel.get_n_pages() - 1))
        self.carousel.scroll_to(self.carousel.get_nth_page(index), True)
        self._update_navigation(index)

    def _on_page_changed(self, _carousel, index: int) -> None:
        self._update_navigation()
        # Der Fokus landet auf „Weiter“; ohne Ansage bliebe der neue Seiteninhalt ungelesen.
        if self.get_mapped():
            announce(self, f"{self.step_label.get_text()}. {self._page_texts[index]}")

    def _update_navigation(self, index: int | None = None) -> None:
        index = self.position() if index is None else index
        last = self.carousel.get_n_pages() - 1
        self.back_button.set_visible(index > 0)
        self.next_button.set_visible(index < last)
        self.start_button.set_visible(index == last)
        self.step_label.set_text(f"Schritt {index + 1} von {last + 1}: {self.STEP_TITLES[index]}")
        (self.start_button if index == last else self.next_button).grab_focus()

    def finish(self, start_check: bool) -> None:
        """Meldet das Ende genau einmal – egal ob per Button, Überspringen oder Schließen."""
        if self._finished:
            return
        self._finished = True
        self._on_finish(start_check)
