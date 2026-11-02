#!/usr/bin/env python3
"""Shopping list for an AI agent. The agent understands the request; this script keeps the list.

Data lives in one folder per user ($SHOPPING_HOME, default ~/.hermes/shopping):
  list.json       the current list: items with name, quantity, category, note, added date
  history.jsonl   append-only: every item bought or dropped, used for suggestions

Commands (JSON arguments are single-quoted strings):
  add '<json item or list of items>'    {"name": "leite zero lactose", "qty": "2 L", "category": "laticínios"}
  remove <name...> [--dropped]          bought by default; --dropped = removed without buying
  bought-all                            the whole list was bought (end of a shopping trip)
  receipt '<json list>' [--date D] [--store S]
                                        items from a receipt: recorded as bought on that date, and taken
                                        off the list when they match one item there ({"name": "leite zero lactose"})
  known                                 every item name in the history, to reuse the same names
  list
  suggest [--limit N]                   usual items that are not on the list and are due again
"""

import argparse
import json
import os
import sys
import unicodedata
from datetime import date
from pathlib import Path

LOCALE = os.environ.get("SHOPPING_LOCALE", "pt-BR")
TEXT = {
    "pt-BR": {
        "added": "🛒 Adicionado: {items}", "updated": "🛒 Atualizado: {items}",
        "removed": "✅ Comprado: {items}", "dropped": "🗑️ Tirado da lista: {items}",
        "header": "🛒 Lista ({n} itens)", "empty": "🛒 A lista está vazia.",
        "other": "outros", "no_match": "Nada na lista parecido com {q!r}.",
        "ambiguous": "{q!r} bate com mais de um item: {items}. Diga qual.",
        "suggest_header": "💡 Costuma comprar e não está na lista:",
        "suggest_line": "- {name} · comprado {n}x · a cada ~{every} dias · último há {ago} dias",
        "suggest_none": "💡 Ainda não há histórico suficiente para sugerir (cada item precisa ter sido comprado 2 vezes).",
        "receipt": "🧾 Cupom registrado: {n} itens{store} em {day}.",
        "receipt_off": "✅ Saiu da lista: {items}",
        "receipt_new": "🆕 Primeira compra registrada: {items}",
        "known_header": "Itens já comprados ({n}):", "known_none": "Nenhuma compra registrada ainda.",
    },
    "en": {
        "added": "🛒 Added: {items}", "updated": "🛒 Updated: {items}",
        "removed": "✅ Bought: {items}", "dropped": "🗑️ Removed from the list: {items}",
        "header": "🛒 List ({n} items)", "empty": "🛒 The list is empty.",
        "other": "other", "no_match": "Nothing on the list like {q!r}.",
        "ambiguous": "{q!r} matches more than one item: {items}. Say which.",
        "suggest_header": "💡 Usually bought and not on the list:",
        "suggest_line": "- {name} · bought {n}x · every ~{every} days · last {ago} days ago",
        "suggest_none": "💡 Not enough history to suggest yet (an item needs 2 purchases).",
        "receipt": "🧾 Receipt recorded: {n} items{store} on {day}.",
        "receipt_off": "✅ Off the list: {items}",
        "receipt_new": "🆕 First recorded purchase: {items}",
        "known_header": "Items bought before ({n}):", "known_none": "No purchases recorded yet.",
    },
}


class UserError(Exception):
    pass


def t(key: str) -> str:
    return TEXT.get(LOCALE, TEXT["en"])[key]


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join("".join(c if c.isalnum() else " " for c in s).split())


class Store:
    def __init__(self, home: Path):
        self.home = home
        self.list_path = home / "list.json"
        self.history_path = home / "history.jsonl"

    def items(self) -> list[dict]:
        if not self.list_path.exists():
            return []
        return json.loads(self.list_path.read_text(encoding="utf-8")).get("items", [])

    def save(self, items: list[dict]) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        tmp = self.list_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.list_path)

    def history(self) -> list[dict]:
        if not self.history_path.exists():
            return []
        out = []
        for line in self.history_path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return out

    def record(self, events: list[dict]) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        with self.history_path.open("a", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")


def label(item: dict) -> str:
    return item["name"] + (f" ({item['qty']})" if item.get("qty") else "")


TODAY: date | None = None  # set by --today (tests and back-dating); None = the real date


def today() -> date:
    return TODAY or date.today()


def cmd_add(store: Store, raw: str) -> str:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UserError(f"Invalid JSON: {exc}") from None
    new = data if isinstance(data, list) else [data]
    items, added, updated = store.items(), [], []
    for n in new:
        if not isinstance(n, dict) or not str(n.get("name", "")).strip():
            raise UserError(f"Each item needs a name: {n}")
        n = {k: str(v).strip() for k, v in n.items() if k in ("name", "qty", "category", "note") and str(v).strip()}
        same = next((i for i in items if normalize(i["name"]) == normalize(n["name"])), None)
        if same:
            same.update({k: v for k, v in n.items() if k != "name"})
            updated.append(label(same))
        else:
            items.append({**n, "added": today().isoformat()})
            added.append(label(items[-1]))
    store.save(items)
    out = []
    if added:
        out.append(t("added").format(items=" · ".join(added)))
    if updated:
        out.append(t("updated").format(items=" · ".join(updated)))
    return "\n".join(out)


def find(items: list[dict], query: str) -> dict:
    q = normalize(query)
    exact = [i for i in items if normalize(i["name"]) == q]
    if exact:
        return exact[0]
    words = set(q.split())  # whole words: "sal" must not match "salsinha"
    hits = [i for i in items if words <= set(normalize(i["name"]).split()) or set(normalize(i["name"]).split()) <= words]
    if not hits:
        raise UserError(t("no_match").format(q=query))
    if len(hits) > 1:
        raise UserError(t("ambiguous").format(q=query, items=", ".join(i["name"] for i in hits)))
    return hits[0]


def cmd_remove(store: Store, names: list[str], dropped: bool) -> str:
    items = store.items()
    gone = [find(items, q) for q in names]  # resolve all first: an error changes nothing
    for g in gone:
        if g in items:
            items.remove(g)
    store.save(items)
    store.record([{"date": today().isoformat(), "event": "dropped" if dropped else "bought",
                   "name": g["name"], "key": normalize(g["name"])} for g in gone])
    return t("dropped" if dropped else "removed").format(items=" · ".join(label(g) for g in gone))


def cmd_bought_all(store: Store) -> str:
    items = store.items()
    if not items:
        return t("empty")
    store.save([])
    store.record([{"date": today().isoformat(), "event": "bought", "name": i["name"], "key": normalize(i["name"])}
                  for i in items])
    return t("removed").format(items=" · ".join(label(i) for i in items))


def cmd_receipt(store: Store, raw: str, day: date | None, shop: str | None) -> str:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UserError(f"Invalid JSON: {exc}") from None
    names, seen = [], set()
    for n in data if isinstance(data, list) else [data]:
        name = str(n.get("name", "")).strip() if isinstance(n, dict) else str(n).strip()
        if not name:
            raise UserError(f"Each item needs a name: {n}")
        if normalize(name) not in seen:
            seen.add(normalize(name))
            names.append(name)
    if not names:
        raise UserError("The receipt has no items.")
    day = day or today()
    known = {e.get("key") for e in store.history() if e.get("event") == "bought"}
    items, off = store.items(), []
    for name in names:
        try:
            hit = find(items, name)
        except UserError:  # not on the list, or matches several items: only recorded
            continue
        items.remove(hit)
        off.append(label(hit))
    store.save(items)
    event = {"date": day.isoformat(), "event": "bought", "source": "receipt"}
    if shop:
        event["store"] = shop.strip()
    store.record([{**event, "name": n, "key": normalize(n)} for n in names])
    out = [t("receipt").format(n=len(names), store=f" ({shop.strip()})" if shop else "", day=day.strftime("%d/%m"))]
    if off:
        out.append(t("receipt_off").format(items=" · ".join(off)))
    new = [n for n in names if normalize(n) not in known]
    if new:
        out.append(t("receipt_new").format(items=" · ".join(new)))
    return "\n".join(out)


def cmd_known(store: Store) -> str:
    names: dict[str, str] = {}
    for e in store.history():
        if e.get("event") == "bought":
            names[e["key"]] = e["name"]
    if not names:
        return t("known_none")
    return "\n".join([t("known_header").format(n=len(names))] + sorted(names.values(), key=normalize))


def cmd_list(store: Store) -> str:
    items = store.items()
    if not items:
        return t("empty")
    groups: dict[str, list[dict]] = {}
    for i in items:
        groups.setdefault(i.get("category") or t("other"), []).append(i)
    out = [t("header").format(n=len(items))]
    for cat in sorted(groups, key=lambda c: (c == t("other"), normalize(c))):
        out.append(f"\n{cat.capitalize()}")
        out += [f"- {label(i)}" + (f" · {i['note']}" if i.get("note") else "") for i in groups[cat]]
    return "\n".join(out)


def cmd_suggest(store: Store, limit: int) -> str:
    on_list = {normalize(i["name"]) for i in store.items()}
    bought: dict[str, list[date]] = {}
    names: dict[str, str] = {}
    for e in store.history():
        if e.get("event") == "bought":
            bought.setdefault(e["key"], []).append(date.fromisoformat(e["date"]))
            names[e["key"]] = e["name"]
    now, rows = today(), []
    for key, days in bought.items():
        days = sorted(set(days))
        if key in on_list or len(days) < 2:
            continue
        every = (days[-1] - days[0]).days / (len(days) - 1)
        ago = (now - days[-1]).days
        if ago >= 0.8 * every:
            rows.append((ago / every if every else 99, names[key], len(days), round(every), ago))
    if not rows:
        return t("suggest_none")
    rows.sort(reverse=True)
    return "\n".join([t("suggest_header")] + [t("suggest_line").format(name=r[1], n=r[2], every=r[3], ago=r[4])
                                               for r in rows[:limit]])


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--home", default=os.environ.get("SHOPPING_HOME") or str(Path.home() / ".hermes" / "shopping"))
    p.add_argument("--today", type=date.fromisoformat, help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("add")
    s.add_argument("json")
    s = sub.add_parser("remove")
    s.add_argument("names", nargs="+")
    s.add_argument("--dropped", action="store_true")
    sub.add_parser("bought-all")
    s = sub.add_parser("receipt")
    s.add_argument("json")
    s.add_argument("--date", type=date.fromisoformat)
    s.add_argument("--store")
    sub.add_parser("known")
    sub.add_parser("list")
    s = sub.add_parser("suggest")
    s.add_argument("--limit", type=int, default=8)
    args = p.parse_args(argv)
    global TODAY
    TODAY = args.today
    store = Store(Path(args.home).expanduser())
    try:
        out = {"add": lambda: cmd_add(store, args.json),
               "remove": lambda: cmd_remove(store, args.names, args.dropped),
               "bought-all": lambda: cmd_bought_all(store), "list": lambda: cmd_list(store),
               "receipt": lambda: cmd_receipt(store, args.json, args.date, args.store),
               "known": lambda: cmd_known(store),
               "suggest": lambda: cmd_suggest(store, args.limit)}[args.cmd]()
    except (UserError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
