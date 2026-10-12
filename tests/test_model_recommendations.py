# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

import model_recommendations as recommendations


GIB = 1024 ** 3


class RecommendationTests(unittest.TestCase):
    def test_bewertet_konkrete_gguf_dateigroesse_konservativ(self):
        models = [{
            "id": "ggml-org/Test-4B-GGUF",
            "siblings": [
                {"rfilename": "test.Q4_K_M.gguf", "size": 4 * GIB},
                {"rfilename": "test.Q8_0.gguf", "lfs": {"size": 7 * GIB}},
            ],
        }]
        graphics = {"adapters": [{"kind": "dediziert", "vram_bytes": 8 * GIB}]}
        result, notes = recommendations.recommend(models, graphics)
        self.assertEqual([item.rating for item in result], ["GUT PASSEND", "NICHT EMPFOHLEN"])
        self.assertEqual(result[0].variant.quantization, "Q4_K_M")
        self.assertTrue(any("werden nicht zusammengerechnet" in note for note in notes))

    def test_unbekannt_bei_fehlernder_groesse_oder_vram(self):
        models = [{"id": "ggml-org/Test", "siblings": [{"rfilename": "test.Q4_K_M.gguf"}]}]
        result, notes = recommendations.recommend(models, {"adapters": []})
        self.assertEqual(result[0].rating, "UNBEKANNT")
        self.assertIn("keine Dateigröße", result[0].reason)
        self.assertTrue(any("Kein verlässlicher" in note for note in notes))

    def test_keine_gguf_datei_wird_nicht_aus_parameterzahl_erraten(self):
        result, _notes = recommendations.recommend([{"id": "Qwen/Test", "siblings": []}], {"adapters": []})
        self.assertEqual(result[0].rating, "UNBEKANNT")
        self.assertIsNone(result[0].required_bytes)


if __name__ == "__main__":
    unittest.main()
