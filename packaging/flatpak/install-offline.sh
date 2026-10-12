#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
set -eu
cd -- "$(dirname -- "$0")"
if ! command -v flatpak >/dev/null 2>&1; then
    echo "Bitte zuerst Flatpak über die Softwareverwaltung der Distribution installieren." >&2
    exit 1
fi
sha256sum --check SHA256SUMS
# Keine Downloads, keine Systeminstallation, kein Host-Timer.
for bundle in runtime-*.flatpak modellradar-*.flatpak; do
    flatpak install --user --assumeyes --noninteractive --no-deps --no-related "$bundle"
done
echo "Installation abgeschlossen. Modellradar ist im Anwendungsmenü verfügbar."
