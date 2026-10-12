<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Lokaler LLM-Modellradar für Linux

Lizenz für die eigenen Projektdateien: **GPL-3.0-or-later**.
Vollständiger Text: [LICENSE](LICENSE); Geltungsbereich, Beiträge und Weitergabe:
[LICENSING.md](LICENSING.md); externe Komponenten: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Der Modellradar fragt einmal pro Woche ausschließlich die in `config.json`
freigegebenen Hugging-Face-Namespaces ab. Treffer werden stets nur als
**mögliche Nachfolger** bezeichnet. Es werden keine Modelle heruntergeladen,
keine LLMs ausgeführt und keine Rechte an ein LLM weitergegeben.

Der Kern benötigt ausschließlich Python 3.10 oder neuer und dessen
Standardbibliothek. Die grafische Oberfläche verwendet GTK4/libadwaita über
PyGObject und ist für Debian- und Fedora-basierte Systeme vorgesehen, auch
außerhalb von GNOME. Desktop-Hinweise nutzen optional `notify-send` und eine
grafische D-Bus-Sitzung. Öffentliche Modellmetadaten
werden ohne Hugging-Face-Token abgefragt; das Skript liest auch keinen lokal
gespeicherten Token.

## GUI-Paket für Debian, Fedora und unveränderliche Images

`python3 build_flatpak.py` erzeugt ein Flatpak sowie ein Offline-Archiv inklusive
Python/PyGObject, GTK4, libadwaita und Mesa-Laufzeit. Wayland wird direkt unterstützt,
X11 nur als Rückfalloption. Details, Voraussetzungen und Sandbox-Einschränkungen:
[Paketinstallation](packaging/flatpak/README.md).
Die lokal durchgeführten Prüfungen sind im [Prüfprotokoll](packaging/flatpak/VALIDATION.md) dokumentiert.

## Dateien und Datenfluss

- `config.json`: versionierte Freigabe- und Filterregeln
- `model_radar.py`: API-Abfrage, Filter, Vergleich, Bericht und Benachrichtigung
- `model_radar_gui.py`: schlanker GTK-Anwendungsstart und Fensterkoordinator
- `model_radar_diagnostics.py`: strukturierte, nur lesende systemd-Diagnose
- `radar_gui_components.py`: Einstellungs-/Aktionsseiten, Prozessausführung und
  systemd-Timersteuerung
- `radar_gui_logic.py`: GUI-unabhängige Validierungs- und Speicherlogik
- `radar_platform.py`: GTK-freie Adapter für Python-Aufrufe, Benachrichtigungen und systemd
- `qwen35-model-radar.desktop`: Starter für das Anwendungsmenü des Desktops
- `qwen35-model-radar.service` / `.timer`: systemd-User-Einheiten
- `install.sh`: idempotente Installation und Aktualisierung aller Komponenten
- `release_check.py`: automatisierter Syntax-, Test-, GTK- und Installationscheck
- `CHANGELOG.md`: versioniertes Änderungsprotokoll
- `.github/workflows/platform-check.yml`: getrennte GUI-/Headless-Prüfungen
  in Debian- und Fedora-Containern bei Push und Pull Request
- `tests/fixtures/`: reproduzierbare, rein lokale API-Antworten
- `state.json` (später unter `~/.local/share/...`): bekannte IDs, SHA,
  Kandidaten-Snapshots, Anwesenheitsstatus sowie `first_seen`, `last_seen`,
  `missing_since` und `last_modified`
- `latest-report.txt`: letzter erfolgreicher ausführlicher Bericht

Ein API-Fehler bei nur einer Teilabfrage bricht die gesamte Prüfung ab. Dadurch
wird nie ein unvollständiger Abruf als neuer Zustand gespeichert. HTTP 429,
Serverfehler und Zeitüberschreitungen werden begrenzt mit Backoff wiederholt.
Auch ein erreichtes `max_pages_per_query`-Limit bricht kontrolliert ab, solange
die API noch eine Folgeseite meldet; das Limit kann dann gezielt erhöht werden.
Zustand und Bericht werden mit restriktiven Rechten atomar ersetzt. In Logs
landen keine Header, Tokens oder Umgebungsvariablen. Zustands- und Berichtspfad
müssen auf unterschiedliche Dateien zeigen.

Alte `config.json`- und `state.json`-Schemata werden beim regulären CLI-Lauf
und beim GUI-Start nach erfolgreicher Validierung automatisch gespeichert.
Daneben bleibt die unveränderte Originaldatei als
`<Dateiname>.schema-v<alte Version>.bak` erhalten; bei einer schon belegten
Sicherungsbezeichnung wird eine fortlaufende Nummer angehängt. Wenn das
Ersetzen fehlschlägt, stellt das Programm die ursprünglichen Bytes wieder her.
Falls auch dies nicht gelingt, meldet es den Pfad der Sicherung. Eine fehlende
Zustandsdatei wird nicht künstlich angelegt. `--dry-run` und das reine
GUI-`--check` schreiben weder Migrationen noch Sicherungen. Die Sicherungen
werden nicht automatisch gelöscht und können nach Prüfung der neuen Dateien
manuell archiviert werden.

## Lokaler Test im Projekt

Syntax und Tests:

```bash
python3 -m unittest discover -v
```

Reproduzierbarer Dry-Run ohne Netzwerk, Schreibzugriff oder Benachrichtigung:

```bash
python3 model_radar.py --dry-run --fixture tests/fixtures/scan_initial.json
```

Live-Dry-Run gegen die öffentliche API (weiterhin ohne Zustandsänderung und
ohne Benachrichtigung):

```bash
python3 model_radar.py --dry-run
```

Der reguläre Testlauf bleibt vollständig netzwerkfrei. Ein kleiner, explizit
aktivierter Vertragstest prüft auf Wunsch eine einzelne API-Seite:

```bash
MODEL_RADAR_LIVE_API=1 python3 -m unittest -v tests.test_live_api_smoke
```

## Grafische Oberfläche

Die GUI ist der komfortable Zugang für Desktop-Nutzer. Prüfung, Einstellungen,
Fortschritt, Abbruch und Berichtsanzeige funktionieren auch ohne systemd.
Fehlt ein erreichbarer systemd-Benutzerdienst, erklärt die Oberfläche dies und
deaktiviert ausschließlich die Timeraktionen. Automatische Hintergrundprüfungen
benötigen derzeit systemd; andere Zeitplaner können die Headless-CLI aufrufen.
Die GUI setzt eine grafische Sitzung und GTK4/libadwaita voraus.

Abhängigkeiten auf Debian/Ubuntu:

```bash
sudo apt install python3 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 libnotify-bin
```

Auf Fedora:

```bash
sudo dnf install python3 python3-gobject gobject-introspection gtk4 libadwaita libnotify
```

Bei unveränderlichen Images können diese Abhängigkeiten vom jeweiligen Image
bereitgestellt werden. Die Benutzerinstallation verändert keine Systemdateien.
Alternativ bringt das unter „GUI-Paket“ beschriebene Flatpak die grafische
Laufzeit mit.

Die GUI bearbeitet sämtliche Konfigurationsfelder und ist hierarchisch
aufgebaut: Die Übersicht enthält nur die sofortige Prüfung, den aktuellen
Status, den Schalter **Automatische Prüfungen**, der den Timer ein- und
ausschaltet, sowie die Zeilen „Letztes Ergebnis“ und „Passend zu diesem
Rechner“. Diese führen in die Unteransichten **Bericht** und
**Hardware-Eignung**; die **Dienstdiagnose** ist eine dritte Unteransicht.
Jede Unteransicht zeigt eindeutig, ob sie lädt, ein Ergebnis hat oder ein
Fehler aufgetreten ist, und bringt ihre eigene Kopier- beziehungsweise
Neu-berechnen-Aktion mit. Ab libadwaita 1.4 nutzt die Navigation
`Adw.NavigationView` (Zurück per Alt+←, Maustaste oder Wischgeste), ältere
Versionen erhalten einen Zurück-Button. Die Hardware-Eignung wird beim ersten
Öffnen berechnet und danach nur auf Wunsch erneut; eine **Vorschau ohne
Änderungen** erscheint in der Berichtsansicht deutlich als ungespeichert
markiert. Das Hauptmenü enthält Einstellungen, „Einführung erneut
anzeigen“, das Untermenü
„Weitere Prüfoptionen“ mit **Vorschau ohne Änderungen**, die Dienstdiagnose,
eine Übersicht der Tastenkürzel und „Über Modellradar“. Der gespeicherte Bericht
wird beim Start und nach jeder erfolgreichen Prüfung automatisch geladen.
Die Einstellungen sind in die Gruppen Modellquellen, Automatische Prüfungen,
Netzwerk und Erweitert gegliedert; Änderungen gelten erst nach **Speichern**.
Netzwerkprüfungen laufen in einem
Hintergrundprozess, während eine pulsierende Fortschrittsanzeige sichtbar ist;
der Lauf kann direkt abgebrochen werden. Nach einer erfolgreichen regulären
Prüfung wird der gespeicherte Bericht automatisch geladen und zeigt eine
Kurzbilanz für neue, aktualisierte, entfernte und wieder erschienene Modelle.
Bericht, Hardware-Eignung und Diagnose können ohne Terminal in die
Zwischenablage kopiert werden; die jeweilige Kopieraktion ist erst aktiv, wenn
passende Daten vorliegen. Schlägt ein Vorgang fehl, führt „Dienstdiagnose
öffnen“ direkt aus der Fehlermeldung zur Diagnose.
Der Menüpunkt „Dienstdiagnose“ zeigt eine strukturierte Diagnose mit Dienst- und
Timerzustand, letztem Ergebnis, Prozessstatus, Unit-Pfaden, Zeitpunkten und den
letzten 30 Journalzeilen. Befunde erscheinen nach Fehler, Warnung und Information
getrennt.
Passende Aktionen erlauben direkt das Aktivieren des Timers, den Wechsel zur
Konfiguration, eine erneute Prüfung oder das Wiederholen der Diagnose. Befehle
werden immer als Argumentliste ohne Shell gestartet.

Beim ersten Start führt eine kurze, jederzeit überspringbare Einführung in drei
Schritten in Alltagssprache durch die Anwendung: was Modellradar tut (und
dass es keine Modelle herunterlädt oder ausführt), welche Grafikkarte und wie
viel Grafikspeicher lokal erkannt wurden, ob automatische Prüfungen laufen, und
wie die erste Prüfung gestartet wird. Die Hardwareerkennung läuft dabei im
Hintergrund und wird nicht übertragen. Nach der ersten erfolgreichen Prüfung
weist ein Hinweis auf den Bericht. Der Einführungsstand wird als
`onboarding_version` neben der Fenstergröße gespeichert, sodass spätere größere
Neuerungen gezielt erneut vorgestellt werden können. Solange noch kein Bericht
existiert, erklärt ein Leerzustand den nächsten sicheren Schritt. Das
Einschalten automatischer Prüfungen verlangt eine Bestätigung, weil es regelmäßige
Netzwerkprüfungen startet. Kurze Erfolgsmeldungen erscheinen als
vorübergehende Hinweise; ist der systemd-Benutzerdienst nicht erreichbar, weist
ein dauerhaftes Banner mit Sprung zur Diagnose darauf hin. Fehlermeldungen
bleiben kurz; technische Details sind bei Bedarf aufklappbar. `Strg+,` öffnet
die Einstellungen, `Strg+S` speichert sie, `Strg+R` startet eine Prüfung und
`Strg+?` zeigt alle Tastenkürzel. Fenstergröße und Einführungsstand werden lokal
als reine Darstellungspräferenzen gespeichert.

Neben dem nächsten täglichen Timerstart zeigt die Oberfläche den letzten
erfolgreichen API-Lauf und die aus `check_interval_days` berechnete tatsächliche
API-Fälligkeit an.

Die Zuständigkeiten sind bewusst getrennt: `SettingsPage` besitzt das
Konfigurationsformular, `DashboardPage` die Übersicht mit Prüfung, Status und
Timer, `ReportPage`, `HardwarePage` und `DiagnosticsPage` die gleichnamigen
Unteransichten, `NavigationHost` die Navigation zwischen ihnen,
`CommandRunner` die asynchrone Prozessausführung und `TimerController` die
fest begrenzten systemd-Benutzerbefehle. `RadarWindow` verbindet diese Teile
und koordiniert nur Speichern, Prüfablauf und Statusanzeige.

Die GUI startet stets das im selben Verzeichnis liegende `model_radar.py`.
Dadurch können ein Projektstand und eine ältere installierte CLI nicht
versehentlich miteinander vermischt werden. Bei einem fehlgeschlagenen Befehl
erscheint die letzte konkrete Fehlerzeile zusätzlich in der Statusanzeige.

Lokaler Start aus dem Projektverzeichnis:

```bash
python3 model_radar_gui.py --config config.json
```

Displayfreier Vorabtest:

```bash
python3 model_radar_gui.py --config config.json --check
```

Die API-Adresse ist absichtlich nicht editierbar. Zustands- und Berichtspfade
müssen unter `~/.local/share/qwen35-model-radar/` liegen. Andere Herausgeber
können im Formular nur dadurch aufgenommen werden, dass der Benutzer sie dort
ausdrücklich ergänzt.

## Kontrollierte Installation oder Aktualisierung

Nach einem erfolgreichen Test installiert oder aktualisiert das idempotente
Installationsskript alle Komponenten. Eine vorhandene Konfiguration bleibt
erhalten; ein bereits aktiver Timer wird neu gestartet, ein deaktivierter Timer
bleibt deaktiviert:

```bash
./install.sh
```

Für einen isolierten Installationstest ohne systemd-Benutzerzugriff:

```bash
./install.sh --target-home /absoluter/testpfad --no-systemd
```

Serverinstallation ohne GTK und ohne Desktop-Starter:

```bash
./install.sh --headless
python3 release_check.py --headless
```

Ohne erreichbaren systemd-Benutzerdienst beendet der Installer die Installation
erfolgreich und weist auf manuelle Prüfungen hin. `--headless` lässt eine bereits
installierte GUI unangetastet. Die normale Installation prüft GUI-Abhängigkeiten
vor dem Kopieren und legt einen Starter mit absolutem Pfad an.

Die Python-Dateien liegen dabei gemeinsam im Anwendungsverzeichnis, damit CLI
und GUI dieselbe geprüfte Logik importieren. Die entsprechenden manuellen
Schritte lauten:

```bash
mkdir -p ~/.local/bin ~/.config/qwen35 \
  ~/.local/share/qwen35-model-radar/app \
  ~/.local/share/applications ~/.config/systemd/user
install -m 755 model_radar.py model_radar_gui.py model_radar_diagnostics.py \
  ~/.local/share/qwen35-model-radar/app/
install -m 644 radar_gui_logic.py radar_gui_components.py radar_platform.py \
  ~/.local/share/qwen35-model-radar/app/
ln -sfn ~/.local/share/qwen35-model-radar/app/model_radar.py \
  ~/.local/bin/qwen35-model-radar
ln -sfn ~/.local/share/qwen35-model-radar/app/model_radar_gui.py \
  ~/.local/bin/qwen35-model-radar-gui
test -e ~/.config/qwen35/model-radar.json || \
  install -m 600 config.json ~/.config/qwen35/model-radar.json
install -m 644 qwen35-model-radar.service qwen35-model-radar.timer \
  ~/.config/systemd/user/
install -m 644 qwen35-model-radar.desktop \
  ~/.local/share/applications/
systemctl --user daemon-reload
systemctl --user restart qwen35-model-radar.timer
```

Die bedingte Installation der Konfiguration erhält vorhandene GUI-/CLI-
Einstellungen. Nach der Installation erscheint **LLM-Modellradar** in der
Anwendungsübersicht des Desktops. Alternativ kann es über
`~/.local/bin/qwen35-model-radar-gui` gestartet werden.

Manuelle erste echte Prüfung:

```bash
systemctl --user start qwen35-model-radar.service
journalctl --user -u qwen35-model-radar.service -n 100 --no-pager
```

Beim ersten echten Lauf wird standardmäßig nur der Ausgangsbestand gespeichert;
es erscheint keine Benachrichtigung. Das verhindert eine Meldungsflut für
bereits existierende Repositories. Der Bericht listet diese dennoch als neue
Treffer. Wer das anders möchte, setzt `notify_on_first_run` auf `true`.

Erst nach Prüfung des Berichts den Timer aktivieren:

```bash
systemctl --user enable --now qwen35-model-radar.timer
systemctl --user list-timers qwen35-model-radar.timer
```

Der Timer führt täglich eine sehr kleine lokale Fälligkeitsprüfung aus. Nur wenn
`check_interval_days` abgelaufen ist, wird die API kontaktiert. Dadurch kann das
Intervall in der GUI in Tagen geändert werden, während `Persistent=true` eine
verpasste tägliche Fälligkeitsprüfung nachholt. `RandomizedDelaySec=1h` verteilt
die API-Last. „Jetzt prüfen“ in der GUI ignoriert das Intervall einmalig.

## Manuelle Prüfung und Testmodus

Nach der Installation:

```bash
~/.local/bin/qwen35-model-radar --dry-run \
  --config ~/.config/qwen35/model-radar.json
```

Ein regulärer sofortiger Lauf ist möglich mit:

```bash
systemctl --user start qwen35-model-radar.service
```

Oder ohne Terminal über **Jetzt prüfen** in der GUI. Der CLI-Schalter `--force`
ignoriert das konfigurierte Intervall einmalig.

`--dry-run` schreibt weder Zustand noch Bericht und versendet niemals eine
Benachrichtigung. `--no-notify` schreibt dagegen Zustand und Bericht, unterdrückt
aber einmalig die Desktop-Meldung. Eine Fixture kann auch installiert über
`--fixture /pfad/zur/antwort.json` verwendet werden.

`--headless` speichert Bericht und Zustand ohne Desktop-Benachrichtigung:

```bash
~/.local/bin/qwen35-model-radar --headless --force
```

Dieser Lauf benötigt weder GTK noch systemd noch einen Display-Server. Auch ohne
den Schalter werden Benachrichtigungen ausgelassen, wenn keine grafische Sitzung
oder kein `notify-send` verfügbar ist. Schlägt eine tatsächlich gestartete
Benachrichtigung fehl, bleibt der bisherige Zustand für einen erneuten Lauf erhalten.

## Lebenszyklus und Schema-Migrationen

Der Radar unterscheidet `NEU`, `AKTUALISIERT`, `ENTFERNT` und
`WIEDER ERSCHIENEN`. „Entfernt“ bedeutet dabei präzise, dass ein zuvor passendes
Repository in einer vollständig erfolgreichen Prüfung nicht mehr unter den
passenden API-Ergebnissen vorkommt; auch eine bewusste Filteränderung kann dies
auslösen. Abwesende Einträge bleiben im Zustand erhalten, damit ein späteres
Wiederauftauchen erkannt wird.

`config.json` verwendet derzeit Schema 2, `state.json` Schema 2. Bestehende
Konfigurationen ohne Versionsfeld werden beim Laden mit dem konservativen
Filtermodus ergänzt; Konfigurationen aus Schema 1 erhalten zusätzlich die
Aufbewahrungsfrist. Zustände aus Schema 1 werden im Speicher um Anwesenheit und
Abwesenheitszeitpunkt erweitert und erst nach einer vollständig erfolgreichen
Prüfung atomar als Schema 2 gespeichert. Neuere, unbekannte Schemaversionen
werden kontrolliert abgelehnt, statt möglicherweise Daten falsch zu deuten.

`absent_retention_days` begrenzt, wie lange bereits abwesende Repositories im
Zustand verbleiben. Der Standard beträgt 365 Tage, `0` deaktiviert die
Bereinigung. Neu entfernte Modelle bleiben mindestens bis zum folgenden
erfolgreichen Lauf erhalten. Nach einer Bereinigung wird ein späteres Auftauchen
wieder als `NEU` statt als `WIEDER ERSCHIENEN` erkannt.
Jeder Prüfbericht nennt die Anzahl und Repository-Namen der in diesem Lauf
bereinigten Datensätze. Im Dry-Run werden dieselben Einträge nur als zur
Bereinigung vorgemerkt angezeigt; Zustand und Bericht bleiben unverändert.

## Release-Prüfung

Der vollständige lokale Release-Check kompiliert alle Module, startet die
Unit-/Integrationstests, führt die echten GTK-Workflows unter Xvfb aus und
vergleicht eine isolierte Installation bytegenau mit den Quelldateien:

```bash
python3 release_check.py
```

Mit `python3 release_check.py --live-api` wird zusätzlich der optionale
Hugging-Face-Vertragstest ausgeführt.

## Filtergrenzen

Parameterzahlen stammen bevorzugt aus API-Metadaten und nur ersatzweise aus
Tags oder dem Repository-Namen. Ist keine Zahl erkennbar, wird das Repository
konservativ verworfen und im Abschnitt „Filterdiagnose“ genannt. GGUF-Varianten
werden anhand von Tags und `.gguf`-Dateien erkannt; Quantisierungen wie
`Q4_K_M` werden aus Dateinamen extrahiert. Der voreingestellte Modus
`conservative` behält Kandidaten mit unbekannter oder anderer Pipeline als
deutlich gekennzeichneten Hinweis. `strict_text` akzeptiert ausschließlich
Repositories, deren Pipeline in `preferred_pipeline_tags` steht; unbekannte und
abweichende Pipelines erscheinen in der Filterdiagnose. Beide Modi liefern
weiterhin keine Aussage über Kompatibilität oder Qualität.

## Diagnose

Die GUI wertet die Diagnose maschinenlesbar aus. Sie zeigt zusätzlich die für
den aktuellen Prozess sichtbaren Grafikadapter, deren Treiber und – sofern der
Treiber dies unprivilegiert bereitstellt – den dedizierten VRAM. Die Erkennung
liest nur DRM/sysfs-Attribute; für NVIDIA wird optional das vorhandene
`nvidia-smi` mit einer festen, lesenden Abfrage verwendet. Sie installiert keine
Treiber, nutzt kein `sudo` und schreibt nie an Hardware-Schnittstellen.

Je nach Distribution, Container- oder Flatpak-Sandbox und Sicherheitsrichtlinie
kann die Hardware absichtlich teilweise oder vollständig verborgen sein. In
diesem Fall meldet Modellradar die Einschränkung statt aus fehlenden Daten zu
schließen, dass keine dGPU vorhanden ist. Die Anzeige ist eine Bestandsaufnahme
und noch keine Kompatibilitäts- oder Modell-Empfehlung.

Für Skripte ist derselbe JSON-Vertrag direkt verfügbar:

```bash
python3 model_radar_diagnostics.py --json
```

### Hardware-Eignung für GGUF-Varianten

`python3 model_radar.py --config config.json --recommendations` verbindet nur auf
ausdrückliche Anforderung die sichtbare, lokal gelesene Grafikhardware mit
öffentlichen Hugging-Face-Dateimetadaten. Es bewertet konkrete GGUF-Dateien als
„gut passend“, „eingeschränkt“, „nicht empfohlen“ oder „unbekannt“. Modelle
werden dabei weder heruntergeladen noch ausgeführt. Die Ausgabe nennt ihre
konservative Speicherformel und bleibt bei fehlendem VRAM oder Dateigrößenwert
bewusst bei „unbekannt“.

```bash
systemctl --user status qwen35-model-radar.service qwen35-model-radar.timer
journalctl --user -u qwen35-model-radar.service --since '14 days ago'
systemd-analyze --user verify ~/.config/systemd/user/qwen35-model-radar.service \
  ~/.config/systemd/user/qwen35-model-radar.timer
cat ~/.local/share/qwen35-model-radar/latest-report.txt
```

- Exitcode 2: Konfiguration, API, Rate Limit, Timeout oder Benachrichtigung
- Exitcode 3: lokaler Dateizugriff fehlgeschlagen
- Keine Meldung bei unverändertem Stand ist beabsichtigt.
- Bei einem API-Fehler bleibt der bisherige Zustand unangetastet.
- Falls der Bericht geschrieben wurde, aber `notify-send` scheitert, zeigt der
  Dienst einen Fehler. Der Zustand bleibt unverändert, sodass die Meldung beim
  nächsten Lauf erneut versucht wird. Scheitert erst das Zustandsschreiben nach
  einer erfolgreichen Meldung, kann die Meldung wiederholt werden; Treffer gehen
  dadurch nicht verloren.

## Deinstallation

```bash
systemctl --user disable --now qwen35-model-radar.timer
rm ~/.config/systemd/user/qwen35-model-radar.timer \
  ~/.config/systemd/user/qwen35-model-radar.service \
  ~/.local/bin/qwen35-model-radar \
  ~/.local/bin/qwen35-model-radar-gui \
  ~/.config/qwen35/model-radar.json \
  ~/.local/share/applications/qwen35-model-radar.desktop
rm -r ~/.local/share/qwen35-model-radar/app
systemctl --user daemon-reload
```

Der gespeicherte Zustand und Bericht bleiben dabei zur Wiederherstellung
erhalten. Wenn sie bewusst ebenfalls gelöscht werden sollen:

```bash
rm -r ~/.local/share/qwen35-model-radar
```

## Datenschutz und Sicherheit

Das Skript sendet nur öffentliche GET-Anfragen an
`https://huggingface.co/api/models`; Pagination auf andere Hosts wird
abgelehnt. Es startet ausschließlich `notify-send` mit einer Argumentliste,
niemals über eine Shell. Es verändert weder lokale Modelle noch Jan-, MCP- oder
sonstige LLM-Konfigurationen.
