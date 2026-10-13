# hermes-controle-de-calorias-e-macros

A calorie and macro tracker skill for [Hermes Agent](https://github.com/NousResearch/hermes-agent) (and any
agent that reads `SKILL.md` skills). Log meals and water by chat (text, voice or photo), ask how much is left
today, record weigh-ins. Brazilian foods come from the **TACO** table (NEPA-UNICAMP, 597 foods).

```
você: almocei 150 g de arroz, uma concha de feijão e um filé de frango grelhado
agent: ✅ Almoço · 457 kcal · P 42 C 61 G 4
         · Arroz, tipo 1, cozido 150 g: 192 kcal
         · Feijão, carioca, cozido 140 g: 106 kcal
         · Frango, peito, sem pele, grelhado 100 g: 159 kcal
       Faltam 1.713 kcal · P 132 C 149 G 66
```

## Design

- **The model interprets, the script computes.** The agent splits the meal into items and picks foods; a
  stdlib Python script does every sum, so totals never drift. LLM calorie estimates from text land within
  ±10% only about 35–60% of the time ([NutriBench](https://arxiv.org/abs/2407.12843)), so table values come
  first and estimates are marked `~`.
- **Lookup order:** your shortcuts (fixed meals) → your custom foods (from labels) → TACO → estimate.
- **Low friction:** clear amounts are logged at once; vague portions and photos get a preview to confirm.
- **Per-user and private:** profile, targets and log live in `$MACROS_HOME` (default `~/.hermes/macros`),
  never in this repo. Targets can differ by weekday, with a calorie reserve for a planned free meal.
- **Optional Markdown journal:** one note per day in a folder you choose (an Obsidian vault, say).

## Install (Hermes)

```bash
hermes skills install LucasRotsen/hermes-controle-de-calorias-e-macros/skills/macro-tracker --category health
```

Then start a new chat (`/new` on Telegram, since gateway sessions load skills only when they start) and
say "quero começar a controlar minhas calorias" or "set up my macro tracking". The agent asks for your data
and targets, or suggests targets with Mifflin-St Jeor / Katch-McArdle.

Optional: `MACROS_HOME=/some/dir` changes where data lives. In a Docker sandbox the default resolves to the
sandbox home, which Hermes persists.

## Command line

```bash
S=skills/macro-tracker/scripts/macros.py
python3 $S profile set '{"locale":"en","timezone":"UTC","targets":{"default":{"kcal":2000,"protein":150,"carbs":200,"fat":65}}}'
python3 $S search frango grelhado
python3 $S log '{"meal":"Lunch","items":[{"taco":410,"g":120},{"taco":3,"g":150}]}'
python3 $S today
python3 $S water 500
python3 $S weight 80.4
python3 $S week
```

Run `python3 $S --help` for every command, and see [SKILL.md](skills/macro-tracker/SKILL.md) for the agent
workflow and the `log` JSON format.

## Português

Skill de controle de calorias, macros e água para o Hermes. Você registra refeições e água pelo chat (texto,
áudio ou foto), pergunta quanto falta no dia e registra o peso. Os alimentos vêm da tabela TACO, e quem faz as contas
é um script, não o modelo.

1. Instale: `hermes skills install LucasRotsen/hermes-controle-de-calorias-e-macros/skills/macro-tracker --category health`
2. No Telegram, mande `/new`.
3. Diga "quero controlar minhas calorias". O agente pergunta peso, altura, objetivo e metas, e salva tudo
   em `~/.hermes/macros`.

Metas podem variar por dia da semana, com reserva de calorias para uma refeição livre. A meta de água e os
seus recipientes ("garrafa" = 650 ml) ficam no perfil, então "tomei uma garrafa" já vira 650 ml. Atalhos guardam
refeições que se repetem com os números exatos.

## Data and disclaimer

Food data: NEPA-UNICAMP, *Tabela Brasileira de Composição de Alimentos – TACO*, 4ª ed. revisada e ampliada,
2011, reproduced with citation as its terms allow, via [raulfdm/taco-api](https://github.com/raulfdm/taco-api).
See [data/README.md](skills/macro-tracker/data/README.md).

Estimates are approximate and not medical or nutritional advice. Code under the [MIT license](LICENSE).
