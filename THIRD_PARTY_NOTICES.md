<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Drittanbieter und externe Daten

Die Lizenz `GPL-3.0-or-later` gilt für die eigenen Modellradar-Dateien, nicht für
die nachfolgenden Projekte. Deren Urheberrechte und Lizenzbedingungen bleiben
unverändert. Diese Übersicht ist keine vollständige Stückliste der Flatpak-Laufzeit.

## Direkte Laufzeitabhängigkeiten

- **Python und Standardbibliothek:** PSF-Lizenzbedingungen und die in der jeweiligen
  Distribution enthaltenen weiteren Hinweise.
  <https://docs.python.org/3/license.html>
- **PyGObject:** LGPL-2.1-or-later.
  <https://pygobject.gnome.org/>
- **GTK4:** GNU LGPL; maßgeblich sind die Hinweise der tatsächlich ausgelieferten Version.
  <https://docs.gtk.org/gtk4/overview.html>
- **libadwaita:** maßgeblich sind die COPYING-Datei und die dateibezogenen Hinweise
  der tatsächlich ausgelieferten Version.
  <https://gitlab.gnome.org/GNOME/libadwaita>

Der Headless-Kern benötigt keine externen Python-Pakete. Native Installationen
verwenden vom Betriebssystem bereitgestellte Abhängigkeiten. Werkzeuge wie
systemd, libnotify/notify-send, Flatpak, OSTree, Cage und Xvfb sind eigenständige
Projekte und werden nicht durch die Modellradar-Lizenz umlizenziert.

## Gebündelte Flatpak-Laufzeiten

Das Offline-Archiv enthält unveränderte Laufzeit-Bundles für `org.gnome.Platform`
und `org.freedesktop.Platform.GL.default`. Sie enthalten zahlreiche weitere
Komponenten unter verschiedenen Lizenzen. **Mesa ist überwiegend MIT-lizenziert,
aber nicht jede Datei oder Komponente hat dieselbe Lizenz.**
<https://docs.mesa3d.org/license.html>

Die Laufzeit-Commits sind in `build-info.json` dokumentiert. Vor einer öffentlichen
Weitergabe des Offline-Pakets müssen anhand dieser konkreten Versionen alle
relevanten Lizenztexte, Copyright-Hinweise und Quellcodebereitstellungspflichten
geprüft und erfüllt werden. Ein Verweis auf die Upstream-Startseite allein genügt
dafür nicht. Vorhandene Hinweise in den Laufzeit-Bundles dürfen nicht entfernt werden.

Upstream-Builddefinitionen als Ausgangspunkt für diese Prüfung:

- GNOME Runtime: <https://gitlab.gnome.org/GNOME/gnome-build-meta>
- Freedesktop Runtime: <https://gitlab.com/freedesktop-sdk/freedesktop-sdk>

Die mitgelieferte Laufzeit ist durch diese Übersicht **nicht abschließend für eine
öffentliche Distribution geprüft**. Lizenzierung des eigenen Codes und Prüfung
der gesamten Laufzeit sind getrennte Aufgaben.

## Hugging Face und Modelle

Modellradar fragt externe Metadaten ab, lädt aber keine Modelle herunter.
Modellgewichte, Model Cards, externe Metadaten und sonstige Inhalte werden nicht
unter die Projektlizenz gestellt. Für deren Verwendung gelten die jeweiligen
Rechte und Bedingungen der Anbieter. Lokale Test-Fixtures sind Testdaten des Projekts.
