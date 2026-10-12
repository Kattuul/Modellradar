# SPDX-License-Identifier: GPL-3.0-or-later
"""Executed inside the installed package; no API requests or model downloads."""
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, "/app/lib/modellradar")
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, Gtk
import radar_gui_logic as logic
from model_radar_gui import RadarWindow

assert Gtk.init_check(), "No display"
display = Gdk.Display.get_default()
assert display is not None
expected = sys.argv[1]
assert expected.lower() in type(display).__name__.lower(), type(display).__name__
app = Adw.Application(application_id="io.github.Kattuul.Modellradar", flags=Gio.ApplicationFlags.NON_UNIQUE)
app.register(None)
config_path = logic.default_gui_config_path()
window = RadarWindow(app, config_path)
window.settings_page.min_spin.set_value(3)
config = window.settings_page.collect_config()
logic.save_config(config_path, config)
assert json.loads(config_path.read_text())["parameter_min_b"] == 3
window.present()
fixture = Path("/tmp/fixture.json")
fixture.write_text(sys.argv[2])
subprocess.run(logic.radar_command(config_path, "--headless", "--force", "--fixture", str(fixture)), check=True)
window.load_report()
assert Path(config["report_file"]).is_file()
buffer = window.actions_page.output_view.get_buffer()
assert "Prüfbericht" in buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
assert json.loads(Path(config["state_file"]).read_text())["repositories"]
window.close()
print(f"Flatpak GUI, Einstellungen, Fixture-Prüfung und Bericht: OK ({type(display).__name__})")
