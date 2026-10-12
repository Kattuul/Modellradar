<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Lizenzierung von Modellradar

Die eigenen Projektdateien von Modellradar stehen unter **GNU General Public
License, Version 3 oder einer späteren Version** (`GPL-3.0-or-later`). Dies gilt
für Anwendungscode, Tests, Installations- und Buildskripte, CI-Dateien, eigene
Dokumentation, Konfigurationen, Test-Fixtures, Desktop-/systemd-Dateien und das
eigene SVG-Symbol, sofern eine Datei nicht ausdrücklich anders gekennzeichnet ist.
Der vollständige, unveränderte Lizenztext steht in [LICENSE](LICENSE).

Modellradar is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later version.

Modellradar is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Modellradar. If not, see <https://www.gnu.org/licenses/>.

## Beiträge und Fremdmaterial

Beiträge zu den eigenen Projektdateien werden unter `GPL-3.0-or-later` eingereicht.
Beitragende behalten ihre Urheberrechte; eine Rechteübertragung ist nicht vorgesehen.
Bitte nur Beiträge einreichen, für die die erforderlichen Rechte vorliegen, und
übernommenes Fremdmaterial samt Herkunft, Urheber- und Lizenzhinweisen kennzeichnen.
Die Projektlizenz beansprucht keine Rechte an fremdem Code oder fremden Daten.

Externe Abhängigkeiten, separat gebündelte Laufzeiten, Modellmetadaten und Modelle
sind von dieser Lizenzfestlegung ausgeschlossen. Generierte Analyseausgaben und
lokale Benutzerdateien werden nicht pauschal unter die Projektlizenz gestellt.
Siehe [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Installation und Weitergabe

Die native Installation kopiert diese Hinweise und `LICENSE` in ihr App-Verzeichnis.
Der Flatpak-Bauer legt sie unter `/app/share/licenses/io.github.Kattuul.Modellradar/`
und zusätzlich im Offline-Archiv ab. Bereits erzeugte Pakete ändern sich dadurch
nicht rückwirkend; sie müssen neu gebaut werden.

Bei einer Weitergabe müssen die einschlägigen Lizenzbedingungen erfüllt werden,
einschließlich des Zugangs zum zur Version gehörenden Quellcode und den benötigten
Buildskripten. Projektquellen: <https://github.com/Kattuul/Modellradar>.
Ein Link auf einen beliebigen aktuellen Stand ersetzt nicht den passenden Quellstand.
Noch nicht veröffentlichte lokale Änderungen müssen bei einer Weitergabe ebenfalls
als zugehöriger Quellcode bereitgestellt werden.

Für öffentliche Offline-Pakete ist zusätzlich die komponentenbezogene Prüfung der
mitgelieferten Laufzeiten erforderlich. Die Lizenzhinweise allein sind keine
Bestätigung, dass alle Pflichten für alle Laufzeitkomponenten erfüllt sind.
