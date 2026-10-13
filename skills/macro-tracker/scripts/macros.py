#!/usr/bin/env python3
"""Calorie and macro tracker for an AI agent. The agent interprets meals; this script does every number.

Data lives in one folder per user ($MACROS_HOME, default ~/.hermes/macros):
  profile.json   body data, targets, locale, timezone, optional journal_dir
  foods.json     custom foods (per 100 g, optional unit size) and shortcuts (fixed meals)
  log.jsonl      append-only: meals, weigh-ins, and voids (undo)

Commands (JSON arguments are single-quoted strings):
  profile show | profile set '<json patch>'
  calc --weight KG --height CM --age N --sex M|F [--bf PCT] [--activity 1.2-1.9] [--deficit 0.2]
  search <words...> [--limit N]
  food add '<json>' | food list
  shortcut add '<json>' | shortcut list | shortcut remove <key>
  log '<json>' [--dry-run]
  undo [entry_id]
  today [--date YYYY-MM-DD] [--short]
  week [--end YYYY-MM-DD] [--short]
  weight <kg> [--bf PCT] [--date YYYY-MM-DD]
  journal [--date YYYY-MM-DD]
"""

import argparse
import csv
import json
import os
import secrets
import sys
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MACROS = ("protein", "carbs", "fat")
STOPWORDS = {"de", "da", "do", "das", "dos", "com", "e", "a", "o", "em", "of", "with", "and", "the"}
# Everyday names that TACO spells differently. Single words map on exact match only.
SYNONYMS = {
    "arroz branco": "arroz tipo 1", "peito de frango": "frango peito", "file de frango": "frango peito",
    "carne moida": "bovina moido", "patinho moido": "bovina patinho", "feijao": "feijao carioca",
    "banana": "banana prata", "ovo": "ovo galinha", "batata": "batata inglesa", "macarrao": "macarrao trigo",
    "pao integral": "pao trigo forma integral", "pao de forma": "pao trigo forma", "leite": "leite vaca",
}

TEXT = {
    "pt-BR": {
        "wd": ("seg", "ter", "qua", "qui", "sex", "sáb", "dom"), "dec": ",", "thou": ".", "fat": "G",
        "logged": "✅ {meal} · {kcal} kcal · P {p} C {c} G {f}",
        "left": "Faltam {kcal} kcal · P {p} C {c} G {f}",
        "over": "Passou {kcal} kcal da meta",
        "before_reserve": "Reserva {label}: {reserve} kcal · fora dela faltam {kcal} kcal",
        "reserve_used": "Reserva {label}: usou {kcal} de {reserve} kcal",
        "today": "🍽️ Hoje: {kcal}/{tk} kcal · P {p}/{tp} · C {c}/{tc} · G {f}/{tf}",
        "today_empty": "🍽️ Hoje: nenhuma refeição registrada",
        "estimate": "~ inclui estimativa",
        "voided": "↩️ Removido: {meal} ({kcal} kcal)",
        "nothing": "Nada para desfazer hoje.",
        "weight": "⚖️ {kg} kg · média 7 dias {avg} kg{delta}",
        "delta": " ({sign}{d} kg vs 7 dias antes)",
        "week": "🍽️ 7 dias: média {kcal} kcal/dia ({days}/7 dias registrados) · proteína na meta em {hit}/{days}",
        "week_empty": "🍽️ 7 dias: nenhum registro",
        "j_title": "Nutrição", "j_total": "Total", "j_meals": "Refeições", "j_weight": "Peso",
        "j_avg": "média 7 dias",
    },
    "en": {
        "wd": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"), "dec": ".", "thou": ",", "fat": "F",
        "logged": "✅ {meal} · {kcal} kcal · P {p} C {c} F {f}",
        "left": "Left: {kcal} kcal · P {p} C {c} F {f}",
        "over": "{kcal} kcal over target",
        "before_reserve": "Reserve {label}: {reserve} kcal · outside it {kcal} kcal left",
        "reserve_used": "Reserve {label}: used {kcal} of {reserve} kcal",
        "today": "🍽️ Today: {kcal}/{tk} kcal · P {p}/{tp} · C {c}/{tc} · F {f}/{tf}",
        "today_empty": "🍽️ Today: no meals logged",
        "estimate": "~ includes estimates",
        "voided": "↩️ Removed: {meal} ({kcal} kcal)",
        "nothing": "Nothing to undo today.",
        "weight": "⚖️ {kg} kg · 7-day average {avg} kg{delta}",
        "delta": " ({sign}{d} kg vs previous 7 days)",
        "week": "🍽️ 7 days: average {kcal} kcal/day ({days}/7 days logged) · protein on target {hit}/{days}",
        "week_empty": "🍽️ 7 days: nothing logged",
        "j_title": "Nutrition", "j_total": "Total", "j_meals": "Meals", "j_weight": "Weight",
        "j_avg": "7-day average",
    },
}


class UserError(Exception):
    pass


# ---------- storage ----------

class Store:
    def __init__(self, home: Path):
        self.home = home
        self.profile_path = home / "profile.json"
        self.foods_path = home / "foods.json"
        self.log_path = home / "log.jsonl"

    def read_json(self, path: Path, default):
        return json.loads(path.read_text()) if path.exists() else default

    def write_json(self, path: Path, data) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
        os.replace(tmp, path)

    def profile(self) -> dict:
        prof = self.read_json(self.profile_path, None)
        if prof is None:
            raise UserError(f"No profile yet. Create one with: profile set '<json>' (home: {self.home})")
        return prof

    def foods(self) -> dict:
        return self.read_json(self.foods_path, {"foods": {}, "shortcuts": {}})

    def entries(self) -> list[dict]:
        if not self.log_path.exists():
            return []
        out = []
        for line in self.log_path.read_text().splitlines():
            if line.strip():
                out.append(json.loads(line))
        voided = {e["target"] for e in out if e.get("type") == "void"}
        return [e for e in out if e.get("type") != "void" and e.get("id") not in voided]

    def append(self, entry: dict) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------- helpers ----------

def now_in(profile: dict) -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(profile.get("timezone", "UTC")))
    except Exception:
        return datetime.now()


def text(profile: dict) -> dict:
    return TEXT.get(profile.get("locale", "en"), TEXT["en"])


def num(value: float, t: dict, decimals: int = 0) -> str:
    s = f"{value:,.{decimals}f}"
    return s.replace(",", "\0").replace(".", t["dec"]).replace("\0", t["thou"])


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return "".join(c if c.isalnum() else " " for c in s)


def tokens(s: str) -> list[str]:
    out = []
    for tok in normalize(s).split():
        if tok in STOPWORDS:
            continue
        out.append(tok[:-1] if len(tok) > 3 and tok.endswith("s") else tok)
    return out


def deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for k, v in patch.items():
        if v is None:
            out.pop(k, None)
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def parse_json(raw: str, what: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UserError(f"Invalid JSON for {what}: {exc}") from None


def zero() -> dict:
    return {"kcal": 0.0, "protein": 0.0, "carbs": 0.0, "fat": 0.0}


def add(a: dict, b: dict) -> dict:
    return {k: a[k] + b.get(k, 0) for k in a}


def rounded(n: dict) -> dict:
    return {"kcal": round(n["kcal"]), **{m: round(n[m], 1) for m in MACROS}}


def targets_for(profile: dict, day: date) -> dict:
    t = profile.get("targets") or {}
    base = dict(t.get("default") or {})
    return deep_merge(base, (t.get("weekdays") or {}).get(WEEKDAYS[day.weekday()], {}))


# ---------- food data ----------

def taco() -> dict[int, dict]:
    rows = {}
    with (DATA / "taco.csv").open(newline="") as fh:
        for r in csv.DictReader(fh):
            rows[int(r["id"])] = {
                "name": r["name"], "category": r["category"],
                **{k: (float(r[k]) if r[k] else None) for k in ("kcal", "protein", "carbs", "fat", "fiber")},
            }
    return rows


def search(store: Store, query: str, limit: int) -> list[dict]:
    joined = " ".join(tokens(query))
    for k, v in SYNONYMS.items():
        key = " ".join(tokens(k))
        if joined == key or (" " in key and joined.startswith(key + " ")):
            joined = " ".join(tokens(v)) + joined[len(key):]
            break
    want = joined.split()
    if not want:
        return []
    candidates = [{"ref": f"taco:{i}", **f} for i, f in taco().items()]
    foods = store.foods()
    candidates += [{"ref": f"food:{k}", "category": "custom", **v} for k, v in foods["foods"].items()]
    candidates += [{"ref": f"shortcut:{k}", "category": "shortcut", **v} for k, v in foods["shortcuts"].items()]
    scored = []
    for c in candidates:
        have = tokens(c["name"]) + tokens(c["ref"].split(":", 1)[1])
        hits = sum(any(h.startswith(w) or w.startswith(h) for h in have) for w in want)
        if hits:
            first = have and have[0].startswith(want[0])
            scored.append((hits, first, -len(have), c))
    scored.sort(key=lambda s: s[:3], reverse=True)
    best = scored[0][0] if scored else 0
    return [c for hits, *_, c in scored if hits == best][:limit]


def resolve_item(item: dict, store: Store, foods: dict, table: dict | None) -> dict:
    """One log item -> {name, g, kcal, protein, carbs, fat, source}. Raises UserError on bad input."""
    if "taco" in item:
        tid = int(item["taco"])
        row = (table or {}).get(tid)
        if row is None:
            raise UserError(f"TACO id {tid} not found")
        if row["kcal"] is None:
            raise UserError(f"TACO {tid} ({row['name']}) has no energy value; log it as an estimate")
        g = float(item.get("g") or 0)
        if g <= 0:
            raise UserError(f"TACO item {row['name']} needs grams (\"g\")")
        n = {k: (row[k] or 0) * g / 100 for k in ("kcal", *MACROS)}
        return {"name": item.get("name") or row["name"], "g": g, **n, "source": f"taco:{tid}"}
    if "food" in item:
        key = item["food"]
        f = foods["foods"].get(key)
        if f is None:
            raise UserError(f"Custom food '{key}' not found (food list)")
        g = float(item.get("g") or 0) or float(item.get("units", 0)) * float(f.get("unit_g") or 0)
        if g <= 0:
            raise UserError(f"Custom food '{key}' needs \"g\" or \"units\" (with unit_g set)")
        n = {k: float(f.get(k, 0)) * g / 100 for k in ("kcal", *MACROS)}
        return {"name": item.get("name") or f["name"], "g": g, **n, "source": f"food:{key}"}
    if "shortcut" in item:
        key = item["shortcut"]
        s = foods["shortcuts"].get(key)
        if s is None:
            raise UserError(f"Shortcut '{key}' not found (shortcut list)")
        x = float(item.get("x", 1))
        n = {k: float(s.get(k, 0)) * x for k in ("kcal", *MACROS)}
        name = s["name"] if x == 1 else f"{s['name']} ×{x:g}"
        return {"name": name, "g": None, **n, "source": f"shortcut:{key}"}
    if "kcal" in item:
        if not item.get("name"):
            raise UserError("Estimated items need a \"name\"")
        n = {k: float(item.get(k) or 0) for k in ("kcal", *MACROS)}
        g = float(item["g"]) if item.get("g") else None
        return {"name": item["name"], "g": g, **n, "source": "estimate"}
    raise UserError(f"Item needs one of taco, food, shortcut or kcal: {item}")


def day_meals(store: Store, day: str) -> list[dict]:
    return [e for e in store.entries() if e.get("type") == "meal" and e["date"] == day]


def day_totals(meals: list[dict]) -> dict:
    total = zero()
    for m in meals:
        total = add(total, m["totals"])
    return total


# ---------- commands ----------

def cmd_profile(store: Store, args) -> str:
    if args.action == "show":
        return json.dumps(store.profile(), ensure_ascii=False, indent=1)
    current = store.read_json(store.profile_path, {})
    patch = parse_json(args.json or "", "profile")
    store.write_json(store.profile_path, deep_merge(current, patch))
    return "Profile saved."


def cmd_calc(args) -> str:
    w, h, a, sex = args.weight, args.height, args.age, args.sex.upper()
    mifflin = 10 * w + 6.25 * h - 5 * a + (5 if sex == "M" else -161)
    lines = [f"BMR Mifflin-St Jeor: {mifflin:.0f} kcal"]
    bmr, lean = mifflin, None
    if args.bf is not None:
        lean = w * (1 - args.bf / 100)
        bmr = 370 + 21.6 * lean
        lines.append(f"Lean mass: {lean:.1f} kg · BMR Katch-McArdle: {bmr:.0f} kcal (used)")
    tdee = bmr * args.activity
    kcal = tdee * (1 - args.deficit)
    protein = 2.2 * lean if lean else 1.8 * w
    fat = kcal * 0.25 / 9
    carbs = max(kcal - protein * 4 - fat * 9, 0) / 4
    lines += [
        f"TDEE (×{args.activity}): {tdee:.0f} kcal",
        f"Suggested ({args.deficit:.0%} deficit): {kcal:.0f} kcal · protein {protein:.0f} g · "
        f"carbs {carbs:.0f} g · fat {fat:.0f} g",
        "Protein: 2.2 g/kg lean mass (or 1.8 g/kg body weight); fat: 25% of energy; carbs: the rest.",
        "A starting point, not medical advice. Adjust by the 2–4 week weight trend.",
    ]
    return "\n".join(lines)


def cmd_search(store: Store, args) -> str:
    hits = search(store, " ".join(args.words), args.limit)
    if not hits:
        return "No match. Estimate it, or add a custom food."
    out = []
    for h in hits:
        if h["category"] == "shortcut":
            out.append(f"{h['ref']} | {h['name']} | fixed: {h['kcal']:.0f} kcal P {h['protein']:g} "
                       f"C {h['carbs']:g} F {h['fat']:g}")
            continue
        vals = " ".join(f"{k[0].upper()} {h[k]:g}" if h.get(k) is not None else f"{k[0].upper()} ?" for k in MACROS)
        kcal = f"{h['kcal']:.0f}" if h.get("kcal") is not None else "?"
        unit = f" · 1 unit = {h['unit_g']:g} g" if h.get("unit_g") else ""
        out.append(f"{h['ref']} | {h['name']} | per 100 g: {kcal} kcal {vals}{unit}")
    return "\n".join(out)


def _validate_numbers(obj: dict, keys) -> None:
    for k in keys:
        if not isinstance(obj.get(k), (int, float)) or obj[k] < 0:
            raise UserError(f"'{k}' must be a non-negative number")


def cmd_food(store: Store, args) -> str:
    foods = store.foods()
    if args.action == "list":
        return "\n".join(f"food:{k} | {v['name']} | per 100 g: {v['kcal']:g} kcal" for k, v in foods["foods"].items()) \
            or "No custom foods."
    f = parse_json(args.json or "", "food")
    key = f.pop("key", None)
    if not key or not f.get("name"):
        raise UserError("food add needs \"key\" and \"name\", plus kcal/protein/carbs/fat per 100 g")
    _validate_numbers(f, ("kcal", *MACROS))
    foods["foods"][key] = f
    store.write_json(store.foods_path, foods)
    return f"Saved food:{key}."


def cmd_shortcut(store: Store, args) -> str:
    foods = store.foods()
    if args.action == "list":
        return "\n".join(f"shortcut:{k} | {v['name']} | {v['kcal']:g} kcal P {v['protein']:g} C {v['carbs']:g} "
                         f"F {v['fat']:g}" + (f" | {v['description']}" if v.get("description") else "")
                         for k, v in foods["shortcuts"].items()) or "No shortcuts."
    if args.action == "remove":
        if foods["shortcuts"].pop(args.json or "", None) is None:
            raise UserError(f"Shortcut '{args.json}' not found")
        store.write_json(store.foods_path, foods)
        return f"Removed shortcut:{args.json}."
    s = parse_json(args.json or "", "shortcut")
    key = s.pop("key", None)
    if not key or not s.get("name"):
        raise UserError("shortcut add needs \"key\" and \"name\", plus total kcal/protein/carbs/fat")
    _validate_numbers(s, ("kcal", *MACROS))
    foods["shortcuts"][key] = s
    store.write_json(store.foods_path, foods)
    return f"Saved shortcut:{key}."


def remaining_lines(profile: dict, day: date, meals: list[dict]) -> list[str]:
    t, tgt = text(profile), targets_for(profile, day)
    if not tgt.get("kcal"):
        return []
    total = day_totals(meals)
    left = {k: tgt.get(k, 0) - total[k] for k in total}
    reserve = tgt.get("reserve")
    lines = []
    if reserve:
        free = [m for m in meals if m.get("free")]
        if free:
            used = day_totals(free)["kcal"]
            lines.append(t["reserve_used"].format(label=reserve["label"], kcal=num(used, t),
                                                  reserve=num(reserve["kcal"], t)))
        else:
            structured = tgt["kcal"] - reserve["kcal"] - total["kcal"]
            lines.append(t["before_reserve"].format(label=reserve["label"], kcal=num(max(structured, 0), t),
                                                    reserve=num(reserve["kcal"], t)))
    if left["kcal"] >= 0:
        lines.append(t["left"].format(kcal=num(left["kcal"], t), p=num(max(left["protein"], 0), t),
                                      c=num(max(left["carbs"], 0), t), f=num(max(left["fat"], 0), t)))
    else:
        lines.append(t["over"].format(kcal=num(-left["kcal"], t)))
    return lines


def cmd_log(store: Store, args) -> str:
    profile = store.profile()
    t = text(profile)
    payload = parse_json(args.json, "log")
    items = payload.get("items") or []
    if not items:
        raise UserError("log needs \"items\"")
    foods = store.foods()
    table = taco() if any("taco" in i for i in items) else None
    resolved = [resolve_item(i, store, foods, table) for i in items]
    totals = zero()
    for r in resolved:
        totals = add(totals, r)
    now = now_in(profile)
    day = payload.get("date") or now.date().isoformat()
    date.fromisoformat(day)
    entry = {
        "id": f"m-{now:%Y%m%d%H%M%S}-{secrets.token_hex(2)}", "type": "meal", "date": day,
        "time": payload.get("time") or now.strftime("%H:%M"), "meal": payload.get("meal") or "—",
        "free": bool(payload.get("free")),
        "items": [{**r, **rounded(r)} for r in resolved], "totals": rounded(totals), "ts": now.isoformat(),
    }
    if payload.get("note"):
        entry["note"] = payload["note"]
    tot = entry["totals"]
    head = t["logged"].format(meal=entry["meal"], kcal=num(tot["kcal"], t), p=num(tot["protein"], t),
                              c=num(tot["carbs"], t), f=num(tot["fat"], t))
    detail = [f"  · {r['name']}" + (f" {r['g']:g} g" if r["g"] else "") + f": {r['kcal']:.0f} kcal"
              + (" ~" if r["source"] == "estimate" else "") for r in resolved]
    meals = day_meals(store, day) + [entry]
    tail = remaining_lines(profile, date.fromisoformat(day), meals)
    if any(r["source"] == "estimate" for r in resolved):
        tail.append(t["estimate"])
    if args.dry_run:
        return "\n".join(["(preview, not saved)", head, *detail, *tail])
    store.append(entry)
    write_journal(store, profile, day)
    return "\n".join([head, *detail, *tail, f"id: {entry['id']}"])


def cmd_undo(store: Store, args) -> str:
    profile = store.profile()
    t = text(profile)
    meals = [e for e in store.entries() if e.get("type") == "meal"]
    if args.entry_id:
        target = next((m for m in meals if m["id"] == args.entry_id), None)
        if target is None:
            raise UserError(f"No active meal with id {args.entry_id}")
    else:
        today = now_in(profile).date().isoformat()
        todays = [m for m in meals if m["date"] == today]
        if not todays:
            return t["nothing"]
        target = todays[-1]
    store.append({"type": "void", "target": target["id"], "ts": now_in(profile).isoformat()})
    write_journal(store, profile, target["date"])
    return t["voided"].format(meal=target["meal"], kcal=num(target["totals"]["kcal"], t))


def today_report(store: Store, profile: dict, day: date, short: bool) -> str:
    t, tgt = text(profile), targets_for(profile, day)
    meals = day_meals(store, day.isoformat())
    total = day_totals(meals)
    if not meals and short:
        return t["today_empty"]
    line = t["today"].format(kcal=num(total["kcal"], t), tk=num(tgt.get("kcal", 0), t),
                             p=num(total["protein"], t), tp=num(tgt.get("protein", 0), t),
                             c=num(total["carbs"], t), tc=num(tgt.get("carbs", 0), t),
                             f=num(total["fat"], t), tf=num(tgt.get("fat", 0), t))
    if short:
        return line
    out = [line]
    for m in meals:
        est = " ~" if any(i["source"] == "estimate" for i in m["items"]) else ""
        out.append(f"- {m['time']} {m['meal']}: {num(m['totals']['kcal'], t)} kcal{est} "
                   f"(P {num(m['totals']['protein'], t)}) [id {m['id']}]")
    return "\n".join(out + remaining_lines(profile, day, meals))


def cmd_today(store: Store, args) -> str:
    profile = store.profile()
    day = date.fromisoformat(args.date) if args.date else now_in(profile).date()
    return today_report(store, profile, day, args.short)


def weight_avg(entries: list[dict], end: date, days: int = 7) -> float | None:
    start = end - timedelta(days=days - 1)
    vals = [e["kg"] for e in entries if e.get("type") == "weight" and start.isoformat() <= e["date"] <= end.isoformat()]
    return sum(vals) / len(vals) if vals else None


def weight_line(store: Store, profile: dict, day: date, kg: float) -> str:
    t = text(profile)
    entries = store.entries()
    avg = weight_avg(entries, day)
    prev = weight_avg(entries, day - timedelta(days=7))
    delta = ""
    if prev is not None and avg is not None:
        d = avg - prev
        delta = t["delta"].format(sign="+" if d > 0 else "", d=num(d, t, 1))
    return t["weight"].format(kg=num(kg, t, 1), avg=num(avg if avg is not None else kg, t, 1), delta=delta)


def cmd_weight(store: Store, args) -> str:
    profile = store.profile()
    day = date.fromisoformat(args.date) if args.date else now_in(profile).date()
    if not 20 <= args.kg <= 400:
        raise UserError("Weight must be in kg (20–400)")
    entry = {"type": "weight", "date": day.isoformat(), "kg": args.kg, "ts": now_in(profile).isoformat()}
    if args.bf is not None:
        entry["bf"] = args.bf
    store.append(entry)
    body = {"weight_kg": args.kg, **({"body_fat_pct": args.bf} if args.bf is not None else {})}
    store.write_json(store.profile_path, deep_merge(profile, {"body": body}))
    write_journal(store, store.profile(), day.isoformat())
    return weight_line(store, profile, day, args.kg)


def cmd_week(store: Store, args) -> str:
    profile = store.profile()
    t = text(profile)
    end = date.fromisoformat(args.end) if args.end else now_in(profile).date()
    rows = []
    for i in range(6, -1, -1):
        day = end - timedelta(days=i)
        meals = day_meals(store, day.isoformat())
        if meals:
            total, tgt = day_totals(meals), targets_for(profile, day)
            rows.append((day, total, tgt))
    lines = []
    if rows:
        avg = sum(r[1]["kcal"] for r in rows) / len(rows)
        hit = sum(1 for _, total, tgt in rows if total["protein"] >= 0.9 * tgt.get("protein", 0))
        lines.append(t["week"].format(kcal=num(avg, t), days=len(rows), hit=hit))
    else:
        lines.append(t["week_empty"])
    entries = store.entries()
    avg_w = weight_avg(entries, end)
    if avg_w is not None:
        last = [e for e in entries if e.get("type") == "weight" and e["date"] <= end.isoformat()][-1]
        lines.append(weight_line(store, profile, end, last["kg"]))
    if not args.short:
        for day, total, tgt in rows:
            lines.append(f"- {t['wd'][day.weekday()]} {day:%d/%m}: {num(total['kcal'], t)}/{num(tgt.get('kcal', 0), t)} "
                         f"kcal · P {num(total['protein'], t)}/{num(tgt.get('protein', 0), t)}")
    return "\n".join(lines)


def write_journal(store: Store, profile: dict, day: str) -> Path | None:
    """Rewrite the day's Markdown note in journal_dir (if configured). Idempotent."""
    jdir = profile.get("journal_dir")
    if not jdir:
        return None
    t, d = text(profile), date.fromisoformat(day)
    meals = day_meals(store, day)
    weights = [e for e in store.entries() if e.get("type") == "weight" and e["date"] == day]
    path = Path(jdir) / f"{day}.md"
    if not meals and not weights:
        if path.exists():
            path.unlink()
        return None
    total, tgt = day_totals(meals), targets_for(profile, d)
    lines = [
        "---", "type: nutrition", f"date: {day}",
        f"kcal: {round(total['kcal'])}", f"protein: {round(total['protein'])}",
        f"carbs: {round(total['carbs'])}", f"fat: {round(total['fat'])}",
    ]
    if weights:
        lines.append(f"weight_kg: {weights[-1]['kg']}")
    lines += ["---", "", f"# {t['j_title']} · {t['wd'][d.weekday()]}, {d:%d/%m}", ""]
    lines.append(f"**{t['j_total']}:** {num(total['kcal'], t)} / {num(tgt.get('kcal', 0), t)} kcal · "
                 f"P {num(total['protein'], t)}/{num(tgt.get('protein', 0), t)} g · "
                 f"C {num(total['carbs'], t)}/{num(tgt.get('carbs', 0), t)} g · "
                 f"{t['fat']} {num(total['fat'], t)}/{num(tgt.get('fat', 0), t)} g")
    if meals:
        lines += ["", f"## {t['j_meals']}"]
        for m in meals:
            lines += ["", f"### {m['time']} · {m['meal']} · {num(m['totals']['kcal'], t)} kcal"]
            for i in m["items"]:
                grams = f" ({i['g']:g} g)" if i.get("g") else ""
                est = " ~" if i["source"] == "estimate" else ""
                lines.append(f"- {i['name']}{grams}: {num(i['kcal'], t)} kcal{est} · P {num(i['protein'], t)} "
                             f"C {num(i['carbs'], t)} {t['fat']} "
                             f"{num(i['fat'], t)}")
            if m.get("note"):
                lines.append(f"- _{m['note']}_")
    if weights:
        avg = weight_avg(store.entries(), d)
        lines += ["", f"## {t['j_weight']}", "",
                  f"{num(weights[-1]['kg'], t, 1)} kg · {t['j_avg']} {num(avg, t, 1)} kg"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    return path


def cmd_journal(store: Store, args) -> str:
    profile = store.profile()
    day = args.date or now_in(profile).date().isoformat()
    path = write_journal(store, profile, day)
    return f"Wrote {path}" if path else "No journal_dir configured, or nothing logged that day."


# ---------- CLI ----------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--home", default=os.environ.get("MACROS_HOME") or str(Path.home() / ".hermes" / "macros"))
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("profile")
    s.add_argument("action", choices=("show", "set"))
    s.add_argument("json", nargs="?")

    s = sub.add_parser("calc")
    s.add_argument("--weight", type=float, required=True)
    s.add_argument("--height", type=float, required=True)
    s.add_argument("--age", type=int, required=True)
    s.add_argument("--sex", choices=("M", "F", "m", "f"), required=True)
    s.add_argument("--bf", type=float)
    s.add_argument("--activity", type=float, default=1.375)
    s.add_argument("--deficit", type=float, default=0.2)

    s = sub.add_parser("search")
    s.add_argument("words", nargs="+")
    s.add_argument("--limit", type=int, default=8)

    for name, actions in (("food", ("add", "list")), ("shortcut", ("add", "list", "remove"))):
        s = sub.add_parser(name)
        s.add_argument("action", choices=actions)
        s.add_argument("json", nargs="?")

    s = sub.add_parser("log")
    s.add_argument("json")
    s.add_argument("--dry-run", action="store_true")

    s = sub.add_parser("undo")
    s.add_argument("entry_id", nargs="?")

    s = sub.add_parser("today")
    s.add_argument("--date")
    s.add_argument("--short", action="store_true")

    s = sub.add_parser("week")
    s.add_argument("--end")
    s.add_argument("--short", action="store_true")

    s = sub.add_parser("weight")
    s.add_argument("kg", type=lambda v: float(v.replace(",", ".")))
    s.add_argument("--bf", type=float)
    s.add_argument("--date")

    s = sub.add_parser("journal")
    s.add_argument("--date")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    store = Store(Path(args.home).expanduser())
    handlers = {
        "profile": lambda: cmd_profile(store, args), "calc": lambda: cmd_calc(args),
        "search": lambda: cmd_search(store, args), "food": lambda: cmd_food(store, args),
        "shortcut": lambda: cmd_shortcut(store, args), "log": lambda: cmd_log(store, args),
        "undo": lambda: cmd_undo(store, args), "today": lambda: cmd_today(store, args),
        "week": lambda: cmd_week(store, args), "weight": lambda: cmd_weight(store, args),
        "journal": lambda: cmd_journal(store, args),
    }
    try:
        print(handlers[args.cmd]())
    except (UserError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
