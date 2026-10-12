<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Änderungsprotokoll

## Unveröffentlicht

- Hardwarebewusste, unprivilegierte GPU-Erkennung in der Systemdiagnose:
  sichtbare DRM-Grafikadapter, Treiber und optionaler dedizierter VRAM; klare
  Hinweise bei Sandbox- und Berechtigungseinschränkungen.

- GUI-Polishing: verständliche Vorschau-Bezeichnung, Hilfetexte, anpassungsfähige
  Schaltflächen, anfängerfreundlicher Leerzustand, Tastenkürzel `Strg+S` und
  `Strg+R` sowie Wiederherstellung von Fenstergröße und zuletzt geöffneter Ansicht.
- Bericht zeigt einen Kurzüberblick über Zeit, Treffer und alle vier
  Lebenszyklusereignisse; Bericht und Dienstdiagnose lassen sich in die
  Zwischenablage kopieren. Fehlerdetails sind einklappbar.
- Timeraktivierung verlangt eine explizite Bestätigung mit Hinweis auf die
  regelmäßigen Netzwerkprüfungen.

- Eigene Projektdateien unter GPL-3.0-or-later gestellt; Lizenztext, SPDX-Kennzeichnungen
  und Drittanbieterhinweise ergänzt. Native Installation und neue Flatpak-Pakete enthalten die Hinweise.

- Flatpak-GUI und Offline-Installationsarchiv mit GNOME-/Mesa-Laufzeit; Wayland und X11-Rückfalloption.
- Isolierte, beschreibbare Flatpak-Konfiguration und Datenpfade; kein pauschaler Host-Zugriff.

- Fedora-GUI-Abhängigkeiten um `gobject-introspection` ergänzt, damit auch in minimalen Images die Cairo-Typinformationen verfügbar sind.
- CI prüft GTK-/libadwaita-Imports vor dem Testlauf; Installationshinweise entsprechend korrigiert.

## 2.6.0 – 2026-10-12

- Expliziter Headless-Modus und GTK-freie Plattformadapter für Desktop und systemd.
- GUI verwendet ihren laufenden Python-Interpreter; Desktop-Starter mit absolutem Pfad.
- Installation für Debian-/Fedora-Systeme mit optionaler Serverinstallation; fehlender
  systemd-Benutzerdienst verhindert weder Installation noch manuelle Prüfungen.
- Verständliche Abhängigkeitshinweise und deaktivierte Timeraktionen bei fehlendem Dienst.
- Release-Check für Headless-Betrieb und Plattformtests ohne GTK oder Desktop-Sitzung.

## 2.5.0 – 2026-09-15

- Alte Konfigurations- und Zustandsdateien werden beim regulären CLI-Lauf und GUI-Start nach erfolgreicher Validierung automatisch im aktuellen Schema gespeichert.
- Vor jeder Schema-Migration bleibt eine bytegetreue, restriktiv geschützte `.schema-vN.bak`-Sicherung erhalten; bei einem Schreibfehler wird die Originaldatei wiederhergestellt.
- Dry-Runs und GUI-`--check` lesen alte Schemata weiterhin ohne Migration auf der Festplatte.

## 2.4.0 – 2026-09-15

- Handlungsorientierte GUI-Diagnose mit Schweregraden und kontextabhängigen Aktionsschaltflächen.
- Maschinenlesbarer JSON-Diagnosevertrag zusätzlich zur bisherigen Terminalausgabe.
- Prüfberichte nennen Anzahl und Repository-Namen der bereinigten Zustandsdatensätze.
- Dry-Runs kennzeichnen Bereinigungen ausdrücklich als unverbindliche Vorschau.

## 2.3.0 – 2026-09-15

- Konfigurierbare Bereinigung lange abwesender Modelle (`absent_retention_days`).
- Strukturierte Dienst- und Timerdiagnose mit relevanten systemd-Statusfeldern und Journal-Auszug.
- Echte GTK-End-to-End-Tests für Fortschritt, Abbruch und automatisches Berichtsladen.
- Explizit aktivierbarer Live-Smoke-Test für den öffentlichen Hugging-Face-API-Vertrag.
- Reproduzierbarer Release-Check mit Syntax-, Test-, GTK-, Versions- und Installationsprüfung.

## 2.2.0 – 2026-09-15

- Lebenszyklusereignisse `ENTFERNT` und `WIEDER ERSCHIENEN`.
- Versionierte Konfigurations- und Zustandsmigrationen.
- Konservativer und strikter Textmodell-Filtermodus.
- Abbruch, Fortschrittsanzeige, automatisches Berichtsladen und Dienstdiagnose in der GUI.

## 2.1.1 – 2026-09-15

- Installation aller gemeinsam benötigten GUI-/CLI-Module abgesichert.
- Hugging-Face-Pagination, Retry-Verhalten und Teilfehler durch Integrationstests abgedeckt.

