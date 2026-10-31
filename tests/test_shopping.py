import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "shopping-list" / "scripts"))
import lista  # noqa: E402


class ShoppingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        self.day = "2026-11-01"

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = lista.main(["--home", self.home, "--today", self.day, *argv])
        return code, out.getvalue(), err.getvalue()

    def ok(self, *argv):
        code, out, err = self.run_cli(*argv)
        self.assertEqual(code, 0, err)
        return out

    def add(self, *items):
        return self.ok("add", json.dumps(list(items)))

    def test_add_list_and_update(self):
        out = self.add({"name": "Leite zero lactose", "qty": "2 L", "category": "laticínios"},
                       {"name": "Ovos", "qty": "30", "category": "laticínios"}, {"name": "Detergente"})
        self.assertIn("Adicionado: Leite zero lactose (2 L) · Ovos (30) · Detergente", out)
        self.assertIn("Atualizado: Leite zero lactose (3 L)", self.add({"name": "leite zero lactose", "qty": "3 L"}))
        out = self.ok("list")
        self.assertIn("Lista (3 itens)", out)
        self.assertLess(out.index("Laticínios"), out.index("Outros"))  # "outros" goes last
        self.assertIn("- Leite zero lactose (3 L)", out)

    def test_remove_bought_dropped_and_ambiguous(self):
        self.add({"name": "pão francês"}, {"name": "pão de forma"}, {"name": "banana"})
        code, _, err = self.run_cli("remove", "pao")
        self.assertEqual(code, 1)
        self.assertIn("mais de um item", err)
        self.assertIn("Comprado: banana", self.ok("remove", "Banana"))
        self.assertIn("Tirado da lista: pão de forma", self.ok("remove", "pão de forma", "--dropped"))
        self.assertIn("Lista (1 itens)", self.ok("list"))
        code, _, err = self.run_cli("remove", "abacaxi")
        self.assertIn("Nada na lista", err)

    def test_failed_remove_changes_nothing(self):
        self.add({"name": "arroz"}, {"name": "feijão"})
        code, _, _ = self.run_cli("remove", "arroz", "abacaxi")
        self.assertEqual(code, 1)
        self.assertIn("Lista (2 itens)", self.ok("list"))

    def test_bought_all_and_empty(self):
        self.add({"name": "aveia"}, {"name": "whey"})
        self.assertIn("Comprado: aveia · whey", self.ok("bought-all"))
        self.assertIn("vazia", self.ok("list"))

    def test_suggest_from_history(self):
        for day in ("2026-10-01", "2026-10-08", "2026-10-15"):
            self.day = day
            self.add({"name": "Ovos"}, {"name": "Banana"})
            self.ok("bought-all")
        self.day = "2026-10-20"
        self.add({"name": "Detergente"})
        self.ok("remove", "detergente")  # bought once: not enough history
        self.add({"name": "banana"})  # already on the list: not suggested
        self.day = "2026-10-21"
        out = self.ok("suggest")
        self.assertIn("Ovos · comprado 3x · a cada ~7 dias · último há 6 dias", out)
        self.assertNotIn("Banana", out)
        self.assertNotIn("Detergente", out)

    def test_suggest_without_history(self):
        self.assertIn("histórico suficiente", self.ok("suggest"))

    def test_rejects_bad_input(self):
        code, _, err = self.run_cli("add", '[{"qty": "2"}]')
        self.assertEqual(code, 1)
        self.assertIn("needs a name", err)
        code, _, err = self.run_cli("add", "not json")
        self.assertIn("Invalid JSON", err)


if __name__ == "__main__":
    unittest.main()
