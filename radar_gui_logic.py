# SPDX-License-Identifier: GPL-3.0-or-later
"""GUI-unabhängige Konfigurationslogik für den LLM-Modellradar."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import model_radar
import radar_platform


def mapping_to_text(mapping: dict[str, list[str]]) -> str:
    return "\n".join(f"{key}: {', '.join(values)}" for key, values in mapping.items())


def text_to_mapping(text: str, label: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        if ":" not in line:
            raise model_radar.RadarError(f"{label}, Zeile {line_number}: Doppelpunkt fehlt.")
        key, raw_values = line.split(":", 1)
        key = key.strip()
        values = [item.strip() for item in raw_values.split(",") if item.strip()]
        if not key or not values:
            raise model_radar.RadarError(f"{label}, Zeile {line_number}: Name oder Wert fehlt.")
        if key in result:
            raise model_radar.RadarError(f"{label}: {key!r} ist doppelt vorhanden.")
        result[key] = values
    if not result:
        raise model_radar.RadarError(f"{label} darf nicht leer sein.")
    return result


def list_to_text(values: list[str]) -> str:
    return ", ".join(values)


def text_to_list(text: str, label: str, allow_empty: bool = False) -> list[str]:
    values = [item.strip() for item in text.replace("\n", ",").split(",") if item.strip()]
    if not values and not allow_empty:
        raise model_radar.RadarError(f"{label} darf nicht leer sein.")
    return list(dict.fromkeys(values))


def save_config(path: Path, config: dict[str, Any]) -> None:
    validated = model_radar.validate_config(config)
    model_radar.atomic_write(
        path.expanduser(),
        json.dumps(validated, indent=2, ensure_ascii=False) + "\n",
    )


def default_gui_config_path() -> Path:
    installed = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "qwen35" / "model-radar.json"
    if installed.is_file():
        return installed
    if os.environ.get("FLATPAK_ID"):
        config = model_radar.load_config(Path(__file__).resolve().with_name("config.json"))
        data = data_directory()
        config.update(state_file=str(data / "state.json"), report_file=str(data / "latest-report.txt"))
        save_config(installed, config)
        return installed
    return Path(__file__).resolve().with_name("config.json")


def data_directory() -> Path:
    if os.environ.get("FLATPAK_ID"):
        return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "qwen35-model-radar"
    return Path.home() / ".local" / "share" / "qwen35-model-radar"


def validate_data_path(value: str, label: str) -> str:
    if not value.strip():
        raise model_radar.RadarError(f"{label} darf nicht leer sein.")
    path = Path(value).expanduser().resolve(strict=False)
    allowed = data_directory().resolve(strict=False)
    if path != allowed and allowed not in path.parents:
        raise model_radar.RadarError(f"{label} muss innerhalb von {allowed} liegen.")
    return value.strip()


def radar_command(config_path: Path, *arguments: str) -> list[str]:
    # CLI und GUI werden gemeinsam installiert. Die benachbarte Datei verhindert,
    # dass eine Projekt-GUI versehentlich eine ältere globale CLI startet.
    script = Path(__file__).resolve().with_name("model_radar.py")
    return radar_platform.python_command(script.name, "--config", str(config_path), *arguments)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")


def check_status_text(
    config: dict[str, Any], now: datetime | None = None, *, persist_migration: bool = False,
) -> str:
    """Beschreibt den letzten Erfolg und die tatsächliche API-Fälligkeit."""
    state_path = Path(config["state_file"]).expanduser()
    state, _first_run = model_radar.load_state(
        state_path, persist_migration=persist_migration,
    )
    last_check = model_radar.parse_timestamp(state.get("last_successful_check"))
    due_at = model_radar.next_check_at(state, int(config.get("check_interval_days", 7)))
    if last_check is None or due_at is None:
        return "Letzte erfolgreiche API-Prüfung: noch keine · API-Prüfung ist jetzt fällig"

    current = now or model_radar.utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    last_text = _format_utc(last_check)
    if current.astimezone(timezone.utc) >= due_at:
        due_text = "API-Prüfung ist jetzt fällig"
    else:
        due_text = f"Nächste API-Prüfung: {_format_utc(due_at)}"
    return f"Letzte erfolgreiche API-Prüfung: {last_text} · {due_text}"
