# SPDX-License-Identifier: GPL-3.0-or-later
import json
import io
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import model_radar as radar


ROOT = Path(__file__).resolve().parents[1]
CONFIG = radar.validate_config(radar.load_json(ROOT / "config.json", "Konfiguration"))


class ModelRadarTests(unittest.TestCase):
    def fixture(self, name):
        return radar.load_json(ROOT / "tests" / "fixtures" / name, "Testdaten")

    def test_filtert_namespace_groesse_und_erkennt_quantisierungen(self):
        selected, rejected = radar.select_candidates(self.fixture("scan_initial.json"), CONFIG)
        self.assertEqual([item.repo_id for item in selected], [
            "ggml-org/gemma-4-9b-it-GGUF", "Qwen/Qwen4-4B-Instruct"
        ])
        gguf = selected[0]
        self.assertEqual(gguf.parameters_b, 9.0)
        self.assertEqual(gguf.quantizations, ("Q4_K_M", "Q8_0"))
        self.assertTrue(any("27B außerhalb" in item for item in rejected))
        self.assertTrue(any("Herausgeber nicht freigegeben" in item for item in rejected))

    def test_neu_aktualisiert_und_unveraendert(self):
        initial, _ = radar.select_candidates(self.fixture("scan_initial.json"), CONFIG)
        state = radar.updated_state(initial, {"repositories": {}}, "2026-09-02T00:00:00+00:00")
        updated, _ = radar.select_candidates(self.fixture("scan_updated.json"), CONFIG)
        changes = radar.compare(updated, state)
        self.assertEqual([(status, item.repo_id) for status, item, _ in changes], [
            ("NEU", "openai/gpt-oss-12b-instruct"),
            ("AKTUALISIERT", "Qwen/Qwen4-4B-Instruct"),
        ])

    def test_entfernt_und_wieder_erschienen_werden_einmalig_erkannt(self):
        candidates, _ = radar.select_candidates(self.fixture("scan_initial.json"), CONFIG)
        initial = radar.updated_state(candidates, {"repositories": {}}, "2026-09-01T00:00:00+00:00")
        removed = radar.compare(candidates[:1], initial)
        self.assertEqual([(status, item.repo_id) for status, item, _ in removed], [
            ("ENTFERNT", candidates[1].repo_id),
        ])
        absent_state = radar.updated_state(candidates[:1], initial, "2026-09-08T00:00:00+00:00")
        self.assertEqual(radar.compare(candidates[:1], absent_state), [])
        reappeared = radar.compare(candidates, absent_state)
        self.assertEqual([(status, item.repo_id) for status, item, _ in reappeared], [
            ("WIEDER ERSCHIENEN", candidates[1].repo_id),
        ])
        record = absent_state["repositories"][candidates[1].repo_id]
        self.assertFalse(record["present"])
        self.assertEqual(record["missing_since"], "2026-09-08T00:00:00+00:00")

    def test_state_v1_wird_auf_v2_migriert(self):
        old = {
            "schema_version": 1,
            "last_successful_check": "2026-09-01T00:00:00+00:00",
            "repositories": {"Qwen/Qwen4-4B": {"sha": "alt", "last_seen": "damals"}},
        }
        migrated = radar.migrate_state(old)
        self.assertEqual(migrated["schema_version"], 2)
        self.assertTrue(migrated["repositories"]["Qwen/Qwen4-4B"]["present"])
        self.assertIsNone(migrated["repositories"]["Qwen/Qwen4-4B"]["missing_since"])
        self.assertNotIn("present", old["repositories"]["Qwen/Qwen4-4B"])

    def test_config_ohne_schema_wird_migriert(self):
        old = dict(CONFIG)
        old.pop("schema_version")
        old.pop("filter_mode")
        migrated = radar.validate_config(old)
        self.assertEqual(migrated["schema_version"], 2)
        self.assertEqual(migrated["filter_mode"], "conservative")
        self.assertEqual(migrated["absent_retention_days"], 365)

    def test_config_v1_erhaelt_eine_aufbewahrungsfrist(self):
        old = dict(CONFIG, schema_version=1)
        old.pop("absent_retention_days")
        migrated = radar.validate_config(old)
        self.assertEqual(migrated["schema_version"], 2)
        self.assertEqual(migrated["absent_retention_days"], 365)

    def test_config_migration_sichert_original_und_speichert_v2_einmalig(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            old = dict(CONFIG, schema_version=1, custom_note="bleibt erhalten")
            old.pop("absent_retention_days")
            original = (json.dumps(old, ensure_ascii=False) + "\n").encode("utf-8")
            path.write_bytes(original)

            config = radar.load_config(path, persist_migration=True)
            self.assertEqual(config["schema_version"], 2)
            self.assertEqual(config["custom_note"], "bleibt erhalten")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), config)
            backup = path.with_name("config.json.schema-v1.bak")
            self.assertEqual(backup.read_bytes(), original)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)

            radar.load_config(path, persist_migration=True)
            self.assertEqual(list(Path(directory).glob("*.bak*")), [backup])

    def test_state_migration_sichert_original_auch_ohne_api_pruefung(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            original = b'{"schema_version":1,"repositories":{"Qwen/Alt":{"sha":"abc"}}}\n'
            path.write_bytes(original)
            state, first_run = radar.load_state(path, persist_migration=True)
            self.assertFalse(first_run)
            self.assertEqual(state["schema_version"], 2)
            self.assertTrue(state["repositories"]["Qwen/Alt"]["present"])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), state)
            self.assertEqual(path.with_name("state.json.schema-v1.bak").read_bytes(), original)

    def test_migration_rollt_nach_fehler_beim_ersetzen_zurueck(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            old = dict(CONFIG, schema_version=1)
            old.pop("absent_retention_days")
            original = json.dumps(old).encode("utf-8")
            path.write_bytes(original)
            real_write = radar.atomic_write

            def fail_after_replace(target, content, mode=0o600):
                real_write(target, content, mode)
                raise OSError("Fehler nach Ersatz")

            with mock.patch("model_radar.atomic_write", side_effect=fail_after_replace):
                with self.assertRaisesRegex(OSError, "Fehler nach Ersatz"):
                    radar.load_config(path, persist_migration=True)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(path.with_name("config.json.schema-v1.bak").read_bytes(), original)

    def test_migration_aendert_original_nicht_wenn_sicherung_fehlgeschlaegt(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            original = b'{"schema_version":1,"repositories":{}}'
            path.write_bytes(original)
            with mock.patch("model_radar.atomic_write_bytes", side_effect=OSError("Sicherung voll")):
                with self.assertRaisesRegex(OSError, "Sicherung voll"):
                    radar.load_state(path, persist_migration=True)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(Path(directory).glob("*.bak*")), [])

    def test_migration_ueberschreibt_vorhandene_sicherung_nicht(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            old_backup = path.with_name("state.json.schema-v1.bak")
            old_backup.write_bytes(b"fruehere Sicherung")
            original = b'{"schema_version":1,"repositories":{}}'
            path.write_bytes(original)
            radar.load_state(path, persist_migration=True)
            self.assertEqual(old_backup.read_bytes(), b"fruehere Sicherung")
            self.assertEqual(
                path.with_name("state.json.schema-v1.bak.1").read_bytes(), original,
            )

    def test_migration_unterbleibt_bei_ungueltiger_datei_und_dry_run(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text('{"schema_version": 1}', encoding="utf-8")
            with self.assertRaises(radar.RadarError):
                radar.load_config(path, persist_migration=True)
            self.assertEqual(list(Path(directory).glob("*.bak*")), [])

            old = dict(CONFIG, schema_version=1)
            old.pop("absent_retention_days")
            path.write_text(json.dumps(old), encoding="utf-8")
            radar.load_config(path)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema_version"], 1)
            self.assertEqual(list(Path(directory).glob("*.bak*")), [])

    def test_migration_meldet_sicherung_wenn_rollback_scheitert(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            old = dict(CONFIG, schema_version=1)
            old.pop("absent_retention_days")
            original = json.dumps(old).encode("utf-8")
            path.write_bytes(original)
            real_write = radar.atomic_write
            real_write_bytes = radar.atomic_write_bytes

            def fail_after_replace(target, content, mode=0o600):
                real_write(target, content, mode)
                raise OSError("Fehler nach Ersatz")

            def fail_rollback(target, content, mode=0o600):
                if target == path and content == original:
                    raise OSError("Rollback blockiert")
                real_write_bytes(target, content, mode)

            with mock.patch("model_radar.atomic_write", side_effect=fail_after_replace):
                with mock.patch("model_radar.atomic_write_bytes", side_effect=fail_rollback):
                    with self.assertRaisesRegex(radar.RadarError, "Original liegt in"):
                        radar.load_config(path, persist_migration=True)
            self.assertNotEqual(path.read_bytes(), original)
            self.assertEqual(path.with_name("config.json.schema-v1.bak").read_bytes(), original)

    def test_lange_abwesende_modelle_werden_nach_der_frist_entfernt(self):
        old = {
            "schema_version": 2,
            "repositories": {
                "Qwen/Alt": {
                    "present": False,
                    "missing_since": "2025-09-14T00:00:00+00:00",
                },
                "Qwen/Kuerzlich": {
                    "present": False,
                    "missing_since": "2026-09-14T00:00:00+00:00",
                },
            },
        }
        updated, pruned = radar.update_state_with_pruning(
            [], old, "2026-09-15T00:00:00+00:00", 365,
        )
        self.assertNotIn("Qwen/Alt", updated["repositories"])
        self.assertIn("Qwen/Kuerzlich", updated["repositories"])
        self.assertEqual(pruned, ("Qwen/Alt",))

    def test_pruefbericht_nennt_alle_bereinigten_repositories(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            radar.atomic_write(state_path, json.dumps({
                "schema_version": 2,
                "last_successful_check": "2026-09-01T00:00:00+00:00",
                "repositories": {
                    "Qwen/SehrAlt": {
                        "present": False,
                        "missing_since": "2025-01-01T00:00:00+00:00",
                    },
                    "Qwen/AuchAlt": {
                        "present": False,
                        "missing_since": "2025-02-01T00:00:00+00:00",
                    },
                },
            }))
            result = radar.run_check(
                dict(CONFIG, absent_retention_days=365),
                state_path,
                fixture_path=ROOT / "tests" / "fixtures" / "scan_initial.json",
                now=radar.datetime(2026, 9, 15, tzinfo=radar.timezone.utc),
            )
        self.assertEqual(result.pruned_repositories, ("Qwen/AuchAlt", "Qwen/SehrAlt"))
        self.assertIn("Bereinigte Zustandsdatensätze: 2", result.report)
        self.assertIn("Entfernt nach 365 Tagen Abwesenheit (2):", result.report)
        self.assertIn("erst nach erfolgreichem Schreiben", result.report)
        self.assertIn("  - Qwen/AuchAlt", result.report)
        self.assertIn("  - Qwen/SehrAlt", result.report)

    def test_dry_run_kennzeichnet_bereinigung_als_nur_vorgemerkt(self):
        report = radar.render_report(
            [], [], [], "2026-09-15T00:00:00+00:00", True, False,
            ("Qwen/Alt",), 30,
        )
        self.assertIn("Zur Bereinigung vorgemerkt: 1", report)
        self.assertIn("Würde entfernen nach 30 Tagen Abwesenheit", report)

    def test_neu_entferntes_modell_bleibt_trotz_kurzer_frist_erhalten(self):
        candidates, _ = radar.select_candidates(self.fixture("scan_initial.json"), CONFIG)
        old = radar.updated_state(candidates, {"repositories": {}}, "2026-09-01T00:00:00+00:00")
        updated = radar.updated_state([], old, "2026-09-15T00:00:00+00:00", 1)
        self.assertEqual(len(updated["repositories"]), len(candidates))
        self.assertTrue(all(not item["present"] for item in updated["repositories"].values()))

    def test_aufbewahrungsfrist_null_deaktiviert_bereinigung(self):
        old = {
            "repositories": {
                "Qwen/Alt": {"present": False, "missing_since": "2020-01-01T00:00:00+00:00"},
            },
        }
        updated = radar.updated_state([], old, "2026-09-15T00:00:00+00:00", 0)
        self.assertIn("Qwen/Alt", updated["repositories"])

    def test_unbekannte_neuere_schemata_werden_abgelehnt(self):
        with self.assertRaisesRegex(radar.RadarError, "neuer"):
            radar.validate_config(dict(CONFIG, schema_version=99))
        with self.assertRaisesRegex(radar.RadarError, "neuer"):
            radar.migrate_state({"schema_version": 99, "repositories": {}})

    def test_regulaerer_lauf_migriert_v1_zustand_atomar_auf_v2(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            state_path = base / "state.json"
            report_path = base / "report.txt"
            config = dict(CONFIG, state_file=str(state_path), report_file=str(report_path))
            config_path = base / "config.json"
            radar.atomic_write(config_path, json.dumps(config))
            radar.atomic_write(state_path, json.dumps({
                "schema_version": 1,
                "last_successful_check": "2026-09-01T00:00:00+00:00",
                "repositories": {
                    "Qwen/Qwen3-4B": {
                        "sha": "alt", "last_modified": "2026-08-01T00:00:00Z",
                        "first_seen": "2026-08-01T00:00:00+00:00",
                        "last_seen": "2026-09-01T00:00:00+00:00",
                    },
                },
            }))
            with mock.patch("sys.stdout", new=io.StringIO()):
                result = radar.main([
                    "--config", str(config_path), "--state", str(state_path),
                    "--report", str(report_path), "--fixture",
                    str(ROOT / "tests" / "fixtures" / "scan_initial.json"), "--no-notify",
                ])
            self.assertEqual(result, 0)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["schema_version"], 2)
            self.assertFalse(state["repositories"]["Qwen/Qwen3-4B"]["present"])
            self.assertIn("[ENTFERNT] Qwen/Qwen3-4B", report_path.read_text(encoding="utf-8"))

    def test_strikter_filter_verwirft_fehlende_und_fremde_pipeline(self):
        models = [
            {"id": "Qwen/Qwen4-4B", "safetensors": {"total": 4_000_000_000}},
            {
                "id": "Qwen/Qwen4-4B-Audio", "safetensors": {"total": 4_000_000_000},
                "pipeline_tag": "automatic-speech-recognition",
            },
            {
                "id": "Qwen/Qwen4-4B-Instruct", "safetensors": {"total": 4_000_000_000},
                "pipeline_tag": "text-generation",
            },
        ]
        config = dict(CONFIG, filter_mode="strict_text", excluded_name_terms=[])
        selected, rejected = radar.select_candidates(models, config)
        self.assertEqual([item.repo_id for item in selected], ["Qwen/Qwen4-4B-Instruct"])
        self.assertEqual(sum("strikten Textmodell-Modus" in item for item in rejected), 2)

    def test_atomare_zustandsdatei(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "state.json"
            radar.atomic_write(target, '{"ok": true}\n')
            self.assertEqual(json.loads(target.read_text()), {"ok": True})
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_dry_run_schreibt_keinen_zustand(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "state.json"
            with mock.patch("sys.stdout", new=io.StringIO()):
                result = radar.main([
                    "--config", str(ROOT / "config.json"),
                    "--fixture", str(ROOT / "tests" / "fixtures" / "scan_initial.json"),
                    "--state", str(target),
                    "--dry-run",
                ])
            self.assertEqual(result, 0)
            self.assertFalse(target.exists())

    def test_pagination_verwirft_fremden_host(self):
        with self.assertRaises(radar.RadarError):
            radar.parse_link_next('<https://example.org/api/models?page=2>; rel="next"', CONFIG["api_url"])

    def test_pagination_verwirft_abweichenden_port(self):
        with self.assertRaises(radar.RadarError):
            radar.parse_link_next('<https://huggingface.co:444/api/models?page=2>; rel="next"', CONFIG["api_url"])

    def test_pagination_limit_verhindert_unvollstaendigen_zustand(self):
        config = dict(CONFIG, publishers={"Qwen": ["Qwen"]}, max_pages_per_query=1)
        with mock.patch(
            "model_radar.fetch_json_page",
            return_value=([{"id": "Qwen/Qwen4-4B"}], CONFIG["api_url"] + "?page=2"),
        ):
            with self.assertRaisesRegex(radar.RadarError, "unvollständiger Zustand"):
                radar.fetch_models(config)

    def test_ungueltige_konfiguration_liefert_kontrollierten_fehler(self):
        cases = (
            dict(CONFIG, families=[]),
            dict(CONFIG, parameter_min_b="keine Zahl"),
            dict(CONFIG, parameter_max_b=float("inf")),
            dict(CONFIG, request_retries=True),
            dict(CONFIG, retry_backoff_seconds=float("nan")),
            dict(CONFIG, retry_backoff_seconds=-1),
            dict(CONFIG, filter_mode="unbekannt"),
            dict(CONFIG, filter_mode="strict_text", preferred_pipeline_tags=[]),
            dict(CONFIG, absent_retention_days=-1),
            dict(CONFIG, absent_retention_days=1.5),
            dict(CONFIG, absent_retention_days=True),
            dict(CONFIG, state_file="gleich.json", report_file="gleich.json"),
        )
        for config in cases:
            with self.subTest(config=config):
                with self.assertRaises(radar.RadarError):
                    radar.validate_config(config)

    def test_nicht_endliche_parameterzahl_wird_ignoriert(self):
        self.assertEqual(radar.numeric_value(float("inf")), None)
        self.assertEqual(radar.numeric_value("1e999"), None)

    def test_rate_limit_wird_nach_retries_sauber_gemeldet(self):
        config = dict(CONFIG, request_retries=2, retry_backoff_seconds=0)
        error = urllib.error.HTTPError(
            CONFIG["api_url"], 429, "Too Many Requests", {}, None
        )
        self.addCleanup(error.close)
        with mock.patch("urllib.request.urlopen", side_effect=error) as opener:
            with self.assertRaisesRegex(radar.RadarError, "Rate Limit"):
                radar.fetch_json_page(CONFIG["api_url"], config)
        self.assertEqual(opener.call_count, 2)

    def test_pruefintervall(self):
        state = {"last_successful_check": "2026-09-10T12:00:00+00:00"}
        self.assertFalse(radar.check_is_due(state, 7, radar.datetime(2026, 9, 15, 12, tzinfo=radar.timezone.utc)))
        self.assertTrue(radar.check_is_due(state, 7, radar.datetime(2026, 9, 17, 12, tzinfo=radar.timezone.utc)))
        self.assertEqual(
            radar.next_check_at(state, 7),
            radar.datetime(2026, 9, 17, 12, tzinfo=radar.timezone.utc),
        )

    def test_run_check_liefert_strukturiertes_ergebnis_ohne_schreiben(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            result = radar.run_check(
                CONFIG,
                state_path,
                fixture_path=ROOT / "tests" / "fixtures" / "scan_initial.json",
                dry_run=True,
                now=radar.datetime(2026, 9, 15, 12, tzinfo=radar.timezone.utc),
            )
            self.assertTrue(result.due)
            self.assertEqual(result.checked_at, "2026-09-15T12:00:00+00:00")
            self.assertEqual(len(result.candidates), 2)
            self.assertEqual(len(result.changes), 2)
            self.assertFalse(result.notify_required)
            self.assertFalse(state_path.exists())

    def _notifying_result(self, directory):
        config = dict(CONFIG, notify_on_first_run=True)
        return radar.run_check(
            config,
            Path(directory) / "state.json",
            fixture_path=ROOT / "tests" / "fixtures" / "scan_initial.json",
            now=radar.datetime(2026, 9, 15, 12, tzinfo=radar.timezone.utc),
        )

    def test_fehler_beim_bericht_schreibt_keinen_zustand(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            report_path = Path(directory) / "report.txt"
            result = self._notifying_result(directory)
            with mock.patch("model_radar.atomic_write", side_effect=OSError("Bericht voll")) as writer:
                with self.assertRaisesRegex(OSError, "Bericht voll"):
                    radar.persist_check(result, state_path, report_path)
            self.assertEqual(writer.call_count, 1)
            self.assertFalse(state_path.exists())

    def test_fehler_beim_zustand_laesst_treffer_offen(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            report_path = Path(directory) / "report.txt"
            result = self._notifying_result(directory)
            real_atomic_write = radar.atomic_write
            calls = []

            def fail_second(path, text, mode=0o600):
                calls.append(path)
                if len(calls) == 2:
                    raise OSError("Zustand voll")
                real_atomic_write(path, text, mode)

            with mock.patch("model_radar.atomic_write", side_effect=fail_second):
                with mock.patch("model_radar.notify"):
                    with self.assertRaisesRegex(OSError, "Zustand voll"):
                        radar.persist_check(result, state_path, report_path)
            self.assertEqual(calls, [report_path, state_path])
            self.assertTrue(report_path.exists())
            self.assertFalse(state_path.exists())

    def test_benachrichtigungsfehler_laesst_treffer_offen(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            report_path = Path(directory) / "report.txt"
            result = self._notifying_result(directory)
            with mock.patch("model_radar.notify", side_effect=radar.RadarError("Bus fehlt")):
                with self.assertRaisesRegex(radar.RadarError, "Bus fehlt"):
                    radar.persist_check(result, state_path, report_path)
            self.assertTrue(report_path.exists())
            self.assertFalse(state_path.exists())

    def test_erfolgreicher_lauf_schreibt_bericht_und_zustand(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = dict(
                CONFIG,
                state_file=str(base / "state.json"),
                report_file=str(base / "report.txt"),
            )
            config_path = base / "config.json"
            radar.atomic_write(config_path, json.dumps(config))
            with mock.patch("sys.stdout", new=io.StringIO()):
                returncode = radar.main([
                    "--config", str(config_path),
                    "--fixture", str(ROOT / "tests" / "fixtures" / "scan_initial.json"),
                    "--no-notify",
                ])
            self.assertEqual(returncode, 0)
            self.assertIn("LLM-Modellradar", (base / "report.txt").read_text(encoding="utf-8"))
            state = json.loads((base / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(len(state["repositories"]), 2)

    def test_persistenz_verwirft_identische_zielpfade(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self._notifying_result(directory)
            target = Path(directory) / "beides.txt"
            with self.assertRaisesRegex(radar.RadarError, "unterschiedlich"):
                radar.persist_check(result, target, target)
            self.assertFalse(target.exists())

    def test_nicht_faelliger_lauf_kontaktiert_api_nicht(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = dict(CONFIG, state_file=str(base / "state.json"), report_file=str(base / "report.txt"))
            radar.atomic_write(base / "config.json", json.dumps(config))
            radar.atomic_write(base / "state.json", json.dumps({
                "schema_version": 1,
                "last_successful_check": radar.iso_now(),
                "repositories": {},
            }))
            with mock.patch("model_radar.fetch_models", side_effect=AssertionError("API darf nicht aufgerufen werden")):
                with mock.patch("sys.stdout", new=io.StringIO()) as output:
                    result = radar.main(["--config", str(base / "config.json")])
            self.assertEqual(result, 0)
            self.assertIn("Noch nicht fällig", output.getvalue())
            self.assertEqual(json.loads((base / "state.json").read_text())["schema_version"], 2)
            self.assertTrue((base / "state.json.schema-v1.bak").exists())

    def test_cli_dry_run_laesst_alte_schemata_und_dateien_unveraendert(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = dict(
                CONFIG, schema_version=1,
                state_file=str(base / "state.json"),
                report_file=str(base / "report.txt"),
            )
            config.pop("absent_retention_days")
            config_path = base / "config.json"
            state_path = base / "state.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            state_path.write_text(json.dumps({
                "schema_version": 1,
                "repositories": {},
            }), encoding="utf-8")
            original_config = config_path.read_bytes()
            original_state = state_path.read_bytes()
            with mock.patch("sys.stdout", new=io.StringIO()):
                returncode = radar.main([
                    "--config", str(config_path), "--dry-run",
                    "--fixture", str(ROOT / "tests" / "fixtures" / "scan_initial.json"),
                ])
            self.assertEqual(returncode, 0)
            self.assertEqual(config_path.read_bytes(), original_config)
            self.assertEqual(state_path.read_bytes(), original_state)
            self.assertEqual(list(base.glob("*.bak*")), [])
            self.assertFalse((base / "report.txt").exists())


if __name__ == "__main__":
    unittest.main()
