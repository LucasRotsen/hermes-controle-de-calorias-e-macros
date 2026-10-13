import contextlib
import io
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "macro-tracker" / "scripts"))
import macros  # noqa: E402

PROFILE = {
    "locale": "pt-BR", "timezone": "America/Sao_Paulo",
    "targets": {
        "default": {"kcal": 2000, "protein": 150, "carbs": 200, "fat": 65},
        "weekdays": {"fri": {"kcal": 2300, "reserve": {"label": "jantar livre", "kcal": 1000}}},
    },
}
FRIDAY = "2026-10-16"
THURSDAY = "2026-10-15"


class MacrosTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.journal = self.home / "journal"
        self.run_ok("profile", "set", json.dumps({**PROFILE, "journal_dir": str(self.journal)}))

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = macros.main(["--home", str(self.home), *argv])
        return code, out.getvalue(), err.getvalue()

    def run_ok(self, *argv):
        code, out, err = self.run_cli(*argv)
        self.assertEqual(code, 0, err)
        return out

    def log(self, payload):
        return self.run_ok("log", json.dumps(payload))

    def test_taco_item_scales_per_100g(self):
        out = self.log({"date": THURSDAY, "meal": "Almoço", "items": [{"taco": 3, "g": 150}]})
        self.assertIn("192 kcal", out)  # 128 kcal/100 g × 1.5
        self.assertIn("Faltam 1.808 kcal", out)

    def test_shortcut_and_custom_food(self):
        self.run_ok("shortcut", "add", json.dumps({"key": "cafe", "name": "Café", "kcal": 605, "protein": 37,
                                                   "carbs": 58, "fat": 25}))
        self.run_ok("food", "add", json.dumps({"key": "whey", "name": "Whey", "kcal": 400, "protein": 80,
                                               "carbs": 8, "fat": 6, "unit_g": 30}))
        out = self.log({"date": THURSDAY, "meal": "Manhã", "items": [{"shortcut": "cafe"}, {"food": "whey", "units": 1}]})
        self.assertIn("725 kcal", out)  # 605 + 120
        self.assertIn("P 61", out)  # 37 + 24

    def test_estimate_is_flagged(self):
        out = self.log({"date": THURSDAY, "meal": "Lanche",
                        "items": [{"name": "Pão de queijo", "kcal": 220, "protein": 5, "carbs": 25, "fat": 11}]})
        self.assertIn("~", out)

    def test_dry_run_does_not_save(self):
        self.run_ok("log", json.dumps({"date": THURSDAY, "items": [{"taco": 3, "g": 100}]}), "--dry-run")
        self.assertIn("nenhuma refeição", self.run_ok("today", "--date", THURSDAY, "--short"))

    def test_weekday_override_and_reserve(self):
        out = self.log({"date": FRIDAY, "meal": "Almoço", "items": [{"name": "Prato", "kcal": 700, "protein": 50,
                                                                    "carbs": 60, "fat": 20}]})
        self.assertIn("fora dela faltam 600 kcal", out)  # 2300 - 1000 - 700
        out = self.log({"date": FRIDAY, "meal": "Jantar livre", "free": True,
                        "items": [{"name": "Pizza", "kcal": 950, "protein": 40, "carbs": 100, "fat": 45}]})
        self.assertIn("usou 950 de 1.000", out)
        self.assertIn("Faltam 650 kcal", out)
        self.assertEqual(macros.targets_for(PROFILE, date.fromisoformat(FRIDAY))["protein"], 150)

    def test_undo_specific_entry_and_journal(self):
        first = self.log({"date": THURSDAY, "meal": "A", "items": [{"taco": 3, "g": 100}]})
        self.log({"date": THURSDAY, "meal": "B", "items": [{"taco": 3, "g": 100}]})
        entry_id = first.strip().splitlines()[-1].split("id: ")[1]
        self.run_ok("undo", entry_id)
        today = self.run_ok("today", "--date", THURSDAY)
        self.assertIn("128/2.000", today)
        self.assertNotIn(entry_id, today)
        note = (self.journal / f"{THURSDAY}.md").read_text()
        self.assertIn("· B ·", note)
        self.assertNotIn("· A ·", note)

    def test_over_target(self):
        out = self.log({"date": THURSDAY, "items": [{"name": "Festa", "kcal": 2500, "protein": 100, "carbs": 200,
                                                     "fat": 120}]})
        self.assertIn("Passou 500 kcal", out)

    def test_bad_inputs_fail_cleanly(self):
        for payload in ({"items": []}, {"items": [{"taco": 99999, "g": 10}]}, {"items": [{"taco": 3}]},
                        {"items": [{"shortcut": "nope"}]}, {"items": [{"kcal": 10}]}):
            code, _, err = self.run_cli("log", json.dumps(payload))
            self.assertEqual(code, 1)
            self.assertTrue(err.startswith("Error:"), err)
        code, _, err = self.run_cli("log", "{not json")
        self.assertEqual(code, 1)

    def test_missing_profile(self):
        self.home = self.home / "nobody"
        code, _, err = self.run_cli("today")
        self.assertEqual(code, 1)
        self.assertIn("No profile yet", err)

    def test_weight_average_and_trend(self):
        self.run_ok("weight", "82", "--date", "2026-10-08")
        out = self.run_ok("weight", "81,2", "--date", THURSDAY)
        self.assertIn("81,2 kg", out)
        self.assertIn("-0,8 kg", out)
        self.assertEqual(json.loads((self.home / "profile.json").read_text())["body"]["weight_kg"], 81.2)

    def test_week_summary(self):
        self.log({"date": THURSDAY, "items": [{"name": "X", "kcal": 2000, "protein": 145, "carbs": 1, "fat": 1}]})
        out = self.run_ok("week", "--end", FRIDAY, "--short")
        self.assertIn("2.000 kcal/dia", out)
        self.assertIn("1/7 dias", out)
        self.assertIn("na meta em 1/1", out)

    def test_search_synonyms_and_accents(self):
        self.assertIn("taco:3", self.run_ok("search", "arroz", "branco"))
        self.assertIn("Feijão, preto", self.run_ok("search", "feijao", "preto"))
        self.assertIn("Ovo, de galinha", self.run_ok("search", "ovos").splitlines()[0])

    def test_calc_katch_mcardle(self):
        out = self.run_ok("calc", "--weight", "90", "--height", "175", "--age", "35", "--sex", "M", "--bf", "20")
        self.assertIn("Katch-McArdle: 1925", out)  # 370 + 21.6 × 72 kg lean
        self.assertIn("protein 158 g", out)  # 2.2 × 72

    def set_water(self):
        self.run_ok("profile", "set", json.dumps({"water": {"target_ml": 3000, "containers": {"garrafa": 650}}}))

    def test_water_ml_litres_and_container(self):
        self.set_water()
        self.assertIn("+500 ml · hoje 0,5/3 L (faltam 2,5 L)", self.run_ok("water", "500ml", "--date", THURSDAY))
        self.assertIn("+650 ml", self.run_ok("water", "garrafa", "--date", THURSDAY))
        self.assertIn("+1.300 ml", self.run_ok("water", "Garrafas", "--count", "2", "--date", THURSDAY))
        out = self.run_ok("water", "1,5l", "--date", THURSDAY)
        self.assertIn("hoje 3,95/3 L ✅", out)
        short = self.run_ok("today", "--date", THURSDAY, "--short")
        self.assertIn("💧 3,95/3 L", short)
        note = (self.journal / f"{THURSDAY}.md").read_text()
        self.assertIn("water_ml: 3950", note)

    def test_water_rejects_ambiguous_amounts(self):
        self.set_water()
        for amount in ("2", "copo", "0ml", "6000"):
            code, _, err = self.run_cli("water", amount)
            self.assertEqual(code, 1, amount)
            self.assertTrue(err.startswith("Error:"), err)

    def test_undo_water(self):
        self.set_water()
        self.run_ok("water", "garrafa")
        self.assertIn("água (650 ml)", self.run_ok("undo"))

    def test_water_target_shows_even_without_logs(self):
        self.set_water()
        self.log({"date": THURSDAY, "items": [{"taco": 3, "g": 100}]})
        self.assertIn("💧 0/3 L (faltam 3 L)", self.run_ok("today", "--date", THURSDAY, "--short"))

    def test_week_water_average(self):
        self.set_water()
        self.run_ok("water", "2l", "--date", THURSDAY)
        self.run_ok("water", "3l", "--date", FRIDAY)
        out = self.run_ok("week", "--end", FRIDAY, "--short")
        self.assertIn("média 2,5 L/dia (2/7", out)
        self.assertNotIn("nenhum registro", out)

    def test_english_locale(self):
        self.run_ok("profile", "set", json.dumps({"locale": "en"}))
        out = self.log({"date": THURSDAY, "items": [{"taco": 3, "g": 1000}]})
        self.assertIn("1,280 kcal", out)
        self.assertIn("Left: 720 kcal", out)


if __name__ == "__main__":
    unittest.main()
