#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""GUI- und Offline-Bundles aus installierten Flatpak-Laufzeiten, ohne Downloads."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

import model_radar

ROOT = Path(__file__).resolve().parent
APP_ID = "io.github.Kattuul.Modellradar"
RUNTIME = "org.gnome.Platform"
BRANCH = "49"
LICENSE_FILES = ("LICENSE", "LICENSING.md", "THIRD_PARTY_NOTICES.md")
FILES = ("model_radar.py", "model_recommendations.py", "model_radar_gui.py", "model_radar_diagnostics.py",
         "radar_gui_logic.py", "radar_gui_components.py", "radar_platform.py", "config.json", "config.json.license")
PERMISSIONS = ("--share=network", "--share=ipc", "--socket=wayland",
               "--socket=fallback-x11", "--device=dri")


def run(*args) -> str:
    arguments = list(map(str, args))
    print("+", " ".join(arguments), flush=True)
    return subprocess.check_output(arguments, text=True).strip()


def runtime_source(name: str, branch: str, arch: str) -> tuple[Path, str]:
    ref = f"runtime/{name}/{arch}/{branch}"
    location = Path(run("flatpak", "info", "--show-location", ref))
    commit = run("flatpak", "info", "--show-commit", ref)
    return location.parents[4] / "repo", commit


def digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def copy_license_files(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for name in LICENSE_FILES:
        shutil.copy2(ROOT / name, destination / name)


def build(output: Path) -> Path:
    for tool in ("flatpak", "ostree"):
        if not shutil.which(tool):
            raise RuntimeError(f"Build-Werkzeug fehlt: {tool}")
    arch = run("flatpak", "--default-arch")
    refs = ((RUNTIME, BRANCH), ("org.freedesktop.Platform.GL.default", "25.08"))
    sources = [(name, branch, *runtime_source(name, branch, arch)) for name, branch in refs]
    output.mkdir(parents=True, exist_ok=True)
    prefix = f"modellradar-{model_radar.VERSION}-{arch}"
    archive = output / f"{prefix}-offline.tar.gz"
    if archive.exists():
        raise RuntimeError(f"Ausgabe existiert bereits: {archive}")
    with tempfile.TemporaryDirectory(prefix="modellradar-flatpak-") as directory:
        base = Path(directory)
        app, repo, package = base / "app", base / "repo", base / prefix
        package.mkdir()
        # Pure Python: only stage files; no SDK/compiler needed.
        run("flatpak", "build-init", app, APP_ID, RUNTIME, RUNTIME, BRANCH)
        lib = app / "files/lib/modellradar"
        lib.mkdir(parents=True)
        for name in FILES:
            shutil.copy2(ROOT / name, lib / name)
        copy_license_files(app / "files/share/licenses" / APP_ID)
        launcher = app / "files/bin/modellradar"
        launcher.parent.mkdir(parents=True)
        launcher.write_text('#!/bin/sh\nexec python3 /app/lib/modellradar/model_radar_gui.py "$@"\n')
        launcher.chmod(0o755)
        for source, destination in (
            (f"{APP_ID}.desktop", f"share/applications/{APP_ID}.desktop"),
            (f"{APP_ID}.svg", f"share/icons/hicolor/scalable/apps/{APP_ID}.svg"),
        ):
            target = app / "files" / destination
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / "packaging/flatpak" / source, target)
        run("flatpak", "build-finish", "--command=modellradar", *PERMISSIONS, app)
        run("flatpak", "build", "--env=XDG_CONFIG_HOME=/tmp/radar-config",
            "--env=XDG_DATA_HOME=/tmp/radar-data", app,
            "python3", "/app/lib/modellradar/model_radar_gui.py", "--check")
        run("flatpak", "build-export", repo, app, "stable")
        # Offline bundle deliberately has no remote URL (not even a .flatpakrepo fetch).
        run("flatpak", "build-bundle", repo, package / f"{prefix}.flatpak", APP_ID, "stable")
        run("flatpak", "build-bundle", "--runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo",
            repo, base / f"{prefix}.flatpak", APP_ID, "stable")
        commits = {}
        for name, branch, origin, commit in sources:
            cached = output / ".runtime-cache" / f"{commit}.flatpak"
            target = package / f"runtime-{name}-{branch}-{arch}.flatpak"
            if cached.is_file():
                shutil.copy2(cached, target)
            else:
                run("ostree", f"--repo={repo}", "pull-local", origin, commit)
                run("ostree", f"--repo={repo}", "refs", f"--create=runtime/{name}/{arch}/{branch}", commit)
                run("flatpak", "build-bundle", "--runtime", repo, target, name, branch)
                cached.parent.mkdir(parents=True, exist_ok=True)
                partial = cached.with_suffix(".partial")
                shutil.copy2(target, partial)
                partial.replace(cached)
            commits[f"{name}/{arch}/{branch}"] = commit
        shutil.copy2(ROOT / "packaging/flatpak/install-offline.sh", package / "install-offline.sh")
        (package / "install-offline.sh").chmod(0o755)
        shutil.copy2(ROOT / "packaging/flatpak/README.md", package / "README.md")
        copy_license_files(package)
        (package / "build-info.json").write_text(json.dumps({
            "application": APP_ID, "version": model_radar.VERSION, "arch": arch,
            "application_license": "GPL-3.0-or-later",
            "runtime_commits": commits, "permissions": PERMISSIONS,
            "source_sha256": {name: digest(ROOT / name) for name in (*FILES, *LICENSE_FILES)},
        }, indent=2) + "\n")
        (package / "SHA256SUMS").write_text("".join(
            f"{digest(path)}  {path.name}\n" for path in sorted(package.iterdir())
        ))
        partial_archive = archive.with_suffix(".partial")
        with tarfile.open(partial_archive, "w:gz") as tar:
            tar.add(package, arcname=prefix)
        partial_archive.replace(archive)
        shutil.copy2(base / f"{prefix}.flatpak", output / f"{prefix}.flatpak")
    (output / "SHA256SUMS").write_text("".join(
        f"{digest(path)}  {path.name}\n" for path in (archive, output / f"{prefix}.flatpak")
    ))
    print(f"Offline-Installationspaket: {archive}", flush=True)
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    try:
        build(args.output.resolve())
    except (RuntimeError, subprocess.CalledProcessError, OSError) as exc:
        parser.exit(1, f"Build fehlgeschlagen: {exc}\n")
