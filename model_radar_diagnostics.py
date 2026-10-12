#!/usr/bin/env python3
"""Strukturierte, rein lokale systemd-Diagnose für den Modellradar."""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from typing import Callable, Sequence

import model_radar
import radar_platform


SERVICE = radar_platform.SERVICE
TIMER = radar_platform.TIMER


@dataclass(frozen=True)
class DiagnosticCommandResult:
    output: str
    error: str = ""


def _run(arguments: Sequence[str], executor: Callable | None = None) -> DiagnosticCommandResult:
    run = executor or subprocess.run
    try:
        result = run(
            list(arguments), text=True, capture_output=True, timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return DiagnosticCommandResult("", str(exc))
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    if result.returncode:
        return DiagnosticCommandResult(stdout, stderr or f"Exitcode {result.returncode}")
    return DiagnosticCommandResult(stdout)


def _properties(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def collect_diagnostic_data(executor: Callable | None = None) -> dict:
    """Sammelt maschinenlesbare Statusfelder, Befunde und Empfehlungen."""
    if executor is None:
        status = radar_platform.SystemdScheduler.query_status()
        if status.error:
            return {
                "schema_version": 1, "program_version": model_radar.VERSION,
                "service": {}, "timer": {}, "journal": "Kein Dienstjournal verfügbar.",
                "findings": [{
                    "severity": "info", "title": "Automatische Prüfung nicht verfügbar",
                    "detail": status.error,
                    "recommendation": "Prüfungen und Berichte sind weiterhin über die GUI oder CLI verfügbar.",
                    "actions": ["run_check"],
                }],
            }
    service_result = _run([
        "systemctl", "--user", "show", SERVICE,
        "--property=LoadState,ActiveState,SubState,Result,ExecMainCode,ExecMainStatus,FragmentPath",
    ], executor)
    timer_result = _run([
        "systemctl", "--user", "show", TIMER,
        "--property=LoadState,ActiveState,SubState,UnitFileState,LastTriggerUSec,NextElapseUSecRealtime,FragmentPath",
    ], executor)
    journal_result = _run([
        "journalctl", "--user", "-u", SERVICE, "-n", "30", "--no-pager",
    ], executor)

    service = _properties(service_result.output)
    timer = _properties(timer_result.output)
    findings: list[dict[str, str | list[str]]] = []
    if service_result.error:
        findings.append({
            "severity": "error",
            "title": "Dienststatus nicht lesbar",
            "detail": service_result.error,
            "recommendation": "Benutzersitzung und systemd-Verbindung prüfen, dann die Diagnose wiederholen.",
            "actions": ["retry_diagnostics"],
        })
    elif service.get("LoadState") != "loaded":
        findings.append({
            "severity": "error",
            "title": "Dienst ist nicht geladen",
            "detail": service.get("LoadState") or "unbekannt",
            "recommendation": "Installation prüfen und anschließend die Diagnose wiederholen.",
            "actions": ["retry_diagnostics"],
        })
    elif service.get("Result") not in ("", "success"):
        findings.append({
            "severity": "error",
            "title": "Letzter Dienstlauf fehlgeschlagen",
            "detail": (
                f"{service.get('Result')} / Exitstatus "
                f"{service.get('ExecMainStatus') or 'unbekannt'}"
            ),
            "recommendation": "Konfiguration prüfen und die Modellprüfung erneut starten.",
            "actions": ["open_settings", "run_check"],
        })
    if timer_result.error:
        findings.append({
            "severity": "error",
            "title": "Timerstatus nicht lesbar",
            "detail": timer_result.error,
            "recommendation": "Benutzersitzung und systemd-Verbindung prüfen, dann die Diagnose wiederholen.",
            "actions": ["retry_diagnostics"],
        })
    elif timer.get("ActiveState") != "active":
        findings.append({
            "severity": "warning",
            "title": "Timer ist nicht aktiv",
            "detail": timer.get("ActiveState") or "nicht aktiv",
            "recommendation": "Timer aktivieren, damit Prüfungen wieder automatisch ausgeführt werden.",
            "actions": ["enable_timer"],
        })
    if journal_result.error:
        findings.append({
            "severity": "warning",
            "title": "Journal nicht lesbar",
            "detail": journal_result.error,
            "recommendation": "Journalzugriff prüfen und die Diagnose wiederholen.",
            "actions": ["retry_diagnostics"],
        })

    return {
        "schema_version": 1,
        "program_version": model_radar.VERSION,
        "service": service,
        "timer": timer,
        "findings": findings,
        "journal": journal_result.output or "Keine Journalzeilen verfügbar.",
    }


def render_diagnostics(data: dict) -> str:
    """Rendert die strukturierte Diagnose für Terminal und Detailansicht."""
    service = data.get("service", {})
    timer = data.get("timer", {})
    findings = data.get("findings", [])

    lines = [
        "LLM-Modellradar – strukturierte Dienstdiagnose",
        "=" * 45,
        f"Programmversion: {data.get('program_version', 'unbekannt')}",
        "",
        "Dienst",
        "------",
        f"Ladezustand: {service.get('LoadState', 'unbekannt')}",
        f"Aktivität: {service.get('ActiveState', 'unbekannt')} / {service.get('SubState', 'unbekannt')}",
        f"Letztes Ergebnis: {service.get('Result', 'unbekannt')}",
        f"Prozessstatus: {service.get('ExecMainCode', 'unbekannt')} / {service.get('ExecMainStatus', 'unbekannt')}",
        f"Unit-Datei: {service.get('FragmentPath', 'unbekannt')}",
        "",
        "Timer",
        "-----",
        f"Ladezustand: {timer.get('LoadState', 'unbekannt')}",
        f"Aktivität: {timer.get('ActiveState', 'unbekannt')} / {timer.get('SubState', 'unbekannt')}",
        f"Aktivierung: {timer.get('UnitFileState', 'unbekannt')}",
        f"Letzter Start: {timer.get('LastTriggerUSec', 'unbekannt')}",
        f"Nächster Start: {timer.get('NextElapseUSecRealtime', 'unbekannt')}",
        f"Unit-Datei: {timer.get('FragmentPath', 'unbekannt')}",
        "",
        "Bewertung",
        "---------",
    ]
    for finding in findings:
        severity = str(finding.get("severity", "info")).upper()
        lines.append(f"- [{severity}] {finding.get('title', 'Befund')}: {finding.get('detail', '')}")
        lines.append(f"  Nächster Schritt: {finding.get('recommendation', 'Keine Empfehlung verfügbar.')}")
    if not findings:
        lines.append("Keine offensichtlichen Dienst- oder Timerfehler erkannt.")
    lines.extend([
        "",
        "Letzte 30 Journalzeilen",
        "-----------------------",
        str(data.get("journal") or "Keine Journalzeilen verfügbar."),
    ])
    return "\n".join(lines) + "\n"


def collect_diagnostics(executor: Callable | None = None) -> str:
    """Sammelt und rendert eine menschenlesbare Diagnose."""
    return render_diagnostics(collect_diagnostic_data(executor))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnose für den LLM-Modellradar")
    parser.add_argument("--json", action="store_true", help="maschinenlesbare Diagnose ausgeben")
    args = parser.parse_args(argv)
    data = collect_diagnostic_data()
    if args.json:
        print(json.dumps(data, ensure_ascii=False))
    else:
        print(render_diagnostics(data), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
