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

    def test_replies_show_food_and_water_left(self):
        self.set_water()
        meal = self.log({"date": THURSDAY, "items": [{"taco": 3, "g": 150}]})
        self.assertIn("Faltam 1.808 kcal", meal)
        self.assertIn("💧 Água: 0/3 L (faltam 3 L)", meal)
        water = self.run_ok("water", "garrafa", "--date", THURSDAY)
        self.assertIn("hoje 0,65/3 L (faltam 2,35 L)", water)
        self.assertIn("Faltam 1.808 kcal", water)

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

    PLAN = {"plan": {
        "free_meal": {"fri": "jantar"},
        "rules": ["sem frituras"],
        "meals": [
            {"key": "cafe", "name": "Café da manhã", "time": "08:00", "kcal": 600, "protein": 40, "carbs": 60, "fat": 20,
             "aliases": ["cafe"], "base": "pão, ovos", "swaps": ["pão francês"]},
            {"key": "almoco", "name": "Almoço", "time": "12:30", "kcal": 600, "protein": 50, "carbs": 60, "fat": 20},
            {"key": "lanche", "name": "Lanche", "time": "16:45", "kcal": 400, "protein": 30, "carbs": 40, "fat": 10},
            {"key": "jantar", "name": "Jantar", "time": "20:30", "kcal": 400, "protein": 30, "carbs": 40, "fat": 15,
             "aliases": ["janta"]},
        ]}}

    def test_next_without_plan_is_an_error(self):
        code, _, err = self.run_cli("next", "--date", THURSDAY, "--time", "08:00")
        self.assertEqual(code, 1)
        self.assertIn("No meal plan", err)

    def test_next_splits_what_is_left_over_remaining_meals(self):
        self.run_ok("profile", "set", json.dumps(self.PLAN))
        self.log({"date": THURSDAY, "time": "08:10", "meal": "Café da manhã",
                  "items": [{"name": "x", "kcal": 600, "protein": 40, "carbs": 60, "fat": 20}]})
        out = self.run_ok("next", "--date", THURSDAY, "--time", "12:00")
        self.assertIn("Próxima: Almoço (plano 12:30)", out)
        self.assertIn("Sobra no dia: 1.400 kcal", out)
        # 1,400 kcal over almoço+lanche+jantar (600/1,400): 600; protein 110 × 50/110 = 50
        self.assertIn("~600 kcal · P 50", out)
        self.assertIn("Depois: Lanche 16:45 · Jantar 20:30", out)
        self.assertIn("Regras: sem frituras", out)

    def test_next_skips_missed_meals_and_rebalances(self):
        self.run_ok("profile", "set", json.dumps(self.PLAN))
        out = self.run_ok("next", "--date", THURSDAY, "--time", "17:00")
        self.assertIn("Próxima: Lanche", out)
        # 2,000 kcal over lanche+jantar (400/800) = 1,000, capped at 1.5 × 400
        self.assertIn("~600 kcal · P 45 C 60 G 15", out)
        self.assertIn("Sem registro hoje: Café da manhã, Almoço", out)

    def test_next_late_breakfast_is_still_breakfast(self):
        self.run_ok("profile", "set", json.dumps(self.PLAN))
        out = self.run_ok("next", "--date", THURSDAY, "--time", "11:00")
        self.assertIn("Próxima: Café da manhã", out)
        self.assertIn("Base do plano: pão, ovos", out)
        self.assertIn("Trocas: pão francês", out)

    def test_next_meal_flag_and_alias(self):
        self.run_ok("profile", "set", json.dumps(self.PLAN))
        self.log({"date": THURSDAY, "meal": "Janta", "items": [{"name": "x", "kcal": 300, "protein": 20, "carbs": 30, "fat": 10}]})
        out = self.run_ok("next", "--date", THURSDAY, "--time", "07:00", "--meal", "almoco")
        self.assertIn("Próxima: Almoço", out)
        self.assertIn("Depois: Lanche 16:45", out)
        self.assertNotIn("Jantar 20:30", out)  # "Janta" was logged and claims the jantar slot

    def test_next_holds_back_the_reserve(self):
        self.run_ok("profile", "set", json.dumps(self.PLAN))
        out = self.run_ok("next", "--date", FRIDAY, "--time", "12:00")
        # Friday: 2,300 − 1,000 reserve = 1,300 over almoço+lanche (jantar is the free meal): 600/1,000 = 780
        self.assertIn("fora a reserva jantar livre: 1.300 kcal", out)
        self.assertIn("~780 kcal · P 65 C 78 G 26", out)  # the plan's mix scaled by 780/600
        self.assertIn("Próxima: Café da manhã", self.run_ok("next", "--date", FRIDAY, "--time", "08:00"))
        out = self.run_ok("next", "--date", FRIDAY, "--time", "20:30")
        self.assertIn("refeição livre de hoje", out)

    def test_next_when_day_is_over_target_and_all_done(self):
        self.run_ok("profile", "set", json.dumps(self.PLAN))
        self.log({"date": THURSDAY, "meal": "Almoço", "items": [{"name": "x", "kcal": 2100, "protein": 10, "carbs": 10, "fat": 10}]})
        out = self.run_ok("next", "--date", THURSDAY, "--time", "16:30")
        self.assertIn("passou 100 kcal", out)
        self.assertIn("~0 kcal", out)
        for meal in ("Café da manhã", "Lanche", "Jantar"):
            self.log({"date": THURSDAY, "meal": meal, "items": [{"name": "x", "kcal": 1, "protein": 0, "carbs": 0, "fat": 0}]})
        self.assertIn("Todas as refeições", self.run_ok("next", "--date", THURSDAY, "--time", "22:00"))

    def weigh(self, day, kg, *extra):
        return self.run_ok("weight", str(kg), "--date", day, *extra)

    def test_weight_metrics_and_checkin_first(self):
        self.weigh("2026-10-05", "118,0", "--bf", "28,6", "--metrics", '{"muscle_kg": 78.0, "visceral": 14}')
        out = self.run_ok("checkin", "--date", "2026-10-05")
        self.assertIn("Check-in · seg 05/10", out)
        self.assertIn("primeira pesagem", out)
        code, _, err = self.run_cli("weight", "117", "--metrics", '{"muscle_kg": "alto"}')
        self.assertEqual(code, 1)
        self.assertIn("metrics must be", err)

    def test_checkin_trend_body_food_and_verdict(self):
        self.run_ok("profile", "set", json.dumps({"goal_rate": {"min": 0.5, "max": 1.0, "fast": 1.2},
                                                  "checkin_rules": ["parado 2–3 semanas: +2.000 passos/dia"],
                                                  "water": {"target_ml": 3000}}))
        self.weigh("2026-09-28", 119.0)
        self.weigh("2026-10-05", 118.0, "--bf", "28,6", "--metrics", '{"muscle_kg": 78.0}')
        self.weigh("2026-10-12", 117.2, "--bf", "28,1", "--metrics", '{"muscle_kg": 78.2}')
        self.log({"date": "2026-10-08", "meal": "Almoço", "items": [{"name": "x", "kcal": 2000, "protein": 150, "carbs": 1, "fat": 1}]})
        self.log({"date": "2026-10-09", "meal": "Almoço", "items": [{"name": "x", "kcal": 1800, "protein": 100, "carbs": 1, "fat": 1}]})
        self.log({"date": "2026-10-12", "meal": "Café", "items": [{"name": "x", "kcal": 500, "protein": 30, "carbs": 1, "fat": 1}]})
        self.run_ok("water", "3000", "--date", "2026-10-08")
        self.run_ok("water", "2000", "--date", "2026-10-09")
        out = self.run_ok("checkin", "--date", "2026-10-12")
        self.assertIn("117,2 kg · −0,8 kg desde 05/10 (−0,80 kg/sem)", out)
        self.assertIn("Ritmo em 2 semanas: −0,90 kg/sem (3 pesagens)", out)
        self.assertIn("bf_pct 28,6 → 28,1 · muscle_kg 78,0 → 78,2", out)
        # 05/10–11/10 window: the 12/10 breakfast belongs to the next week
        self.assertIn("2/7 com registro · média 1.900 kcal (meta 2.150) · proteína na meta em 1/2", out)  # Fri target 2,300
        self.assertIn("média 2,5 L · meta batida em 1/2 dias", out)
        self.assertIn("Ritmo dentro da faixa (0,5–1,0 kg/sem)", out)
        self.assertIn("Regras de ajuste: parado 2–3 semanas", out)

    def test_checkin_counts_slow_weeks_and_fast_loss(self):
        self.run_ok("profile", "set", json.dumps({"goal_rate": {"min": 0.5, "max": 1.0, "fast": 1.2}}))
        for day, kg in (("2026-09-21", 120.0), ("2026-09-28", 119.0), ("2026-10-05", 118.8), ("2026-10-12", 118.9)):
            self.weigh(day, kg)
        self.assertIn("Peso subindo: ritmo abaixo da faixa (0,5–1,0 kg/sem) há 2 semana(s)",
                      self.run_ok("checkin", "--date", "2026-10-12"))
        self.weigh("2026-10-19", 117.4)
        self.assertIn("acima de 1,2 kg/sem", self.run_ok("checkin", "--date", "2026-10-19"))

    def test_english_locale(self):
        self.run_ok("profile", "set", json.dumps({"locale": "en"}))
        out = self.log({"date": THURSDAY, "items": [{"taco": 3, "g": 1000}]})
        self.assertIn("1,280 kcal", out)
        self.assertIn("Left: 720 kcal", out)


if __name__ == "__main__":
    unittest.main()
