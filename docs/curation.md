# Food curation

## Translation policy

The BLS supplies canonical German and English names. Curation adds French, Italian, and Japanese display names without changing either source name. Preserve regional names where a literal translation would lose the food identity; use aliases for common alternatives and transliterations.

Every translated value records its method (`machine`, `human`, or `machine-reviewed`) and, where applicable, model and review details. Translation work is keyed to BLS code, can be generated in deterministic batches, and must be validated for missing/blank values and duplicate aliases. A translated name is not a new food record.

Food names and nutrients are separate concerns: translation cannot change the source food identity, BLS code, nutrient values, units, provenance, or references.

## Categories

The source-group letter in a BLS code is source metadata. It must not silently become the app's user-facing category. Train Libre categories are defined later in `curation/categories.json` and assigned through `category_id` in the food overlay.

Category IDs are stable machine identifiers; localized labels may change. Keep categories broad enough to browse and distinct enough to avoid overlapping labels. Do not infer that a prepared BLS dish is a branded packaged product.

## Aliases

Aliases are optional localized search terms, not alternate source names. Each alias has a language code and type/provenance. They support spelling variants, common regional names, and user search language. Aliases do not carry nutrition values and do not merge separate foods.

## Review order

1. Confirm identity from German/English source names and the BLS code.
2. Translate display names and add only useful search aliases.
3. Assign a Train Libre category after the taxonomy is agreed.
4. Validate translation completeness and category references.
5. Inspect ambiguous foods and duplicates; retain distinct raw/cooked/prepared BLS records.
