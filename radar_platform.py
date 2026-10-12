# SPDX-License-Identifier: GPL-3.0-or-later
"""Desktop- und systemd-Adapter ohne GTK-Abhängigkeit."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


SERVICE = "qwen35-model-radar.service"
TIMER = "qwen35-model-radar.timer"

PCI_VENDORS = {
    "0x1002": "AMD",
    "0x10de": "NVIDIA",
    "0x8086": "Intel",
}


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


def _read_text(path: Path) -> str:
    """Liest ein einzelnes, vom Kernel bereitgestelltes Attribut sicher ein."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return ""


def _vram_bytes(device: Path) -> int | None:
    """Liest den vom DRM-Treiber gemeldeten dedizierten VRAM ohne Treiberzugriff."""
    value = _read_text(device / "mem_info_vram_total")
    try:
        return int(value) if value else None
    except ValueError:
        return None


def _nvidia_vram(command_runner: Callable | None = None) -> list[int]:
    """Fragt optional nvidia-smi ab; Fehler bedeuten nur unvollständige Anzeige."""
    executable = shutil.which("nvidia-smi")
    if not executable:
        return []
    run = command_runner or subprocess.run
    try:
        result = run(
            [executable, "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            text=True, capture_output=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode:
        return []
    values: list[int] = []
    for line in result.stdout.splitlines():
        try:
            values.append(int(line.strip()) * 1024 * 1024)
        except ValueError:
            continue
    return values


def detect_graphics(
    sysfs_root: Path = Path("/sys/class/drm"),
    command_runner: Callable | None = None,
) -> dict:
    """Ermittelt sichtbare Grafikadapter ohne Root-Rechte oder Schreibzugriffe.

    Es werden nur Kernelattribute in sysfs und, für NVIDIA-VRAM, optional das
    vorhandene lesende Treiberwerkzeug abgefragt. Container und Flatpaks dürfen
    diese Informationen aus Sicherheitsgründen einschränken; das Ergebnis ist
    deshalb eine Bestandsaufnahme der *sichtbaren* Hardware, keine Zusicherung
    über die gesamte Host-Hardware.
    """
    adapters: list[dict[str, object]] = []
    limitations: list[str] = []
    try:
        cards = sorted(
            path for path in sysfs_root.iterdir()
            if path.name.startswith("card") and path.name[4:].isdigit()
        )
    except OSError as exc:
        return {"adapters": [], "limitations": [f"Grafikhardware ist für diesen Prozess nicht lesbar: {exc}"]}

    for card in cards:
        device = card / "device"
        vendor_id = _read_text(device / "vendor").lower()
        if not vendor_id:
            continue
        vendor = PCI_VENDORS.get(vendor_id, f"Unbekannter PCI-Hersteller ({vendor_id})")
        driver_link = device / "driver"
        try:
            driver = driver_link.resolve().name if driver_link.is_symlink() else ""
        except OSError:
            driver = ""
        vram = _vram_bytes(device)
        kind = "dediziert" if vendor == "NVIDIA" or (vendor == "AMD" and vram) else (
            "integriert" if vendor == "Intel" else "unbekannt"
        )
        adapters.append({
            "card": card.name,
            "vendor": vendor,
            "vendor_id": vendor_id,
            "device_id": _read_text(device / "device"),
            "driver": driver or "unbekannt",
            "kind": kind,
            "vram_bytes": vram,
            "vram_source": "DRM/sysfs" if vram is not None else "",
        })

    # NVIDIA exposes VRAM through its userspace tool on many systems, whereas
    # its DRM sysfs attribute is commonly absent. The fixed command is optional
    # and unprivileged; failures deliberately leave the value unknown.
    missing_nvidia = [item for item in adapters if item["vendor"] == "NVIDIA" and item["vram_bytes"] is None]
    for adapter, vram in zip(missing_nvidia, _nvidia_vram(command_runner)):
        adapter["vram_bytes"] = vram
        adapter["vram_source"] = "nvidia-smi"

    if os.environ.get("FLATPAK_ID"):
        limitations.append("Flatpak-Sandbox: Es werden nur für die Anwendung freigegebene Grafikdaten angezeigt.")
    if not adapters:
        limitations.append("Keine für diesen Prozess sichtbaren DRM-Grafikadapter gefunden.")
    elif any(item["vram_bytes"] is None for item in adapters):
        limitations.append("Dedizierter VRAM konnte nicht für alle Adapter ermittelt werden.")
    return {"adapters": adapters, "limitations": limitations}


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
        if os.environ.get("FLATPAK_ID"):
            return TimerStatus("", "", error="Host-systemd ist in der Flatpak-Sandbox nicht zugänglich")
        run = executor or subprocess.run

        def capture(arguments: list[str], *, status: bool = False) -> str:
            result = run(arguments, text=True, capture_output=True, timeout=15, check=False)
            output = result.stdout.strip()
            # Neuere systemd-Versionen melden eine fehlende Unit nur als „not-found“ auf stdout.
            if status and output == "not-found":
                raise RuntimeError("Der Hintergrunddienst ist nicht installiert")
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
