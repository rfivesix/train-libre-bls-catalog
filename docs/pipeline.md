# Import and build pipeline

## Inputs

The versioned upstream inputs are the original BLS 4.0 data workbook, component workbook, and technical documentation under `sources/bls/4.0/`. Their SHA-256 digests are recorded in the generated source manifest and SQLite metadata. The current Train Libre base-food asset is kept as a read-only comparison snapshot under `sources/train-libre/current/`.

## Stages

1. **Import source.** Read the BLS worksheets, validate the expected columns and component definitions, and write one normalized JSON file per BLS food. Source spreadsheets are read-only inputs.
2. **Validate source.** Re-read the workbook and compare each record and component definition against normalized JSON. Count foods, nutrient facts, explicit zeroes, missing-value facts with provenance, and notes. Fail on missing, extra, duplicate, or changed data.
3. **Build normalized SQLite.** Load only normalized source records into `bls-4.0-source.sqlite`. This stage does not read curation and cannot modify the source records.
4. **Curate.** Add per-food translation/alias/category overlays, category definitions, and reviewed legacy-to-BLS mappings. Exact-name legacy candidates are suggestions; each old ID must be explicitly mapped or marked `no_equivalent` with a review note.
5. **Build app asset.** Clone the legacy SQLite database so its compatibility schema and indexes are retained, replace catalog rows, and add normalized nutrient, provenance, alias, and migration tables. Strict mode refuses missing translations, category assignments, or unresolved legacy mappings. Preview mode is local-only and marks incomplete rows.
6. **Review reports.** `reports/source-validation-4.0.json` records field-level source validation and input checksums. Each app build writes `reports/app-build-<version>.json` with output checksum, category coverage, counts, and legacy mapping status.

## Non-destructive update policy

- Never edit upstream workbooks in place.
- A source import refuses to replace existing normalized records unless `--replace` is explicitly supplied. Re-imports regenerate only `data/source/bls/<version>/`; they never touch curation overlays.
- Keep each BLS release in a new version directory. Do not replace 4.0 with a later release.
- Curation is keyed to stable BLS codes. A removed code remains in its old source version; migration to a successor requires a separately documented mapping.
- Build artifacts are disposable outputs. Rebuilding never rewrites source or curation files.
- A generated report must make the exact input version and SHA-256 checksums visible.

## Integrity checks

The importer and validator check:

- exactly 7,140 unique BLS food codes in version 4.0;
- 138 nutrient triplets in the main sheet and matching component definitions;
- preservation of every populated value/origin/reference triplet, including null values with non-null provenance and qualitative value markers (`TR`, `-`, `<LOD`, and `<LOQ` variants);
- preservation of the final `Hinweis` column;
- all non-empty component reference rows, including definitions not present as food-data columns;
- normalized JSON shape and value checks, curation field allowlists, and category/alias/mapping references. The JSON Schema files document the machine-readable contracts.
- output food, component, and fact counts against the normalized source.

The expected source counts are guards, not substitutes for field-by-field comparison. Any mismatch fails the build.
