#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Install locally built offline bundles into an isolated Flatpak installation and test displays."""
import argparse
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile

import build_flatpak


def verify(archive: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="modellradar-package-test-") as directory:
        base = Path(directory)
        with tarfile.open(archive) as tar:
            for member in tar.getmembers():
                path = Path(member.name)
                if path.is_absolute() or ".." in path.parts or not (member.isfile() or member.isdir()):
                    raise RuntimeError(f"Unsafe archive entry: {member.name}")
            tar.extractall(base / "unpacked")
        package = next((base / "unpacked").iterdir())
        environment = dict(os.environ, HOME=str(base / "home"), XDG_DATA_HOME=str(base / "data"),
                           XDG_CONFIG_HOME=str(base / "config"), XDG_CACHE_HOME=str(base / "cache"),
                           XDG_RUNTIME_DIR=str(base / "run"), FLATPAK_SYSTEM_DIR=str(base / "system"))
        (base / "run").mkdir(mode=0o700)
        (base / "system").mkdir()
        (base / "home").mkdir()
        subprocess.run(["sh", str(package / "install-offline.sh")], env=environment, check=True)
        code = (build_flatpak.ROOT / "packaging/flatpak/smoke_test.py").read_text()
        fixture = (build_flatpak.ROOT / "tests/fixtures/scan_initial.json").read_text()
        common = ["flatpak", "run", "--user", "--command=python3", "--env=GSK_RENDERER=cairo"]
        # Nested compositor: no X11 permission, assert actual WaylandDisplay.
        wayland = dict(environment, WLR_BACKENDS="headless", WLR_RENDERER="pixman", WLR_LIBINPUT_NO_DEVICES="1")
        subprocess.run(["dbus-run-session", "cage", "-d", "--", *common,
                        "--nosocket=x11", "--nosocket=fallback-x11", "--env=GDK_BACKEND=wayland",
                        build_flatpak.APP_ID, "-c", code, "wayland", fixture], env=wayland, check=True, timeout=90)
        subprocess.run(["xvfb-run", "-a", "dbus-run-session", *common,
                        "--nosocket=wayland", "--socket=x11", "--env=GDK_BACKEND=x11",
                        build_flatpak.APP_ID, "-c", code, "x11", fixture], env=environment, check=True, timeout=90)
        print("Offline-Installation und Wayland-/X11-Pakettests erfolgreich.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    verify(parser.parse_args().archive.resolve())
