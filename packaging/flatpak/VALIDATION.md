<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Paketprüfung – 12.10.2026

Erzeugte x86_64-Testpakete: `dist/modellradar-2.6.0-x86_64.flatpak` und
`dist/modellradar-2.6.0-x86_64-offline.tar.gz` (inklusive GNOME- und Mesa-Laufzeit).
Prüfsummen: `dist/SHA256SUMS`; exakte Laufzeit-Commits und Quellcode-Prüfsummen
stehen zusätzlich in `build-info.json` innerhalb des Offline-Archivs.

## Erfolgreich geprüft

- `python3 release_check.py`: reguläre Tests, sieben GTK-End-to-End-Tests,
  isolierte native Installation, GUI-Konfiguration und lokaler Fixture-Lauf.
- `python3 release_check.py --headless`: GTK-freier Kern und Serverinstallation.
- `python3 verify_flatpak.py dist/modellradar-2.6.0-x86_64-offline.tar.gz`:
  Prüfsummenprüfung und Installation aller drei Bundles in temporären Verzeichnissen,
  ohne vorhandene Host-Laufzeiten als Installationsvoraussetzung.
- Echtes GTK-Fenster im installierten Flatpak unter Cage/Headless mit
  `GdkWaylandDisplay`; X11- und fallback-x11-Berechtigungen explizit entzogen.
- Echtes GTK-Fenster im installierten Flatpak unter Xvfb mit `X11Display`.
- Auf beiden Displays: GUI-Einstellungen ändern und speichern, benachbarte CLI
  mit lokalen Fixture-Daten starten, Zustand und Bericht speichern und Bericht laden.

Keine Live-API-Anfragen, Modell-Downloads, Timeraktivierung oder Änderungen an
bestehenden Modellradar-Installationen. Die Testumgebung meldete erwartbare
Warnungen zu fehlendem Accessibility-Bus/PipeWire und Xvfb-Grafikbeschleunigung;
alle Prüfprozesse endeten mit Exitcode 0.

## Noch nicht verifiziert

Der neue GitHub-Paketworkflow wurde lokal erstellt, aber nicht auf GitHub ausgeführt.
Nicht geprüft: ARM-Pakete, proprietäre NVIDIA-Grafik, Screenreader und Installation
über eine grafische Softwareverwaltung auf frischen Debian-/Fedora-Desktops.
Die Bundles sind unsigniert und haben keinen automatischen Anwendungs-Updatekanal.
