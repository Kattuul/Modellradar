#!/usr/bin/env python3
"""Reproduzierbarer Release-, GTK- und Installationscheck."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import model_radar


ROOT = Path(__file__).resolve().parent
APP_FILES = (
    "model_radar.py", "model_radar_gui.py", "model_radar_diagnostics.py",
    "radar_gui_logic.py", "radar_gui_components.py",
    "radar_platform.py",
)
INSTALLED_FILES = {
    "qwen35-model-radar.service": Path(".config/systemd/user/qwen35-model-radar.service"),
    "qwen35-model-radar.timer": Path(".config/systemd/user/qwen35-model-radar.timer"),
}


def run(
    arguments: list[str], *, env: dict[str, str] | None = None,
    reject_output: str | None = None,
) -> None:
    result = subprocess.run(
        arguments, cwd=ROOT, env=env, text=True, capture_output=True, check=False,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode:
        raise RuntimeError(f"Prüfung fehlgeschlagen ({result.returncode}): {' '.join(arguments)}")
    combined = result.stdout + result.stderr
    if reject_output and reject_output in combined:
        raise RuntimeError(
            f"Prüfung wurde unerwartet übersprungen ({reject_output}): {' '.join(arguments)}"
        )


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_metadata() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if f"## {model_radar.VERSION}" not in changelog:
        raise RuntimeError(f"CHANGELOG.md enthält keinen Eintrag für {model_radar.VERSION}.")
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    if config.get("schema_version") != model_radar.CONFIG_SCHEMA_VERSION:
        raise RuntimeError("config.json und CONFIG_SCHEMA_VERSION stimmen nicht überein.")


def check_installation(headless: bool = False) -> None:
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory)
        arguments = [str(ROOT / "install.sh"), "--target-home", str(target), "--no-systemd"]
        if headless:
            arguments.append("--headless")
        run(arguments)
        app = target / ".local" / "share" / "qwen35-model-radar" / "app"
        for name in APP_FILES:
            if headless and name in {"model_radar_gui.py", "radar_gui_logic.py", "radar_gui_components.py"}:
                continue
            if digest(ROOT / name) != digest(app / name):
                raise RuntimeError(f"Installierte Datei weicht von der Quelle ab: {name}")
        for name, relative_target in INSTALLED_FILES.items():
            if digest(ROOT / name) != digest(target / relative_target):
                raise RuntimeError(f"Installiertes Artefakt weicht von der Quelle ab: {name}")
        cli = target / ".local" / "bin" / "qwen35-model-radar"
        version = subprocess.run([str(cli), "--version"], text=True, capture_output=True, check=False)
        if version.returncode or version.stdout.strip() != model_radar.VERSION:
            raise RuntimeError("Installierte CLI meldet eine abweichende Version.")
        gui = target / ".local" / "bin" / "qwen35-model-radar-gui"
        config = target / ".config" / "qwen35" / "model-radar.json"
        if not headless:
            run([str(gui), "--config", str(config), "--check"])
            desktop = (target / ".local/share/applications/qwen35-model-radar.desktop").read_text()
            if str(gui) not in desktop:
                raise RuntimeError("Desktop-Starter enthält keinen absoluten GUI-Pfad.")
        run([
            sys.executable, "-S", str(cli), "--config", str(config), "--headless",
            "--fixture", str(ROOT / "tests/fixtures/scan_initial.json"),
            "--state", str(target / "state.json"), "--report", str(target / "report.txt"),
        ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-api", action="store_true", help="zusätzlich die öffentliche Hugging-Face-API prüfen")
    parser.add_argument("--headless", action="store_true", help="Kern und Serverinstallation ohne GTK prüfen")
    args = parser.parse_args(argv)
    try:
        check_metadata()
        run([sys.executable, "-m", "py_compile", *APP_FILES, "release_check.py"])
        test_arguments = ["discover", "-v"]
        if args.headless:
            gui_tests = {"test_gui_e2e", "test_radar_gui_components", "test_installation"}
            test_arguments = ["-v", *(
                "tests." + path.stem for path in sorted((ROOT / "tests").glob("test_*.py"))
                if path.stem not in gui_tests
            )]
        run([sys.executable, "-W", "error::ResourceWarning", "-m", "unittest", *test_arguments])
        if not args.headless:
            xvfb = shutil.which("xvfb-run")
            if not xvfb:
                raise RuntimeError("xvfb-run fehlt; GTK-End-to-End-Test kann nicht ausgeführt werden.")
            gtk_environment = dict(os.environ, GDK_BACKEND="x11")
            run(
                [xvfb, "-a", sys.executable, "-m", "unittest", "-v", "tests.test_gui_e2e"],
                env=gtk_environment,
                reject_output="skipped",
            )
        if args.live_api:
            environment = dict(os.environ, MODEL_RADAR_LIVE_API="1")
            run([sys.executable, "-m", "unittest", "-v", "tests.test_live_api_smoke"], env=environment)
        check_installation(args.headless)
    except (OSError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"RELEASE-CHECK FEHLGESCHLAGEN: {exc}", file=sys.stderr)
        return 1
    print(f"Release-Check {model_radar.VERSION}: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
