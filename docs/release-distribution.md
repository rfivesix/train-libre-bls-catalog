# Food catalog release distribution

## Delivery model

Do not bundle the full BLS SQLite catalog in the Train Libre app. Publish a gzip-compressed SQLite snapshot as a versioned release asset. The current strict build is about 82 MiB; the compressed payload is about 26 MiB. The app downloads and verifies the compressed bytes, decompresses to a temporary SQLite file, verifies the uncompressed database, and imports it only after all checks pass.

The former bundled base-food catalog is not a fallback. A fresh installation must obtain the BLS catalog before enabling base-food-dependent features. Previously logged diary entries remain local historical snapshots and are not replaced by catalog updates.

## Release contents

Each release contains these files:

- `train_libre_base_foods.db.gz`: deterministic gzip of the strict app database.
- `catalog_manifest.json`: content version, channel, schema compatibility, checksums, byte sizes, record counts, source attribution, license, and modification statement.
- `THIRD_PARTY_NOTICES.md`: required attribution and license notice.

The manifest distinguishes `download_sha256` (compressed bytes) from `db_sha256` (decompressed SQLite bytes). A client must verify both. Do not overwrite an immutable release asset; publish a new version and update the stable channel pointer instead.

## Build a release package

Build and validate the strict SQLite catalog first:

```sh
python3 tools/catalog.py validate-source --version 4.0
python3 tools/catalog.py build-app --version 4.0 --force
```

Then package it with a unique release version:

```sh
python3 tools/package_catalog_release.py \
  --version 4.0.0 \
  --catalog-version 4.0 \
  --channel stable
```

The command refuses preview builds, checksum mismatches, invalid SQLite files, count mismatches, or pending legacy mappings. It writes the package to `dist/release/`. It does not publish, tag, or change repository visibility.

## Publish a release from this repository

The release belongs in this catalog repository, separate from Train Libre app releases. Make `rfivesix/train-libre-bls-catalog` public before publishing; the release assets must be anonymously downloadable by app clients. The BLS source is CC BY 4.0, the legacy database snapshot matches the copy already bundled in the public Train Libre repository, and the attribution notice accompanies the derived catalog. Review any later-added files before changing visibility. The publishing command uses the local GitHub CLI session, so no token needs to be stored in this repository:

```sh
gh auth login
python3 tools/publish_catalog_release.py --version 4.0.0 --catalog-version 4.0
```

The command validates the strict source and app build, packages the database, confirms this repository is public, then creates or updates the `bls-foods-stable` GitHub Release. When updating, it uploads the database and attribution notice before replacing the manifest, so clients using the previous manifest reject a mismatched database and retain their installed catalog. The command refuses draft or immutable releases. `--dry-run` performs the build and checks the target without publishing.

The command publishes publicly. Run it only for a reviewed release version. It does not change this repository's visibility, commit, or push its source files.

## Client requirements

The release package alone is not a client integration. The app updater must support gzip extraction, compare the downloaded and decompressed checksums separately, enforce `min_app_schema_version`, verify the product and nutrient counts, and install atomically. The app must not seed or retain the old bundled base-food catalog. Catalog update failure must preserve the last valid downloaded catalog; a clean install with no catalog must clearly gate base-food features until the BLS download succeeds.

The manifest uses the release-version and schema fields already used by the OpenExerciseDB channel, with food-specific count fields and explicit gzip checksums. The food client must use its own `source_id` and stable channel configuration.
