# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

import model_radar


ROOT = Path(__file__).resolve().parents[1]

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    GUI_AVAILABLE = True
except (ImportError, ValueError):
    GUI_AVAILABLE = False


@unittest.skipUnless(GUI_AVAILABLE, "GUI-Abhängigkeiten nicht installiert")
class InstallationTests(unittest.TestCase):
    def run_installer(self, target: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(ROOT / "install.sh"), "--target-home", str(target), "--no-systemd"],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_installiert_vollstaendig_und_versionsgleich(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            result = self.run_installer(target)
            self.assertEqual(result.returncode, 0, result.stderr)
            app_dir = target / ".local" / "share" / "qwen35-model-radar" / "app"
            self.assertEqual(
                {path.name for path in app_dir.glob("*.py")},
                {
                    "model_radar.py", "model_radar_gui.py", "model_radar_diagnostics.py",
                    "model_recommendations.py", "radar_gui_logic.py", "radar_gui_components.py", "radar_platform.py",
                },
            )
            cli = target / ".local" / "bin" / "qwen35-model-radar"
            gui = target / ".local" / "bin" / "qwen35-model-radar-gui"
            self.assertEqual(cli.resolve(), app_dir / "model_radar.py")
            self.assertEqual(gui.resolve(), app_dir / "model_radar_gui.py")
            version = subprocess.run(
                [str(cli), "--version"], text=True, capture_output=True, check=False
            )
            self.assertEqual((version.returncode, version.stdout.strip()), (0, model_radar.VERSION))
            config = target / ".config" / "qwen35" / "model-radar.json"
            self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)
            self.assertIsInstance(json.loads(config.read_text(encoding="utf-8")), dict)

    def test_update_erhaelt_vorhandene_konfiguration(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            config = target / ".config" / "qwen35" / "model-radar.json"
            config.parent.mkdir(parents=True)
            config.write_text('{"individuell": true}\n', encoding="utf-8")
            os.chmod(config, 0o644)
            result = self.run_installer(target)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(config.read_text(encoding="utf-8")), {"individuell": True})
            self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
