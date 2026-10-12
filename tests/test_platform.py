# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import radar_platform


ROOT = Path(__file__).resolve().parents[1]


class PlatformTests(unittest.TestCase):
    def test_gpu_erkennung_liefert_sysfs_adapter_ohne_sonderrechte(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            device = root / "card1/device"
            device.mkdir(parents=True)
            (device / "vendor").write_text("0x1002\n")
            (device / "device").write_text("0x744c\n")
            (device / "mem_info_vram_total").write_text(str(16 * 1024 ** 3))
            (device / "driver").symlink_to("amdgpu")
            graphics = radar_platform.detect_graphics(root)
        self.assertEqual(graphics["adapters"][0]["vendor"], "AMD")
        self.assertEqual(graphics["adapters"][0]["kind"], "dediziert")
        self.assertEqual(graphics["adapters"][0]["vram_bytes"], 16 * 1024 ** 3)
        self.assertEqual(graphics["adapters"][0]["vram_source"], "DRM/sysfs")

    def test_nvidia_vram_bleibt_optional_und_lesend(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch("radar_platform.shutil.which", return_value="/usr/bin/nvidia-smi"):
            root = Path(directory)
            device = root / "card0/device"
            device.mkdir(parents=True)
            (device / "vendor").write_text("0x10de\n")
            command = mock.Mock(return_value=subprocess.CompletedProcess(
                [], 0, stdout="12288\n", stderr="",
            ))
            graphics = radar_platform.detect_graphics(root, command)
        self.assertEqual(graphics["adapters"][0]["vram_bytes"], 12 * 1024 ** 3)
        self.assertEqual(graphics["adapters"][0]["vram_source"], "nvidia-smi")
        self.assertEqual(command.call_args.args[0][1:], ["--query-gpu=memory.total", "--format=csv,noheader,nounits"])

    def test_headless_speichert_ohne_site_packages_und_desktop(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = json.loads((ROOT / "config.json").read_text())
            config["notify_on_first_run"] = True
            config["state_file"] = str(base / "state.json")
            config["report_file"] = str(base / "report.txt")
            (base / "config.json").write_text(json.dumps(config))
            environment = dict(os.environ)
            for key in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS"):
                environment.pop(key, None)
            for arguments in (["--headless"], []):
                result = subprocess.run([
                    sys.executable, "-S", str(ROOT / "model_radar.py"),
                    "--config", str(base / "config.json"), "--force",
                    "--fixture", str(ROOT / "tests/fixtures/scan_initial.json"), *arguments,
                ], env=environment, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue((base / "report.txt").is_file())
                self.assertTrue(json.loads((base / "state.json").read_text())["repositories"])
                (base / "state.json").unlink()

    def test_fehlender_systemd_dienst_betrifft_nur_automatik(self):
        def unavailable(*args, **kwargs):
            raise FileNotFoundError("systemctl fehlt")
        status = radar_platform.SystemdScheduler.query_status(unavailable)
        self.assertIn("Manuelle Prüfungen", status.display_text())

    def test_fehler_wird_nicht_durch_stdout_verdeckt(self):
        def failure(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, stdout="irreführend", stderr="Bus fehlt")
        self.assertEqual(radar_platform.SystemdScheduler.query_status(failure).error, "Bus fehlt")

    def test_fehlende_unit_wird_verstaendlich_benannt(self):
        def missing(command, **kwargs):
            return subprocess.CompletedProcess(command, 4, stdout="not-found\n", stderr="")
        error = radar_platform.SystemdScheduler.query_status(missing).error
        self.assertEqual(error, "Der Hintergrunddienst ist nicht installiert")

    def test_deaktivierter_timer_ist_keine_fehlende_systemfunktion(self):
        answers = iter(((1, "disabled"), (3, "inactive"), (0, "")))
        def executor(command, **kwargs):
            code, output = next(answers)
            return subprocess.CompletedProcess(command, code, stdout=output, stderr="")
        self.assertFalse(radar_platform.SystemdScheduler.query_status(executor).error)

    def test_desktop_faehigkeit_braucht_sitzung_und_programm(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch("radar_platform.shutil.which", return_value="/bin/notify-send"):
            self.assertFalse(radar_platform.desktop_notifications_available())
            os.environ.update(WAYLAND_DISPLAY="wayland-0", DBUS_SESSION_BUS_ADDRESS="unix:path=/test")
            self.assertTrue(radar_platform.desktop_notifications_available())


class HeadlessInstallationTests(unittest.TestCase):
    def test_serverinstallation_ohne_desktop_und_erreichbaren_systemd(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            tools = base / "tools"
            tools.mkdir()
            systemctl = tools / "systemctl"
            systemctl.write_text("#!/bin/sh\nexit 1\n")
            systemctl.chmod(0o755)
            result = subprocess.run([
                str(ROOT / "install.sh"), "--target-home", str(base / "home"), "--headless",
            ], env=dict(os.environ, PATH=str(tools) + os.pathsep + os.environ["PATH"]), capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Manuelle Prüfungen", result.stdout)
            app = base / "home/.local/share/qwen35-model-radar/app"
            self.assertEqual({p.name for p in app.glob("*.py")}, {
                "model_radar.py", "model_recommendations.py", "model_radar_diagnostics.py", "radar_platform.py",
            })
            self.assertFalse((base / "home/.local/share/applications").exists())
            result = subprocess.run([
                sys.executable, "-S", str(app / "model_radar.py"), "--version",
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
