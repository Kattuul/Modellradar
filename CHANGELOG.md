<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Änderungsprotokoll

## Unveröffentlicht

- UI-Redesign: Einstellungen in vier Gruppen (Modellquellen, Automatische
  Prüfungen, Netzwerk, Erweitert) mit libadwaita-Zeilen; `Strg+S` speichert
  jetzt auch direkt im Einstellungsfenster. Automatische Prüfungen werden über
  einen Schalter statt zweier Timer-Schaltflächen gesteuert.
- Kurze Erfolgsmeldungen erscheinen als Toast, ein fehlender systemd-Dienst als
  Banner mit Sprung zur Diagnose. Bestätigungen nutzen ab libadwaita 1.5
  `Adw.AlertDialog`.
- Hauptmenü mit Untermenü „Weitere Prüfoptionen“, Tastenkürzel-Übersicht
  (`Strg+?`) und „Über Modellradar“. „Gespeicherten Bericht neu laden“ entfällt;
  der Bericht wird beim Start automatisch geladen.
- Einsteigerfreundliche Einführung beim ersten Start: drei überspringbare
  Schritte (Was Modellradar tut, erkannte Grafikhardware und Timerzustand,
  erste Prüfung starten), wiederaufrufbar über „Einführung erneut anzeigen“.
  Der Stand wird als `onboarding_version` gespeichert; nach der ersten Prüfung
  führt ein Hinweis zum Bericht. GTK-Tests schreiben ihre GUI-Präferenzen nicht
  mehr in das echte Benutzerverzeichnis.
- Eigene Unteransichten für Bericht, Hardware-Eignung und Dienstdiagnose
  (`Adw.NavigationView` ab libadwaita 1.4, sonst Stack mit Zurück-Button). Die
  Übersicht zeigt nur noch Prüfung, Status, Timer und Navigationszeilen samt
  erkannter Grafikkarte. Jede Unteransicht hat eigene Lade-, Ergebnis- und
  Fehlerzustände sowie Kopier- und Neu-berechnen-Aktionen; ein Abbruch stellt
  das vorige Ergebnis wieder her. Die Vorschau erscheint als ungespeicherter
  Bericht, Fehlermeldungen bieten direkt „Dienstdiagnose öffnen“.
- Barrierefreiheit: verständliche Namen für alle Bedienelemente, eindeutige
  Tastenkürzel, Fokus folgt Zustandswechseln, Screenreader-Ansagen für Lade-,
  Ergebnis- und Fehlerzustände; Zustände sind zusätzlich zur Farbe per Text
  und Symbol erkennbar.
- Fenster, Einstellungen und Einführung passen auf 1024×600-Bildschirme;
  auf niedrigen und schmalen Displays entfallen rein dekorative Elemente.
- Bericht und Hardware-Eignung erscheinen als Karten mit Kennzahlen statt als
  Fließtext; der Rohtext bleibt eingeklappt verfügbar.
- Modellkarten-Links öffnen nur noch Adressen auf `https://huggingface.co`.
- Bedienbarkeit per Tastatur und Screenreader (Prüfung mit Orca auf Debian 13):
  Tab bleibt in der Einführung auf der sichtbaren Seite und beginnt auf
  „Weiter“ statt „Überspringen“; mehrzeilige Einstellungsfelder fangen Tab
  nicht mehr ab; die Diagnose hat keinen leeren Fokus-Halt mehr; Seitenansagen
  nennen den Titel nur einmal.
- Wird eine Unteransicht geöffnet, während noch eine Aufgabe läuft, erklärt sie
  das und startet danach automatisch, statt leer zu bleiben. Ein fehlender
  systemd-Timer heißt jetzt „Der Hintergrunddienst ist nicht installiert“
  statt `not-found`.
- Debian 12 (GTK 4.8, libadwaita 1.2) startet die GUI wieder: Die markierbare
  API-Adresse nutzt `set_subtitle_selectable` nur, wo vorhanden. Die CI prüft
  zusätzlich `debian:12-slim`; `container_check.sh` führt denselben
  Release-Check lokal mit Podman oder Docker aus.

- Hardwarebewusste, unprivilegierte GPU-Erkennung in der Systemdiagnose:
  sichtbare DRM-Grafikadapter, Treiber und optionaler dedizierter VRAM; klare
  Hinweise bei Sandbox- und Berechtigungseinschränkungen.

- GUI-Polishing: verständliche Vorschau-Bezeichnung, Hilfetexte, anpassungsfähige
  Schaltflächen, anfängerfreundlicher Leerzustand, Tastenkürzel `Strg+S` und
  `Strg+R` sowie Wiederherstellung der Fenstergröße.
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

