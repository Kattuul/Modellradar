# SPDX-License-Identifier: GPL-3.0-or-later
"""Lizenzhinweise und ihre Mitnahme bei Installation und Paketierung."""
from pathlib import Path
import subprocess
import tempfile
import unittest

import build_flatpak


ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "SPDX-License-Identifier: GPL-3.0-or-later"


class LicensingTests(unittest.TestCase):
    def test_license_text_and_or_later_grant(self):
        license_text = (ROOT / "LICENSE").read_text()
        self.assertIn("Version 3, 29 June 2007", license_text)
        self.assertIn("END OF TERMS AND CONDITIONS", license_text)
        self.assertIn("either version 3", (ROOT / "LICENSING.md").read_text())
        self.assertIn("GPL-3.0-or-later", (ROOT / "README.md").read_text())

    def test_own_files_have_license_identifier(self):
        files = [*ROOT.glob("*.py"), *ROOT.glob("*.sh"), *ROOT.glob("*.desktop"),
                 *ROOT.glob("*.service"), *ROOT.glob("*.timer"),
                 *ROOT.glob("*.md"), *ROOT.glob("*.license"),
                 *ROOT.joinpath("tests").rglob("*.py"),
                 *ROOT.joinpath("tests/fixtures").glob("*.license"),
                 *ROOT.joinpath("packaging").rglob("*"),
                 *ROOT.joinpath(".github/workflows").glob("*.yml")]
        for path in files:
            if not path.is_file() or path.suffix == ".pyc":
                continue
            with self.subTest(file=str(path.relative_to(ROOT))):
                self.assertIn(IDENTIFIER, path.read_text(encoding="utf-8")[:250])
                if path.suffix == ".sh" or path.name in {"model_radar.py", "model_radar_gui.py"}:
                    self.assertTrue(path.read_text().startswith("#!"))
        for path in (ROOT / "tests/fixtures").glob("*.json"):
            self.assertTrue(path.with_suffix(".json.license").is_file())

    def test_flatpak_license_copy_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "licenses" / build_flatpak.APP_ID
            build_flatpak.copy_license_files(destination)
            for name in build_flatpak.LICENSE_FILES:
                self.assertEqual((destination / name).read_bytes(), (ROOT / name).read_bytes())

    def test_headless_installation_includes_license_notices(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [str(ROOT / "install.sh"), "--target-home", directory, "--no-systemd", "--headless"],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            app = Path(directory) / ".local/share/qwen35-model-radar/app"
            for name in (*build_flatpak.LICENSE_FILES, "config.json.license"):
                self.assertEqual((app / name).read_bytes(), (ROOT / name).read_bytes())


if __name__ == "__main__":
    unittest.main()
