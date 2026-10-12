#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
TARGET_HOME=${HOME:?HOME ist nicht gesetzt}
RUN_SYSTEMD=1
HEADLESS=0
RADAR_PYTHON=python3

usage() {
    echo "Aufruf: ./install.sh [--target-home PFAD] [--no-systemd] [--headless]"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --target-home)
            [ "$#" -ge 2 ] || { usage >&2; exit 2; }
            TARGET_HOME=$2
            shift 2
            ;;
        --no-systemd)
            RUN_SYSTEMD=0
            shift
            ;;
        --headless)
            HEADLESS=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unbekannte Option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

case "$TARGET_HOME" in
    /*) ;;
    *) echo "--target-home muss ein absoluter Pfad sein." >&2; exit 2 ;;
esac
[ "$TARGET_HOME" != "/" ] || { echo "/ ist kein zulässiges Zielverzeichnis." >&2; exit 2; }
RADAR_PYTHON=$(command -v "$RADAR_PYTHON") || { echo "Python 3 fehlt." >&2; exit 1; }
"$RADAR_PYTHON" -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10 oder neuer erforderlich"'
if [ "$HEADLESS" -eq 0 ]; then
    "$RADAR_PYTHON" -c 'import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1"); from gi.repository import Gtk, Adw' || {
        echo "GUI-Abhängigkeiten fehlen. Debian/Ubuntu: python3-gi gir1.2-gtk-4.0 gir1.2-adw-1; Fedora: python3-gobject gobject-introspection gtk4 libadwaita." >&2
        echo "Für einen Server ohne Oberfläche: --headless verwenden." >&2
        exit 1
    }
fi

SOURCE_FILES=(model_radar.py model_radar_diagnostics.py radar_platform.py
    config.json qwen35-model-radar.service qwen35-model-radar.timer)
if [ "$HEADLESS" -eq 0 ]; then
    SOURCE_FILES+=(model_radar_gui.py radar_gui_logic.py radar_gui_components.py qwen35-model-radar.desktop)
fi
for source_file in "${SOURCE_FILES[@]}"; do
    [ -f "$SOURCE_DIR/$source_file" ] || {
        echo "Quelldatei fehlt: $SOURCE_DIR/$source_file" >&2
        exit 1
    }
done

APP_DIR="$TARGET_HOME/.local/share/qwen35-model-radar/app"
BIN_DIR="$TARGET_HOME/.local/bin"
CONFIG_DIR="$TARGET_HOME/.config/qwen35"
SYSTEMD_DIR="$TARGET_HOME/.config/systemd/user"
DESKTOP_DIR="$TARGET_HOME/.local/share/applications"

install -d -m 755 "$APP_DIR" "$BIN_DIR" "$SYSTEMD_DIR"
install -d -m 700 "$CONFIG_DIR"
install -m 755 \
    "$SOURCE_DIR/model_radar.py" \
    "$SOURCE_DIR/model_radar_diagnostics.py" "$APP_DIR/"
install -m 644 "$SOURCE_DIR/radar_platform.py" "$APP_DIR/"
ln -sfn "$APP_DIR/model_radar.py" "$BIN_DIR/qwen35-model-radar"
if [ "$HEADLESS" -eq 0 ]; then
    install -d -m 755 "$DESKTOP_DIR"
    install -m 755 "$SOURCE_DIR/model_radar_gui.py" "$APP_DIR/"
    install -m 644 "$SOURCE_DIR/radar_gui_logic.py" "$SOURCE_DIR/radar_gui_components.py" "$APP_DIR/"
    ln -sfn "$APP_DIR/model_radar_gui.py" "$BIN_DIR/qwen35-model-radar-gui"
    # Ein absoluter Starter funktioniert auch, wenn ~/.local/bin nicht im Desktop-PATH liegt.
    "$RADAR_PYTHON" - "$SOURCE_DIR/qwen35-model-radar.desktop" "$DESKTOP_DIR/qwen35-model-radar.desktop" "$BIN_DIR/qwen35-model-radar-gui" <<'PY'
import pathlib, sys
source, target, launcher = sys.argv[1:]
escaped = launcher.replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%')
pathlib.Path(target).write_text(pathlib.Path(source).read_text().replace('Exec=qwen35-model-radar-gui', 'Exec="' + escaped + '"'))
PY
fi

if [ ! -e "$CONFIG_DIR/model-radar.json" ]; then
    install -m 600 "$SOURCE_DIR/config.json" "$CONFIG_DIR/model-radar.json"
else
    chmod 600 "$CONFIG_DIR/model-radar.json"
    echo "Vorhandene Konfiguration bleibt erhalten."
fi

install -m 644 "$SOURCE_DIR/qwen35-model-radar.service" "$SOURCE_DIR/qwen35-model-radar.timer" "$SYSTEMD_DIR/"

INSTALLED_VERSION=$("$RADAR_PYTHON" "$APP_DIR/model_radar.py" --version)
SOURCE_VERSION=$("$RADAR_PYTHON" "$SOURCE_DIR/model_radar.py" --version)
[ "$INSTALLED_VERSION" = "$SOURCE_VERSION" ] || {
    echo "Versionsprüfung fehlgeschlagen: Quelle $SOURCE_VERSION, Installation $INSTALLED_VERSION" >&2
    exit 1
}

if [ "$RUN_SYSTEMD" -eq 1 ]; then
    if command -v systemctl >/dev/null && systemctl --user show-environment >/dev/null 2>&1; then
    if command -v systemd-analyze >/dev/null; then
    systemd-analyze --user verify \
        "$SYSTEMD_DIR/qwen35-model-radar.service" \
        "$SYSTEMD_DIR/qwen35-model-radar.timer"
    fi
    systemctl --user daemon-reload
    if systemctl --user is-active --quiet qwen35-model-radar.timer; then
        systemctl --user restart qwen35-model-radar.timer
        echo "Aktiver Timer wurde neu gestartet."
    else
        echo "Timer bleibt deaktiviert; Aktivierung erfolgt weiterhin bewusst über die GUI."
    fi
    else
        echo "Kein erreichbarer systemd-Benutzerdienst. Manuelle Prüfungen sind verfügbar."
    fi
fi

echo "LLM-Modellradar $INSTALLED_VERSION wurde unter $APP_DIR installiert."
