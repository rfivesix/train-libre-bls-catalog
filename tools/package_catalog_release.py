#!/usr/bin/env python3
"""Package a validated Train Libre food catalog for remote distribution."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "dist" / "train_libre_base_foods.db"
DEFAULT_NOTICES = ROOT / "THIRD_PARTY_NOTICES.md"
DEFAULT_OUTPUT = ROOT / "dist" / "release"
SOURCE_ID = "bls_food_catalog"
CHANNELS = {"stable", "beta"}


class ReleaseError(Exception):
    """Expected packaging or validation error."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_database(path: Path, report_path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ReleaseError(f"Catalog database does not exist: {path}")
    if not report_path.is_file():
        raise ReleaseError(f"Build report does not exist: {report_path}")

    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"Cannot read build report: {exc}") from exc
    if report.get("status") != "passed" or report.get("build_mode") != "release-candidate":
        raise ReleaseError("Only a successful strict app-catalog build can be packaged")
    if report.get("artifact_sha256") != sha256(path):
        raise ReleaseError("Build report checksum does not match the catalog database")

    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ReleaseError(f"SQLite integrity check failed: {integrity}")
        products = db.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        pending = db.execute(
            "SELECT COUNT(*) FROM legacy_food_mappings WHERE status = 'pending'"
        ).fetchone()[0]
        foods = db.execute("SELECT COUNT(*) FROM food_nutrients").fetchone()[0]
        aliases = db.execute("SELECT COUNT(*) FROM food_aliases").fetchone()[0]
        metadata = dict(db.execute("SELECT key, value FROM metadata"))
        db.close()
    except sqlite3.Error as exc:
        raise ReleaseError(f"Cannot validate catalog database: {exc}") from exc

    counts = report.get("counts", {})
    if products != counts.get("products") or foods != counts.get("nutrient_facts"):
        raise ReleaseError("Database counts do not match the strict build report")
    if (
        aliases != report.get("aliases", {}).get("total")
        or metadata.get("curated_alias_owner") != "Train Libre"
        or metadata.get("curated_alias_count") != str(aliases)
    ):
        raise ReleaseError("Alias count or Train Libre curation metadata is inconsistent")
    if pending:
        raise ReleaseError(f"Catalog still has {pending} pending legacy mappings")
    return report


def package(
    *,
    database: Path,
    report_path: Path,
    output_dir: Path,
    version: str,
    catalog_version: str,
    channel: str,
    schema_version: int,
    min_app_schema_version: int,
) -> tuple[Path, Path]:
    if channel not in CHANNELS:
        raise ReleaseError(f"Unsupported release channel: {channel}")
    if schema_version < 1 or min_app_schema_version < 1:
        raise ReleaseError("Schema versions must be positive integers")
    if min_app_schema_version > schema_version:
        raise ReleaseError("Minimum app schema cannot exceed catalog schema")
    if not version.strip() or not catalog_version.strip():
        raise ReleaseError("Release and source catalog versions must not be empty")

    report = validate_database(database, report_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    compressed_name = "train_libre_base_foods.db.gz"
    compressed_path = output_dir / compressed_name
    manifest_path = output_dir / "catalog_manifest.json"
    notices_path = output_dir / "THIRD_PARTY_NOTICES.md"
    temporary_path = compressed_path.with_suffix(compressed_path.suffix + ".tmp")

    try:
        with database.open("rb") as source, temporary_path.open("wb") as raw_output:
            with gzip.GzipFile(
                filename="",
                mode="wb",
                compresslevel=9,
                fileobj=raw_output,
                mtime=0,
            ) as compressed:
                shutil.copyfileobj(source, compressed, length=1024 * 1024)
        temporary_path.replace(compressed_path)
        shutil.copyfile(DEFAULT_NOTICES, notices_path)

        manifest = {
            "source_id": SOURCE_ID,
            "channel": channel,
            "version": version,
            "catalog_id": "bls",
            "catalog_version": catalog_version,
            "schema_version": schema_version,
            "min_app_schema_version": min_app_schema_version,
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "db_file": compressed_name,
            "db_compression": "gzip",
            "db_sha256": sha256(database),
            "download_sha256": sha256(compressed_path),
            "db_size_bytes": database.stat().st_size,
            "download_size_bytes": compressed_path.stat().st_size,
            "expected_food_count": report["counts"]["products"],
            "food_nutrient_fact_count": report["counts"]["nutrient_facts"],
            "food_alias_count": report["aliases"]["total"],
            "legacy_mapping_count": report["counts"]["legacy_mappings"],
            "license": {
                "id": "CC-BY-4.0",
                "url": "https://creativecommons.org/licenses/by/4.0/",
                "notice_file": "THIRD_PARTY_NOTICES.md",
            },
            "source": {
                "publisher": "Max Rubner-Institut",
                "title": "Bundeslebensmittelschlüssel (BLS), Version 4.0 — Deutsche Nährstoffdatenbank",
                "doi": "10.25826/Data20251217-134202-0",
            },
            "modifications": [
                "Converted the BLS source workbooks to SQLite while preserving nutrient values and provenance.",
                "Added Train Libre translations, curated search aliases and categories, and optional default portions.",
                "Added legacy food ID migration outcomes.",
            ],
        }
        manifest_tmp = manifest_path.with_suffix(".json.tmp")
        manifest_tmp.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        manifest_tmp.replace(manifest_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise

    return compressed_path, manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="Immutable release version, e.g. 4.0.0")
    parser.add_argument("--catalog-version", default="4.0", help="Upstream catalog version")
    parser.add_argument("--channel", default="stable", choices=sorted(CHANNELS))
    parser.add_argument("--schema-version", type=int, default=2)
    parser.add_argument("--min-app-schema-version", type=int, default=2)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--build-report", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report_path = args.build_report or ROOT / "reports" / f"app-build-{args.catalog_version}.json"
    try:
        compressed, manifest = package(
            database=args.database.resolve(),
            report_path=report_path.resolve(),
            output_dir=args.output_dir.resolve(),
            version=args.version,
            catalog_version=args.catalog_version,
            channel=args.channel,
            schema_version=args.schema_version,
            min_app_schema_version=args.min_app_schema_version,
        )
    except (OSError, ReleaseError, sqlite3.Error) as exc:
        print(f"release packaging error: {exc}", file=sys.stderr)
        return 2
    print(f"Packaged release database: {compressed}")
    print(f"Release manifest: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
