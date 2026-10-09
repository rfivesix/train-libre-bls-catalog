# Food curation

## Translation policy

The BLS supplies canonical German and English names. Curation adds French, Italian, and Japanese display names without changing either source name. Preserve regional names where a literal translation would lose the food identity; use aliases for common alternatives and transliterations.

Every translated value records its method (`machine`, `human`, or `machine-reviewed`) and, where applicable, model and review details. Translation work is keyed to BLS code, can be generated in deterministic batches, and must be validated for missing/blank values and duplicate aliases. A translated name is not a new food record.

Food names and nutrients are separate concerns: translation cannot change the source food identity, BLS code, nutrient values, units, provenance, or references.

## Categories

The source-group letter in a BLS code is source metadata. It must not silently become the app's user-facing category. Train Libre categories are defined later in `curation/categories.json` and assigned through `category_id` in the food overlay.

Category IDs are stable machine identifiers; localized labels may change. Keep categories broad enough to browse and distinct enough to avoid overlapping labels. Do not infer that a prepared BLS dish is a branded packaged product.

## Aliases

Aliases are optional localized search terms, not alternate source names. They are Train Libre additions; BLS records and nutrient values remain untouched. Each alias records language, kind, curation method, review status, and matching scope. `identity` is reserved for a reviewed equivalent that preserves meaningful variety, composition, and preparation qualifiers. `candidate_only` retrieves possible foods without asserting identity. Machine suggestions must remain `candidate_only`; only source-backed, human-reviewed, or machine-reviewed aliases can be `approved`. Candidate-only aliases are exported to support retrieval but never confer exact-match status. Normalized duplicates for one food are rejected. Cross-food collisions are retained and reported for ranking or clarification.

## Default portions

`default_portion` is optional and separate from aliases and the packaged-product `product_quantity` / `product_quantity_unit` fields. It stores a positive approximate `grams` mass, short labels in `de`, `en`, `fr`, `it`, and `ja`, plus provenance. These values are Train Libre curation; BLS nutrients remain per 100 g and are never rescaled or described as serving-based.

Use a portion only where a common household measure is defensible for the food (for example, a medium whole fruit or a glass of milk). Leave it absent when portions vary materially by preparation, size, recipe, or user practice, or when no recognizable household measure applies. Absence means no default is offered, not zero grams. Do not infer portion mass from nutrients.

Provenance `kind` is `source-backed` only when a cited source explicitly supports that measure; `method` must then be `reference`, and `note` records the reference. `train-libre-estimate` uses `editorial-estimate` and must say the mass is an estimate, not a BLS value. Current curated entries are estimates; no BLS serving-size source has been used.

The current fruit and vegetable review covers all 1,004 entries assigned to those taxonomy categories (264 fruit and 740 vegetables). It records an individual decision and rationale for every candidate in `reports/default-portions-4.0.json`. Sixteen fruit or vegetable entries have a default; the remaining 988 are unset with a reviewed reason. Eggs are in a separate category and are outside this review. References support typical measures or edible-yield assumptions where available; all selected masses remain approximate Train Libre estimates.

## Review order

1. Confirm identity from German/English source names and the BLS code.
2. Translate display names and add only useful search aliases.
3. Assign a Train Libre category after the taxonomy is agreed.
4. Validate translation completeness and category references.
5. Inspect ambiguous foods and duplicates; retain distinct raw/cooked/prepared BLS records.
