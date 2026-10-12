# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import build_flatpak
import model_radar
import radar_gui_logic as logic
import radar_platform


class FlatpakTests(unittest.TestCase):
    def test_first_start_creates_private_config_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with mock.patch.dict(os.environ, {
                "FLATPAK_ID": build_flatpak.APP_ID,
                "XDG_CONFIG_HOME": str(base / "config"), "XDG_DATA_HOME": str(base / "data"),
            }):
                path = logic.default_gui_config_path()
                config = json.loads(path.read_text())
                self.assertEqual(config["state_file"], str(base / "data/qwen35-model-radar/state.json"))
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                config["parameter_min_b"] = 3
                logic.save_config(path, config)
                self.assertEqual(logic.default_gui_config_path(), path)
                self.assertEqual(json.loads(path.read_text())["parameter_min_b"], 3)
                self.assertEqual(logic.validate_data_path(config["report_file"], "Bericht"), config["report_file"])
                with self.assertRaises(model_radar.RadarError):
                    logic.validate_data_path(str(base / "outside.txt"), "Bericht")

    def test_sandbox_never_calls_host_systemd(self):
        with mock.patch.dict(os.environ, {"FLATPAK_ID": build_flatpak.APP_ID}):
            executor = mock.Mock()
            self.assertIn("Flatpak", radar_platform.SystemdScheduler.query_status(executor).error)
            executor.assert_not_called()

    def test_wayland_with_fallback_without_host_access(self):
        self.assertIn("--socket=wayland", build_flatpak.PERMISSIONS)
        self.assertIn("--socket=fallback-x11", build_flatpak.PERMISSIONS)
        self.assertNotIn("--socket=x11", build_flatpak.PERMISSIONS)
        self.assertFalse(any("filesystem" in item or "talk-name" in item for item in build_flatpak.PERMISSIONS))

    def test_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "file"
            path.write_bytes(b"abc")
            self.assertEqual(build_flatpak.digest(path), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
