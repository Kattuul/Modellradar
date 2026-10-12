"""Desktop- und systemd-Adapter ohne GTK-Abhängigkeit."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


SERVICE = "qwen35-model-radar.service"
TIMER = "qwen35-model-radar.timer"


def python_command(script: str, *arguments: str) -> list[str]:
    return [sys.executable, str(Path(__file__).resolve().with_name(script)), *arguments]


def desktop_notifications_available() -> bool:
    return bool(
        shutil.which("notify-send")
        and os.environ.get("DBUS_SESSION_BUS_ADDRESS")
        and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    )


def send_notification(title: str, message: str) -> None:
    subprocess.run(
        ["notify-send", "--app-name=LLM-Modellradar", "--urgency=normal", title, message],
        check=True, timeout=10,
    )


@dataclass(frozen=True)
class TimerStatus:
    enabled: str
    active: str
    next_run: str = ""
    error: str = ""

    def display_text(self) -> str:
        if self.error:
            return f"Automatische Prüfung nicht verfügbar: {self.error} · Manuelle Prüfungen sind möglich."
        suffix = f", nächster Timerstart: {self.next_run}" if self.next_run else ""
        return f"Timer: {self.enabled or 'unbekannt'}, {self.active or 'unbekannt'}{suffix}"


class SystemdScheduler:
    UNIT = TIMER
    SERVICE = SERVICE

    @classmethod
    def change_command(cls, enable: bool) -> list[str]:
        return ["systemctl", "--user", "enable" if enable else "disable", "--now", cls.UNIT]

    @classmethod
    def diagnostic_command(cls) -> list[str]:
        return python_command("model_radar_diagnostics.py", "--json")

    @classmethod
    def query_status(cls, executor=None) -> TimerStatus:
        run = executor or subprocess.run

        def capture(arguments: list[str], *, status: bool = False) -> str:
            result = run(arguments, text=True, capture_output=True, timeout=15, check=False)
            output = result.stdout.strip()
            # systemctl uses nonzero codes for ordinary disabled/inactive states.
            if result.returncode and not (status and output in {
                "enabled", "disabled", "static", "masked", "active", "inactive", "failed",
            }):
                raise RuntimeError(result.stderr.strip() or output or f"Exitcode {result.returncode}")
            return output

        try:
            enabled = capture(["systemctl", "--user", "is-enabled", cls.UNIT], status=True)
            active = capture(["systemctl", "--user", "is-active", cls.UNIT], status=True)
            next_run = capture([
                "systemctl", "--user", "show", cls.UNIT,
                "-p", "NextElapseUSecRealtime", "--value",
            ])
            return TimerStatus(enabled, active, next_run)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            return TimerStatus("", "", error=str(exc))
