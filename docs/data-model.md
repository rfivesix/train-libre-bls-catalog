# Catalog data model

## Design rules

1. A BLS code identifies one source food record and is never reassigned.
2. Source names, source notes, nutrient values, nutrient origins, references, and component definitions are imported without editorial changes.
3. A missing nutrient value is distinct from numeric zero. If the source has an origin or reference but no value, that fact is retained with a null value.
4. Units and component names belong to the component definition, not to translated food text.
5. Translation, aliases, and app categories are additive curation. They cannot override source fields.
6. The SQLite artifact uses a normalized nutrient table; it does not create 138 nullable columns on Train Libre's product model.

## Normalized source record

`data/source/bls/4.0/foods/<group>/<BLS-code>.json` contains:

| Field | Type | Meaning |
| --- | --- | --- |
| `bls_code` | string | Exact stable BLS identifier, including its leading group letter. |
| `source_group` | string | First character of the BLS code, retained as source classification. It is not the Train Libre category. |
| `name_de` | string | Exact German source name. |
| `name_en` | string | Exact English source name. |
| `note` | string or null | Exact BLS `Hinweis` cell, if present. |
| `nutrients` | object | Component code to `{value, data_origin, reference}`. |

`value` is a JSON number, a source marker string, or null. `data_origin` and `reference` are strings or null. A component fact is emitted if any of its three source cells is populated. A fact whose three source cells are empty is not emitted. Explicit zeroes and source markers such as `TR`, `-`, `<LOD`, `<LOQ`, and `<LOD or <LOQ` remain distinct and intact. In particular, `-` in the **value** means a missing value, while `-` in the **reference** is a literal source reference marker.

`data/source/bls/4.0/nutrient_components.json` holds every non-empty row from the BLS component workbook, not only components present as columns in the main sheet. It retains German and English names, units, groups, formulas, and formula-application text. `present_in_food_data` marks whether the component appears in the 138 source nutrient triplets.

## Curation overlay

`curation/foods/<group>/<BLS-code>.json` is optional and keyed by `bls_code`. It may contain only:

- `display_names.fr`, `.it`, and `.ja`: translated display text and translation provenance.
- `aliases`: localized alternative search terms, each linked to the BLS code.
- `category_id`: an identifier declared by `curation/categories.json`.
- `default_portion` (optional): an editorial household portion, stored separately from aliases and package quantity. It contains `grams`, labels in `de`, `en`, `fr`, `it`, and `ja`, and provenance (`source-backed` or `train-libre-estimate`).

The curation schema rejects source nutrient fields, German/English source names, source notes, and source identifiers other than the matching BLS code. This makes accidental source overwrites a validation error.

`curation/categories.json` defines 20 app-facing category IDs, names in `de`, `en`, `fr`, `it`, and `ja`, plus an optional emoji. This replaces the 34 more product-oriented legacy categories with broader ingredient and dish groups suited to a generic food catalog. BLS source groups are kept separately.

`curation/migrations/legacy-base-foods.json` contains one entry for every product ID in the read-only legacy database snapshot. It records an explicitly reviewed BLS target, or an explicit `no_equivalent` outcome. Exact normalized German/English name matches are suggestions only; they are never accepted automatically. Existing user logs remain local and unchanged. The map is for resolving legacy references during future recipe/share import.

## Generated SQLite tables

### Source database

`dist/bls-4.0-source.sqlite` is a normalized, lossless representation of BLS:

- `foods`: one row per BLS code, with source names, source group, and note.
- `nutrient_components`: BLS component metadata and formulas.
- `data_origins` and `source_references`: deduplicated source labels and citations.
- `food_nutrients`: one row per populated source nutrient fact, with `value_numeric` or exact `value_text` and foreign keys to its origin and reference.
- `catalog_metadata`: source, version, DOI, license, input checksums, and schema version.

Primary keys are `foods.bls_code`, `nutrient_components.component_code`, and `(food_nutrients.bls_code, food_nutrients.component_code)`. Origins and citations receive deterministic IDs assigned by sorting their exact source strings. Foreign keys are enabled. Indexes support lookup by food and by component. `value_numeric` and `value_text` are mutually exclusive; both may be null when the BLS has provenance/reference metadata but no reported value. Repeated provenance text is stored once and resolved by joins.

### Train Libre asset

`dist/train_libre_base_foods.db` starts as a SQLite backup of the legacy base-food database. Its existing `products`, `categories`, and `metadata` definitions, column order, constraints, indexes, and `user_version` are retained; rows are regenerated. It adds:

- `nutrient_components` and `food_nutrients` for all BLS nutrient facts and provenance; numeric amounts and qualitative source markers have separate columns.
- `data_origins` and `source_references` deduplicate repeated origin labels and citations in the million-row facts table.
- `food_source_metadata` for BLS code, source version, source license, and food note.
- `food_aliases` for additional localized search terms.
- `food_default_portions` for optional localized default serving labels, approximate mass in grams, and provenance. It is not BLS nutrient data and is not a packaged-product quantity.
- `legacy_food_mappings` for reviewed old product IDs, BLS targets, and explicit no-equivalent outcomes.
- the legacy `metadata` table for catalog version and reproducible source/build details.

Existing product fields are compatibility projections. For example, the app's calories and macro columns are mapped from their BLS component codes, while their full-precision source values remain in `food_nutrients`. No unsupported value is fabricated to satisfy the compatibility table.
