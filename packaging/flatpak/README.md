<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Modellradar mit GUI-Laufzeit

Das Offline-Archiv enthält die Anwendung, GNOME Platform 49 (Python, PyGObject,
GTK4, libadwaita, Cairo und deren Laufzeitbibliotheken) und die zugehörige
Mesa-GL-Laufzeit. Keine GTK-Installation auf dem Host nötig. Geeignet für
Debian-/Fedora-Systeme und unveränderliche Images; GNOME als Desktop ist nicht
erforderlich. Flatpak und ein grafischer Desktop müssen auf dem Ziel vorhanden sein.
Kernel-/Grafiktreiber und Flatpak selbst sind keine mitgelieferten GUI-Bibliotheken.
Proprietäre NVIDIA-Treiber können zusätzlich eine passende Flatpak-Erweiterung benötigen.

## Installation

Die eigenen Modellradar-Dateien stehen unter **GPL-3.0-or-later**. Neue Pakete
enthalten `LICENSE`, `LICENSING.md` und `THIRD_PARTY_NOTICES.md`; im installierten
Flatpak liegen diese unter `/app/share/licenses/io.github.Kattuul.Modellradar/`.
Die Laufzeitkomponenten behalten ihre eigenen Lizenzen. Vor einer öffentlichen
Offline-Weitergabe bleibt deren vollständige Lizenz-/Quellcodeprüfung erforderlich.

Für die komfortable **Online-Installation** die separate `modellradar-*.flatpak`
in der Softwareverwaltung öffnen. Diese installiert fehlende Laufzeitabhängigkeiten
über Flathub; die einzelne Datei enthält diese nicht selbst.

Für die **Offline-Installation** das Archiv entpacken und im entpackten Ordner
`sh install-offline.sh` ausführen. Das Skript prüft SHA-256-Prüfsummen und installiert
die drei Bundles nur für den aktuellen Benutzer, ohne Downloads und ohne sudo.
Danach Modellradar über das Anwendungsmenü starten.
Alternativ die beiden `runtime-*.flatpak`-Dateien und anschließend die
`modellradar-*.flatpak`-Datei aus dem Archiv in der Softwareverwaltung öffnen.
Die Pakete sind noch nicht kryptografisch signiert; Prüfsummen sind keine Signatur.
Es handelt sich um lokale Testpakete, nicht um eine Flathub-Veröffentlichung.
Offline-Bundles bieten keinen eingerichteten automatischen Updatekanal; neue
Anwendungs- und Laufzeitversionen müssen erneut als geprüfte Pakete installiert werden.

## Wayland und Sandbox

Wayland ist direkt freigegeben; X11 nur als Rückfalloption (`fallback-x11`).
Keine erzwungene X11-Umgebung. Einstellungen, Zustand und Bericht liegen isoliert
unter `~/.var/app/io.github.Kattuul.Modellradar/`; vorhandene native Einstellungen
werden nicht überschrieben oder automatisch importiert. Die GUI benötigt Netzwerk
für die Modell-API, aber keinen Zugriff auf alle Host-Dateien oder den Host-Bus.

Manuelle Prüfungen, Einstellungen, Fortschritt, Abbruch und Berichtsanzeige sind
verfügbar. Der native systemd-Timer und `notify-send` sind in diesem Paket nicht
verfügbar; automatische Prüfungen bei geschlossener GUI bleiben der nativen
Installation vorbehalten. Es wird kein Timer aktiviert. Die App lädt keine Modelle.

## Paket bauen

`python3 build_flatpak.py` verwendet bereits installierte Laufzeiten
`org.gnome.Platform//49` und `org.freedesktop.Platform.GL.default//25.08`.
Es benötigt Flatpak und OSTree, keine zusätzlichen Python-Pakete und keinen SDK-
Download. Laufzeit-Commits werden in `build-info.json` dokumentiert. Das Ausgabe-
Archiv entsteht unter `dist/`. Der Build prüft GUI-Imports im Flatpak-Sandbox.
Build und Tests ändern keine bestehende Modellradar-Installation.

`python3 verify_flatpak.py dist/modellradar-2.6.0-x86_64-offline.tar.gz` installiert
das Paket ausschließlich in temporäre Testverzeichnisse und prüft echte GTK-Fenster,
Einstellungen, einen lokalen Fixture-Lauf und den Bericht unter reinem Wayland
(Cage mit Headless-Backend) und X11 (Xvfb). Dafür werden zusätzlich Cage, Xvfb und
dbus-run-session benötigt. Es erfolgen keine API-Anfragen.
