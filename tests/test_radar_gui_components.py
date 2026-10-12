# SPDX-License-Identifier: GPL-3.0-or-later
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

try:
    from radar_gui_components import CommandResult, CommandRunner, TimerController, TimerStatus
except (ImportError, ValueError) as exc:
    raise unittest.SkipTest(f"GTK nicht installiert: {exc}") from exc


class CommandRunnerTests(unittest.TestCase):
    def test_ausgabe_und_exitcode_werden_zusammengefuehrt(self):
        result = CommandRunner.execute(
            ["/usr/bin/python3", "-c", "import sys; print('ausgabe'); print('hinweis', file=sys.stderr)"]
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.output, "ausgabe\nhinweis\n")

    def test_fehlendes_programm_liefert_kontrollierten_fehler(self):
        result = CommandRunner.execute(["/definitiv/nicht/vorhanden"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Fehler:", result.output)

    def test_status_enthaelt_konkrete_letzte_fehlerzeile(self):
        result = CommandResult(
            2,
            "usage: qwen35-model-radar [optionen]\n"
            "qwen35-model-radar: error: unrecognized arguments: --force\n",
        )
        self.assertEqual(
            result.status_text(),
            "Fehlgeschlagen (Exitcode 2): "
            "qwen35-model-radar: error: unrecognized arguments: --force",
        )

    def test_erfolgsstatus_bleibt_knapp(self):
        self.assertEqual(CommandResult(0, "Ausgabe").status_text(), "Erfolgreich abgeschlossen.")

    def test_abbruchstatus_ist_eindeutig(self):
        self.assertEqual(CommandResult(-15, "", cancelled=True).status_text(), "Abgebrochen.")

    def test_lauf_kann_abgebrochen_werden(self):
        runner = CommandRunner()
        process = mock.Mock()
        process.poll.return_value = None
        runner._process = process
        self.assertTrue(runner.cancel())
        process.terminate.assert_called_once_with()


class TimerControllerTests(unittest.TestCase):
    def test_timerbefehle_sind_shell_freie_argumentlisten(self):
        self.assertEqual(
            TimerController.change_command(True),
            ["systemctl", "--user", "enable", "--now", "qwen35-model-radar.timer"],
        )
        self.assertEqual(
            TimerController.change_command(False),
            ["systemctl", "--user", "disable", "--now", "qwen35-model-radar.timer"],
        )
        command = TimerController.diagnostic_command()
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(Path(command[1]).name, "model_radar_diagnostics.py")
        self.assertEqual(command[2], "--json")

    def test_statusabfrage_und_anzeigetext(self):
        answers = iter(("enabled\n", "active\n", "Mon 2026-09-21 00:38:43 CEST\n"))
        commands = []

        def fake_executor(command, **_kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, stdout=next(answers), stderr="")

        status = TimerController.query_status(fake_executor)
        self.assertEqual(status, TimerStatus("enabled", "active", "Mon 2026-09-21 00:38:43 CEST"))
        self.assertIn("nächster Timerstart", status.display_text())
        self.assertEqual(len(commands), 3)

    def test_statusfehler_bleibt_lesbar(self):
        def failing_executor(_command, **_kwargs):
            raise OSError("systemctl fehlt")

        status = TimerController.query_status(failing_executor)
        self.assertIn("systemctl fehlt", status.display_text())

    def test_statusfehler_aus_stderr_bleibt_lesbar(self):
        def failing_executor(command, **_kwargs):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="Keine Verbindung zum Benutzerbus\n")

        status = TimerController.query_status(failing_executor)
        self.assertIn("Keine Verbindung zum Benutzerbus", status.display_text())


if __name__ == "__main__":
    unittest.main()
