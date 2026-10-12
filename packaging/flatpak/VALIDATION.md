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

## UI-Redesign: Desktop-Prüfung – 12.10.2026

Native Ausführung aus dem Quellbaum (kein Flatpak) in einer libvirt-VM mit
Debian 13.7, GNOME 48 (Wayland), GTK 4.18 und libadwaita 1.7. Konfiguration
und Zustand lagen in eigenen `XDG_*`-Verzeichnissen; Eingaben kamen als echte
Tastendrücke über `virsh send-key`, Fokusangaben über AT-SPI.

- `python3 release_check.py` und `--headless`: OK; Testsuite 142 Tests OK
  (1 übersprungen) unter Xvfb.
- Debian 12 (GTK 4.8, libadwaita 1.2) per `./container_check.sh`: OK, damit
  auch die Rückfallpfade ohne `Adw.Dialog`, `Adw.Banner` und `Adw.NavigationView`.
- Echte GNOME-Sitzung unter Wayland und über Xwayland (`GDK_BACKEND=x11`):
  Einführung, Live-Prüfung gegen die Hugging-Face-API, Bericht,
  Hardware-Eignung, Dienstdiagnose und Einstellungen.
- Kleine Bildschirme: 1024×768 und 800×600. Die virtio-Grafik bietet kein
  1024×600 an; 800×600 hat dieselbe nutzbare Höhe und ist schmaler. Einführung,
  Übersicht, Bericht, Dienstdiagnose, Einstellungen und der Ladezustand der
  Hardware-Eignung blieben ohne abgeschnittene Bedienelemente bedienbar.
- Tastatur: vollständiger Tab-Zyklus in Einführung, Übersicht, Unteransichten
  und Einstellungen; `Alt`-Kürzel, `Alt+←` zurück mit Fokus auf der zuvor
  gewählten Zeile, `Esc`, `Strg+,`.
- Screenreader: Orca 48.1 mit Debug-Protokoll der gesprochenen Ausgabe.
  Angesagt werden Seitentext, Listen mit Anzahl, Beschreibungen, Tastenkürzel,
  ausgegraute Zustände und die Schrittansagen der Einführung.

Dabei gefunden und behoben: Tab sprang in der Einführung in unsichtbare
Karussellseiten; die Einführung startete mit Fokus auf „Überspringen“;
mehrzeilige Einstellungsfelder fingen Tab ab; die Diagnose hatte einen leeren
Fokus-Halt; eine während einer laufenden Aufgabe geöffnete Unteransicht blieb
leer; der Banner zeigte den systemd-Rohwert `not-found`.

Einschränkungen dieser Prüfung: Die App wurde per SSH gestartet. GNOME meldete
das Fenster Orca dabei nicht als aktiviert, sodass bei einem Kaltstart die
erste Ansage entfiel; beim Start über das Anwendungsmenü ist das noch zu
bestätigen. Orca wurde nur über das Protokoll geprüft, nicht über die
Sprachausgabe. Das Öffnen von Modellkarten-Links über das Flatpak-Portal und
ein echtes 1024×600-Panel wurden nicht geprüft.

## Noch nicht verifiziert

Der neue GitHub-Paketworkflow wurde lokal erstellt, aber nicht auf GitHub ausgeführt.
Nicht geprüft: ARM-Pakete, proprietäre NVIDIA-Grafik, Screenreader im Flatpak, das
Öffnen von Links über das Flatpak-Portal und Installation über eine grafische
Softwareverwaltung auf frischen Debian-/Fedora-Desktops.
Die Bundles sind unsigniert und haben keinen automatischen Anwendungs-Updatekanal.
