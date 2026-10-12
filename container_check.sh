#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Führt den Release-Check lokal in einem frischen Distributions-Container aus,
# wie .github/workflows/platform-check.yml. Standard ist Debian 12, dessen
# GTK 4.8/libadwaita 1.2 die Fallbacks für ältere Systeme abdeckt.
#
#   ./container_check.sh                       # debian:12-slim, mit GUI
#   ./container_check.sh fedora:latest         # anderes Image
#   ./container_check.sh debian:12-slim --headless
set -euo pipefail

SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
IMAGE=${1:-debian:12-slim}
MODE=${2:-gui}
case "$MODE" in
    gui | --gui) MODE=gui ;;
    headless | --headless) MODE=headless ;;
    *) echo "Unbekannter Modus: $MODE (gui oder --headless)" >&2; exit 2 ;;
esac
case "$IMAGE" in
    */*) ;;
    *) IMAGE="docker.io/library/$IMAGE" ;;
esac

if command -v podman >/dev/null; then
    ENGINE=podman
elif command -v docker >/dev/null; then
    ENGINE=docker
else
    echo "Weder podman noch docker gefunden." >&2
    exit 1
fi

# Das Repository wird nur gelesen; geprüft wird eine Kopie ohne Bytecode-Caches.
exec "$ENGINE" run --rm -e MODE="$MODE" -v "$SOURCE_DIR:/src:ro,Z" "$IMAGE" bash -euo pipefail -c '
if command -v apt-get >/dev/null; then
    export DEBIAN_FRONTEND=noninteractive
    packages="python3 git"
    [ "$MODE" = gui ] && packages="$packages python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 xvfb xauth dbus-x11"
    apt-get update -qq
    apt-get install -y -qq $packages >/dev/null
else
    packages="python3 git"
    [ "$MODE" = gui ] && packages="$packages python3-gobject gobject-introspection gtk4 libadwaita xorg-x11-server-Xvfb xorg-x11-xauth dbus-x11"
    dnf install -y -q $packages >/dev/null
fi
cp -r /src /work
find /work -name __pycache__ -prune -exec rm -rf {} +
cd /work
. /etc/os-release
echo "== $PRETTY_NAME ($MODE) =="
if [ "$MODE" = gui ]; then
    python3 -c "import gi; gi.require_version(\"Gtk\", \"4.0\"); gi.require_version(\"Adw\", \"1\"); from gi.repository import Gtk, Adw; print(f\"GTK {Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}, libadwaita {Adw.get_major_version()}.{Adw.get_minor_version()}.{Adw.get_micro_version()}\")"
    python3 release_check.py
else
    python3 release_check.py --headless
fi
'
