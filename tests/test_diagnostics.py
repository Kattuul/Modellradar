# SPDX-License-Identifier: GPL-3.0-or-later
import subprocess
import unittest

import model_radar_diagnostics as diagnostics


class DiagnosticsTests(unittest.TestCase):
    def test_strukturierte_diagnose_ordnet_status_und_journal_zu(self):
        def executor(arguments, **_kwargs):
            unit = arguments[3] if arguments[0] == "systemctl" else "journal"
            if unit == diagnostics.SERVICE:
                output = (
                    "LoadState=loaded\nActiveState=failed\nSubState=failed\n"
                    "Result=exit-code\nExecMainCode=exited\nExecMainStatus=2\n"
                    "FragmentPath=/unit/service\n"
                )
            elif unit == diagnostics.TIMER:
                output = (
                    "LoadState=loaded\nActiveState=active\nSubState=waiting\n"
                    "UnitFileState=enabled\nLastTriggerUSec=gestern\n"
                    "NextElapseUSecRealtime=morgen\nFragmentPath=/unit/timer\n"
                )
            else:
                output = "Sep 15 radar FEHLER: API nicht erreichbar\n"
            return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr="")

        data = diagnostics.collect_diagnostic_data(executor)
        report = diagnostics.render_diagnostics(data)
        self.assertIn("strukturierte Dienstdiagnose", report)
        self.assertIn("Letzter Dienstlauf fehlgeschlagen: exit-code / Exitstatus 2", report)
        self.assertIn("Nächster Start: morgen", report)
        self.assertIn("FEHLER: API nicht erreichbar", report)
        self.assertEqual(data["findings"][0]["severity"], "error")
        self.assertEqual(data["findings"][0]["actions"], ["open_settings", "run_check"])
        self.assertIn("Konfiguration prüfen", data["findings"][0]["recommendation"])

    def test_fehlender_benutzerbus_bleibt_in_der_bewertung_sichtbar(self):
        def executor(arguments, **_kwargs):
            return subprocess.CompletedProcess(
                arguments, 1, stdout="", stderr="Keine Verbindung zum Benutzerbus",
            )

        report = diagnostics.collect_diagnostics(executor)
        self.assertIn("Dienststatus nicht lesbar: Keine Verbindung", report)
        self.assertIn("Timerstatus nicht lesbar: Keine Verbindung", report)
        self.assertIn("Journal nicht lesbar: Keine Verbindung", report)

    def test_inaktiver_timer_liefert_warnung_mit_aktivierungsaktion(self):
        def executor(arguments, **_kwargs):
            if arguments[0] == "journalctl":
                return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")
            unit = arguments[3]
            if unit == diagnostics.SERVICE:
                output = "LoadState=loaded\nActiveState=inactive\nResult=success\n"
            else:
                output = "LoadState=loaded\nActiveState=inactive\nUnitFileState=disabled\n"
            return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr="")

        data = diagnostics.collect_diagnostic_data(executor)
        timer_finding = next(item for item in data["findings"] if item["title"] == "Timer ist nicht aktiv")
        self.assertEqual(timer_finding["severity"], "warning")
        self.assertEqual(timer_finding["actions"], ["enable_timer"])


if __name__ == "__main__":
    unittest.main()
