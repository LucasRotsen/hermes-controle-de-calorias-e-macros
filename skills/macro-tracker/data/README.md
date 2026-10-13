# Data

`taco.csv`: per 100 g of edible portion — energy (kcal), protein, carbohydrates, fat (lipids) and dietary
fiber, in grams, for the 597 foods of the Brazilian Food Composition Table.

**Source:** NEPA – UNICAMP. *Tabela Brasileira de Composição de Alimentos – TACO*, 4ª edição revisada e
ampliada. Campinas: NEPA-UNICAMP, 2011. Its terms: "É permitida a reprodução total ou parcial do material,
desde que seja citada a fonte."

Normalized CSV from [raulfdm/taco-api](https://github.com/raulfdm/taco-api) (MIT), which converted the
original spreadsheet: `NA` and `*` (samples under re-evaluation) are empty, `Tr` (trace) is 0. Empty cells
mean "no value"; the script refuses to log those rows and the agent estimates instead. Six foods have no
energy value, among them `Leite, de vaca, integral` and `Leite, de vaca, desnatado, UHT`.
