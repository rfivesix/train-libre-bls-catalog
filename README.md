# Train Libre BLS Food Catalog

A reproducible, lossless conversion of the German Bundeslebensmittelschlüssel (BLS) 4.0 into a catalog that Train Libre can consume. The BLS source records remain authoritative. Translations, search aliases, and Train Libre categories are maintained separately and can never replace source nutrient data.

## Scope

- Use BLS 4.0 as the curated source for generic foods and prepared dishes.
- Keep Open Food Facts in Train Libre for branded products and barcode workflows.
- Preserve all BLS food records, nutrient values, units, per-value origins, references, component definitions, and food notes.
- Add app-language translations, aliases, categories, and optional default household portions as overlays keyed by immutable BLS code.
- Generate one local SQLite asset for Train Libre; the pipeline does not require a network service or runtime API key.

## Repository map

| Path | Contract |
| --- | --- |
| `sources/bls/4.0/` | Unmodified official BLS source files and documentation. |
| `sources/train-libre/current/` | Unmodified base-food database snapshot used for comparison. |
| `data/source/bls/4.0/` | Deterministically normalized, reviewable BLS records generated from the source workbook. |
| `curation/foods/` | Per-food additions: translated display names, aliases, app category IDs, and optional default portions. |
| `curation/categories.json` | Train Libre's user-facing category vocabulary and localized labels. |
| `curation/migrations/legacy-base-foods.json` | Reviewable mapping from every legacy base-food ID to a BLS ID or an explicit no-equivalent result. |
| `schemas/` | Machine-readable contracts for source records and curation overlays. |
| `tools/catalog.py` | Import, losslessness validation, source database build, and app asset build. |
| `tools/package_catalog_release.py` | Validate and gzip-package the strict app database with a release manifest and attribution notice. |
| `tools/publish_catalog_release.py` | Rebuild, package, and publish the stable release to the public Train Libre GitHub repository using the local `gh` login. |
| `docs/release-distribution.md` | Release artifact contract, client requirements, and packaging procedure. |
| `reports/` | Human-readable validation and build reports. |
| `dist/` | Local generated SQLite artifacts; outputs are reproducible and ignored by Git. |

## Setup

Requires Python 3.10 or newer.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

## Import and verify the source

The importer refuses to overwrite normalized records unless `--replace` is explicit. It never edits files under `sources/` or `curation/`.

```sh
python tools/catalog.py import-source --version 4.0
python tools/catalog.py validate-source --version 4.0
python tools/catalog.py build-source-db --version 4.0
```

The normalized source records are stored in `data/source/bls/4.0/foods/`, one JSON file per BLS code, sharded by the BLS leading-letter group. Each nutrient is represented by its BLS component code and retains its value, origin, and reference. The source note is retained separately on the food record.

The initial import report is `reports/source-validation-4.0.json`. Validation compares every normalized field with the workbook, including missing-value provenance, explicit zeroes, and notes.

## Build the Train Libre asset

The migration draft includes every row from the legacy base-food database. It offers exact-name suggestions for review without approving any target automatically. To recreate the draft before curation begins:

```sh
python tools/catalog.py prepare-legacy-mapping
```

Once food translations, category assignments, and all legacy mappings are reviewed:

```sh
python tools/catalog.py build-app --version 4.0
```

This writes `dist/train_libre_base_foods.db`. It is blocked if required translations or category assignments are missing. `--preview` builds an explicitly incomplete local artifact for inspection; it must not be used for a release.

Package a strict build for versioned remote distribution with:

```sh
python tools/package_catalog_release.py --version 4.0.0 --catalog-version 4.0 --channel stable
```

This creates a compressed database, manifest, and attribution notice under `dist/release/`. It does not publish the files. See [release distribution](docs/release-distribution.md) for the client contract and release policy.

To build and publish the stable release in one command, make this catalog repository public, authenticate the GitHub CLI with permission to publish releases here, then run:

```sh
gh auth login
python tools/publish_catalog_release.py --version 4.0.0 --catalog-version 4.0
```

Use `--dry-run` to build and validate the package while checking the target release without uploading. The command publishes the database, manifest, and attribution notice as a release of this catalog repository. It does not change repository visibility, commit, or push source files. The app client must support gzip releases before it can consume these assets.

The app asset is cloned from the current base-food database before catalog rows are replaced, preserving the exact legacy table definitions, columns, constraints, and indexes. New nutrient, provenance, alias, and legacy mapping tables are additive. The build report is written to `reports/app-build-4.0.json`. See [the integration contract](docs/train-libre-integration.md).

## Data and license

BLS data are provided under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The required attribution and modification statement are in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md). The source workbook and its documentation are retained unmodified. This notice covers the data; a separate license for repository tooling has not been selected.
