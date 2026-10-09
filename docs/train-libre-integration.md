# Train Libre integration contract

## Compatibility boundary

The generated asset is cloned from the current base catalog database. It retains the exact `products`, `categories`, and `metadata` table definitions, column order, constraints, indexes, and SQLite `user_version`; only catalog rows are regenerated. Existing readers therefore continue to see the same product columns. Additional normalized tables extend nutrient and migration support without requiring those readers to query the new fields.

The BLS data model is not flattened into `FoodItem`. The generated asset additionally stores the complete BLS nutrient set in `food_nutrients`, keyed by the product's stable synthetic catalog key and nutrient component code. Numeric amounts use `value_numeric`; source markers such as trace (`TR`), missing (`-`), or below detection/quantification limits use exact `value_text`. Deduplicated `data_origins` and `source_references` tables retain the exact per-value provenance without repeating long text in every fact row. An additive app migration can import these rows into companion tables without changing diary entries, recipes, or existing macro calculations.

## Stable keys

- `bls_code` is the external source identifier and never changes for an existing BLS record.
- The legacy app `products.barcode` column receives a namespaced text key `bls:<BLS-code>`. It is not an EAN/UPC and must not be treated as one by barcode lookup.
- Existing diary snapshots remain authoritative for historical logged nutrition; catalog updates must not recalculate old entries.

## Core field mapping

The compatibility product columns are mapped by BLS component code, not by translated label:

| Train Libre field | BLS component | Unit handling |
| --- | --- | --- |
| `calories` | `ENERCC` | kcal per 100 g; compatibility projection may round, full value remains in `food_nutrients`. |
| `kj_100g` | `ENERCJ` | kJ per 100 g. |
| `protein` | `PROT625` | g per 100 g. |
| `carbs` | `CHO` | available carbohydrate, g per 100 g. |
| `fat` | `FAT` | g per 100 g. |
| `fiber` | `FIBT` | total dietary fibre, g per 100 g. |
| `sugar` | `SUGAR` | mono- and disaccharides, g per 100 g. |
| `salt` | `NACL` | sodium chloride, g per 100 g. |
| `sodium_100g` | `NA` | mg per 100 g; do not confuse with salt. |
| `calcium_100g` | `CA` | mg per 100 g. |
| `caffeine_mg_per_100g` | No caffeine component is present in the BLS 4.0 component workbook. | Leave null; do not infer or synthesize caffeine values. |

All BLS values are based on 100 g edible portion. No conversion to 100 ml or serving size is inferred during import. Missing core values remain null in the generated asset; they are not replaced with zero.

## Optional default portions

`food_default_portions` is a catalog-only companion table keyed by the product barcode (`bls:<code>`). It exposes `mass_grams`, five localized labels, and provenance fields. It represents a Train Libre default household measure, not BLS source data and not Open Food Facts package quantity. Consumers must continue to read `product_quantity` and `product_quantity_unit` only as packaged-product quantity; they must query this companion table when they need an optional default serving. If no row exists, show no default. Do not use the portion mass to rewrite per-100-g nutrient values; any serving calculation is a client-side scaling operation and must retain the source basis.

## Search and AI matching

Generic, unbranded food queries should search and rank the curated BLS base-food catalog as the canonical ingredient source. Open Food Facts remains available for explicit brands, packaged-product names, and barcode scans. Search ranking must avoid selecting a brand result solely because it shares a token with a generic food (for example, blueberry gum for a generic blueberry request).

`food_aliases` is a Train Libre curation table, separate from BLS source records and nutrient facts. Catalog schema 2 adds normalized alias keys, `match_scope` (`identity` or `candidate_only`), and `review_status` (`approved` or `candidate_only`). The app copies these rows into a replaceable local index when importing the catalog. Candidate-only or unreviewed aliases retrieve BLS possibilities but never count as exact identity evidence. Cross-food collisions stay available for ranking or clarification.

## Release requirements

- Import the complete `food_nutrients` table as part of the app's catalog migration.
- Keep existing consumers of `products` compatible.
- Resolve old food references through `legacy_food_mappings` when importing shared recipes or other external references; never rewrite a user's existing diary rows as part of the catalog update.
- Ensure catalog refreshes preserve product IDs and existing diary/recipe references.
- Bundle BLS attribution and license text with the public app's notices.
- The app repository must contain or reference a reproducible converter/build recipe; a private catalog repository alone cannot be a required build-time dependency for the public app.
