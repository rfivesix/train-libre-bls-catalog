#!/usr/bin/env python3
"""Import BLS 4.0 without editing upstream files; build normalized catalogs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import sqlite3
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parents[1]
LEGACY_DATABASE = ROOT / "sources" / "train-libre" / "current" / "train_libre_base_foods.db"
TOOL_VERSION = "1.0.0"
LICENSE_ID = "CC-BY-4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
PUBLISHER = "Max Rubner-Institut"
DOI = "10.25826/Data20251217-134202-0"
SUPPORTED_LOCALES = ("de", "en", "fr", "it", "ja")
TRANSLATED_LOCALES = ("fr", "it", "ja")
FOOD_CODE_RE = re.compile(r"^[A-Z][A-Z0-9]{6}$")
COMPONENT_CODE_RE = re.compile(r"^[A-Z0-9:]+$")


class CatalogError(Exception):
    """Expected input or integrity error suitable for a CLI diagnostic."""


def json_bytes(value: Any, *, pretty: bool = True) -> bytes:
    text = json.dumps(
        value,
        ensure_ascii=False,
        indent=2 if pretty else None,
        sort_keys=not pretty,
        separators=None if pretty else (",", ":"),
        allow_nan=False,
    )
    return (text + "\n").encode("utf-8")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json_bytes(value))


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError(f"Cannot read valid JSON from {path}: {exc}") from exc


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_paths(version: str) -> tuple[Path, Path, Path]:
    directory = ROOT / "sources" / "bls" / version
    return (
        directory / f"BLS_{version.replace('.', '_')}_Daten_2025_DE.xlsx",
        directory / f"BLS_{version.replace('.', '_')}_Components_DE_EN.xlsx",
        directory / f"BLS_{version.replace('.', '_')}_Dokumentation_DE.pdf",
    )


def normalized_root(version: str) -> Path:
    return ROOT / "data" / "source" / "bls" / version


def clean_cell(value: Any) -> Any:
    """Preserve workbook text and numbers while converting empty cells to null."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            raise CatalogError("Workbook contains a non-finite numeric value")
        return value
    return str(value)


def component_records(path: Path) -> list[dict[str, Any]]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook.active
        header = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True))
        expected = (
            "Index",
            "Nährstoffcode / Component code",
            "Nährstoffbezeichnung",
            "Component name",
            "Einheit / Unit",
            "Nährstoffgruppe",
            "Component group",
            "Formeln / Formula",
            "Formelanwendung / Formula application",
        )
        if tuple(header[: len(expected)]) != expected:
            raise CatalogError("Unexpected BLS component workbook header")

        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in sheet.iter_rows(min_row=2, values_only=True):
            cells = list(row) + [None] * max(0, 9 - len(row))
            code = clean_cell(cells[1])
            if code is None:
                if any(
                    cell is not None and not (isinstance(cell, str) and not cell.strip())
                    for cell in cells
                ):
                    raise CatalogError("Component workbook has an unnamed non-empty row")
                continue
            code = str(code)
            if not COMPONENT_CODE_RE.fullmatch(code):
                raise CatalogError(f"Invalid component code: {code!r}")
            if code in seen:
                raise CatalogError(f"Duplicate component code in workbook: {code}")
            seen.add(code)
            result.append(
                {
                    "index": clean_cell(cells[0]),
                    "component_code": code,
                    "name_de": clean_cell(cells[2]),
                    "name_en": clean_cell(cells[3]),
                    "unit": clean_cell(cells[4]),
                    "group_de": clean_cell(cells[5]),
                    "group_en": clean_cell(cells[6]),
                    "formula": clean_cell(cells[7]),
                    "formula_application": clean_cell(cells[8]),
                }
            )
        if not result:
            raise CatalogError("No component definitions found")
        return result
    finally:
        workbook.close()


def food_header(header: tuple[Any, ...], known_components: set[str]) -> list[dict[str, Any]]:
    note_index = len(header) - 1
    if header[0:3] != ("BLS Code", "Lebensmittelbezeichnung", "Food name"):
        raise CatalogError("Unexpected BLS food workbook identity columns")
    if header[note_index] != "Hinweis":
        raise CatalogError("Expected the final BLS food column to be Hinweis")
    nutrient_columns = header[3:note_index]
    if len(nutrient_columns) % 3:
        raise CatalogError("BLS nutrient columns are not value/origin/reference triplets")

    fields: list[dict[str, Any]] = []
    for offset in range(3, note_index, 3):
        value_header, origin_header, reference_header = header[offset : offset + 3]
        if not all(isinstance(value, str) for value in (value_header, origin_header, reference_header)):
            raise CatalogError(f"Invalid nutrient header triplet at column {offset + 1}")
        code = value_header.split(" ", 1)[0]
        if code not in known_components:
            raise CatalogError(f"No component definition for source column {code}")
        if origin_header != f"{code} Datenherkunft" or reference_header != f"{code} Referenz":
            raise CatalogError(f"Unexpected provenance headings for component {code}")
        fields.append(
            {
                "component_code": code,
                "value_header": value_header,
                "origin_header": origin_header,
                "reference_header": reference_header,
            }
        )
    if len(fields) != 138:
        raise CatalogError(f"Expected 138 BLS nutrient triplets, found {len(fields)}")
    if len({field["component_code"] for field in fields}) != len(fields):
        raise CatalogError("Duplicate nutrient component in BLS food headers")
    return fields


def food_record(row: tuple[Any, ...], nutrient_fields: list[dict[str, Any]]) -> dict[str, Any]:
    cells = list(row) + [None] * max(0, 418 - len(row))
    code = clean_cell(cells[0])
    name_de = clean_cell(cells[1])
    name_en = clean_cell(cells[2])
    if not isinstance(code, str) or not FOOD_CODE_RE.fullmatch(code):
        raise CatalogError(f"Invalid BLS food code: {code!r}")
    if not isinstance(name_de, str) or not name_de.strip():
        raise CatalogError(f"Missing German source name for {code}")
    if not isinstance(name_en, str) or not name_en.strip():
        raise CatalogError(f"Missing English source name for {code}")

    nutrients: dict[str, dict[str, Any]] = {}
    for field in nutrient_fields:
        # Column offsets are attached by food_header below.
        offset = field["offset"]
        value = clean_cell(cells[offset])
        origin = clean_cell(cells[offset + 1])
        reference = clean_cell(cells[offset + 2])
        if value is None and origin is None and reference is None:
            continue
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float, str))
        ):
            raise CatalogError(f"Unsupported source value for {code}/{field['component_code']}: {value!r}")
        nutrients[field["component_code"]] = {
            "value": value,
            "data_origin": origin,
            "reference": reference,
        }

    return {
        "bls_code": code,
        "source_group": code[0],
        "name_de": name_de,
        "name_en": name_en,
        "note": clean_cell(cells[417]),
        "nutrients": nutrients,
    }


def iter_food_rows(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    components = component_records(source_paths_for_data(path)[1])
    known_components = {item["component_code"] for item in components}
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook.active
        header = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True))
        fields = food_header(header, known_components)
        for index, field in enumerate(fields):
            field["offset"] = 3 + index * 3
        main_data_codes = {field["component_code"] for field in fields}
        for item in components:
            item["present_in_food_data"] = item["component_code"] in main_data_codes
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in sheet.iter_rows(min_row=2, values_only=True):
            if not any(cell is not None for cell in row):
                continue
            record = food_record(row, fields)
            if record["bls_code"] in seen:
                raise CatalogError(f"Duplicate BLS food code: {record['bls_code']}")
            seen.add(record["bls_code"])
            records.append(record)
        if len(records) != 7140:
            raise CatalogError(f"Expected 7,140 BLS foods, found {len(records)}")
        return records, components
    finally:
        workbook.close()


def source_paths_for_data(food_path: Path) -> tuple[Path, Path, Path]:
    """Resolve companion files from the canonical source workbook location."""
    version_dir = food_path.parent
    return (
        food_path,
        version_dir / food_path.name.replace("Daten_2025_DE", "Components_DE_EN"),
        version_dir / food_path.name.replace("Daten_2025_DE.xlsx", "Dokumentation_DE.pdf"),
    )


def import_source(version: str, *, replace: bool) -> dict[str, Any]:
    data_path, components_path, documentation_path = source_paths(version)
    for path in (data_path, components_path, documentation_path):
        if not path.is_file():
            raise CatalogError(f"Required source file is missing: {path}")

    output_root = normalized_root(version)
    food_dir = output_root / "foods"
    components_output = output_root / "nutrient_components.json"
    manifest_output = output_root / "manifest.json"
    if output_root.exists() and any(output_root.iterdir()) and not replace:
        raise CatalogError(f"Normalized source already exists; pass --replace to rebuild {output_root}")

    records, components = iter_food_rows(data_path)
    data_codes = {code for record in records for code in record["nutrients"]}
    definitions = {item["component_code"] for item in components}
    if not data_codes.issubset(definitions):
        raise CatalogError(f"No component definitions for {sorted(data_codes - definitions)}")

    output_root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{version}-source-", dir=output_root.parent))
    try:
        staged_food_dir = staging / "foods"
        for record in records:
            target = staged_food_dir / record["source_group"] / f"{record['bls_code']}.json"
            write_json(target, record)
        write_json(staging / "nutrient_components.json", components)
        manifest = make_manifest(version, data_path, components_path, documentation_path, records, components)
        write_json(staging / "manifest.json", manifest)

        if output_root.exists():
            if not replace:
                raise CatalogError(f"Output path exists: {output_root}")
            shutil.rmtree(output_root)
        staging.rename(output_root)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    return manifest


def make_manifest(
    version: str,
    data_path: Path,
    components_path: Path,
    documentation_path: Path,
    records: list[dict[str, Any]],
    components: list[dict[str, Any]],
) -> dict[str, Any]:
    facts = [fact for food in records for fact in food["nutrients"].values()]
    origins = {fact["data_origin"] for fact in facts if fact["data_origin"] is not None}
    references = {fact["reference"] for fact in facts if fact["reference"] is not None}
    return {
        "catalog_id": "bls",
        "source_version": version,
        "source_release": "2025",
        "source_title": "Bundeslebensmittelschlüssel (BLS), Deutsche Nährstoffdatenbank",
        "publisher": PUBLISHER,
        "doi": DOI,
        "license": LICENSE_ID,
        "license_url": LICENSE_URL,
        "tool_version": TOOL_VERSION,
        "input_files": {
            path.name: sha256(path)
            for path in (data_path, components_path, documentation_path)
        },
        "food_count": len(records),
        "nutrient_triplet_count": 138,
        "component_definition_count": len(components),
        "food_nutrient_fact_count": len(facts),
        "data_origin_count": len(origins),
        "source_reference_count": len(references),
        "facts_with_null_value": sum(fact["value"] is None for fact in facts),
        "facts_with_qualitative_value": sum(isinstance(fact["value"], str) for fact in facts),
        "facts_with_explicit_zero": sum(fact["value"] == 0 for fact in facts),
        "food_notes_count": sum(food["note"] is not None for food in records),
        "source_records_are_unmodified": True,
    }


def load_normalized_foods(version: str) -> list[dict[str, Any]]:
    root = normalized_root(version) / "foods"
    if not root.is_dir():
        raise CatalogError(f"Normalized records not found: {root}; run import-source first")
    files = sorted(root.glob("*/*.json"))
    if not files:
        raise CatalogError(f"No normalized food JSON files found under {root}")
    records = [read_json(path) for path in files]
    codes = [record.get("bls_code") for record in records]
    if any(not isinstance(code, str) or not FOOD_CODE_RE.fullmatch(code) for code in codes):
        raise CatalogError("A normalized source record has an invalid BLS code")
    if len(codes) != len(set(codes)):
        raise CatalogError("Duplicate BLS code in normalized source records")
    if len(records) != 7140:
        raise CatalogError(f"Expected 7,140 normalized foods, found {len(records)}")
    for record in records:
        # The root above is foods/, so validate both the filename and group directory.
        actual_path = root / record.get("source_group", "") / f"{record.get('bls_code', '')}.json"
        if not actual_path.is_file():
            raise CatalogError(f"Normalized source file is in the wrong path for {record.get('bls_code')}")
        if set(record) != {"bls_code", "source_group", "name_de", "name_en", "note", "nutrients"}:
            raise CatalogError(f"Unexpected source fields for {record.get('bls_code', '<unknown>')}")
        if record.get("source_group") != record["bls_code"][0]:
            raise CatalogError(f"Source group does not match BLS code {record['bls_code']}")
        if not isinstance(record.get("name_de"), str) or not isinstance(record.get("name_en"), str):
            raise CatalogError(f"Source names must be strings for {record['bls_code']}")
        if record.get("note") is not None and not isinstance(record["note"], str):
            raise CatalogError(f"Source note must be text or null for {record['bls_code']}")
        nutrients = record.get("nutrients")
        if not isinstance(nutrients, dict):
            raise CatalogError(f"Invalid nutrient map for {record['bls_code']}")
        for code, fact in nutrients.items():
            if not COMPONENT_CODE_RE.fullmatch(code):
                raise CatalogError(f"Invalid nutrient component code {code!r}")
            if set(fact) != {"value", "data_origin", "reference"}:
                raise CatalogError(f"Invalid nutrient fact {record['bls_code']}/{code}")
            value = fact["value"]
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float, str))
            ):
                raise CatalogError(f"Unsupported nutrient value {record['bls_code']}/{code}")
            if value is not None and not isinstance(value, str) and not math.isfinite(float(value)):
                raise CatalogError(f"Non-finite nutrient value {record['bls_code']}/{code}")
            if any(fact[field] is not None and not isinstance(fact[field], str) for field in ("data_origin", "reference")):
                raise CatalogError(f"Nutrient provenance must be text or null for {record['bls_code']}/{code}")
    return records


def validate_source(version: str) -> dict[str, Any]:
    data_path, components_path, documentation_path = source_paths(version)
    expected_records, expected_components = iter_food_rows(data_path)
    expected_by_code = {record["bls_code"]: record for record in expected_records}
    root = normalized_root(version)
    actual_records = load_normalized_foods(version)
    actual_by_code = {record["bls_code"]: record for record in actual_records}
    missing = sorted(expected_by_code.keys() - actual_by_code.keys())
    extra = sorted(actual_by_code.keys() - expected_by_code.keys())
    changed: list[str] = []
    for code in sorted(expected_by_code.keys() & actual_by_code.keys()):
        if expected_by_code[code] != actual_by_code[code]:
            changed.append(code)
            if len(changed) >= 10:
                break
    if missing or extra or changed:
        raise CatalogError(
            f"Source mismatch: missing={missing[:5]}, extra={extra[:5]}, changed={changed[:10]}"
        )

    expected_components_by_code = {item["component_code"]: item for item in expected_components}
    actual_components = read_json(root / "nutrient_components.json")
    actual_components_by_code = {item["component_code"]: item for item in actual_components}
    if expected_components_by_code != actual_components_by_code:
        raise CatalogError("Normalized component definitions differ from the source workbook")

    manifest = read_json(root / "manifest.json")
    source_manifest = make_manifest(
        version,
        data_path,
        components_path,
        documentation_path,
        expected_records,
        actual_components,
    )
    for key in (
        "input_files",
        "food_count",
        "nutrient_triplet_count",
        "component_definition_count",
        "food_nutrient_fact_count",
        "data_origin_count",
        "source_reference_count",
    ):
        if manifest.get(key) != source_manifest.get(key):
            raise CatalogError(f"Source manifest mismatch for {key}")

    facts = [fact for food in actual_records for fact in food["nutrients"].values()]
    origins = {fact["data_origin"] for fact in facts if fact["data_origin"] is not None}
    references = {fact["reference"] for fact in facts if fact["reference"] is not None}
    report = {
        "catalog_id": "bls",
        "source_version": version,
        "status": "passed",
        "food_count": len(actual_records),
        "nutrient_triplet_count": 138,
        "component_definition_count": len(actual_components),
        "food_nutrient_fact_count": len(facts),
        "data_origin_count": len(origins),
        "source_reference_count": len(references),
        "facts_with_null_value": sum(fact["value"] is None for fact in facts),
        "facts_with_qualitative_value": sum(isinstance(fact["value"], str) for fact in facts),
        "facts_with_explicit_zero": sum(fact["value"] == 0 for fact in facts),
        "food_notes_count": sum(food["note"] is not None for food in actual_records),
        "source_records_compared_field_by_field": True,
        "input_files": manifest["input_files"],
    }
    write_json(ROOT / "reports" / f"source-validation-{version}.json", report)
    return report


def build_source_db(version: str, *, force: bool) -> Path:
    validate_source(version)
    records = load_normalized_foods(version)
    root = normalized_root(version)
    components = read_json(root / "nutrient_components.json")
    output = ROOT / "dist" / f"bls-{version}-source.sqlite"
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and not force:
        raise CatalogError(f"Output exists; pass --force to replace generated artifact: {output}")
    fd, temp_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    Path(temp_name).unlink(missing_ok=True)
    temporary = Path(temp_name)
    try:
        db = sqlite3.connect(temporary)
        try:
            db.execute("PRAGMA foreign_keys = ON")
            db.executescript(
                """
                CREATE TABLE catalog_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE foods (
                    bls_code TEXT PRIMARY KEY,
                    source_group TEXT NOT NULL,
                    name_de TEXT NOT NULL,
                    name_en TEXT NOT NULL,
                    note TEXT
                );
                CREATE TABLE nutrient_components (
                    component_code TEXT PRIMARY KEY,
                    source_index INTEGER,
                    name_de TEXT,
                    name_en TEXT,
                    unit TEXT,
                    group_de TEXT,
                    group_en TEXT,
                    formula TEXT,
                    formula_application TEXT,
                    present_in_food_data INTEGER NOT NULL CHECK (present_in_food_data IN (0, 1))
                );
                CREATE TABLE data_origins (
                    origin_id INTEGER PRIMARY KEY,
                    label TEXT NOT NULL UNIQUE
                );
                CREATE TABLE source_references (
                    reference_id INTEGER PRIMARY KEY,
                    citation TEXT NOT NULL UNIQUE
                );
                CREATE TABLE food_nutrients (
                    bls_code TEXT NOT NULL REFERENCES foods(bls_code),
                    component_code TEXT NOT NULL REFERENCES nutrient_components(component_code),
                    value_numeric REAL,
                    value_text TEXT,
                    origin_id INTEGER REFERENCES data_origins(origin_id),
                    reference_id INTEGER REFERENCES source_references(reference_id),
                    PRIMARY KEY (bls_code, component_code),
                    CHECK (value_numeric IS NULL OR value_text IS NULL),
                    CHECK (value_numeric IS NOT NULL OR value_text IS NOT NULL OR origin_id IS NOT NULL OR reference_id IS NOT NULL)
                );
                CREATE INDEX food_nutrients_component_idx ON food_nutrients(component_code);
                CREATE INDEX foods_name_de_idx ON foods(name_de);
                CREATE INDEX foods_name_en_idx ON foods(name_en);
                PRAGMA user_version = 1;
                """
            )
            manifest = read_json(root / "manifest.json")
            metadata = {
                "catalog_id": "bls",
                "source_version": version,
                "source_title": manifest["source_title"],
                "publisher": PUBLISHER,
                "doi": DOI,
                "license": LICENSE_ID,
                "license_url": LICENSE_URL,
                "schema_version": "1",
                "tool_version": TOOL_VERSION,
                "input_files_sha256": json.dumps(manifest["input_files"], sort_keys=True),
            }
            db.executemany("INSERT INTO catalog_metadata VALUES (?, ?)", metadata.items())
            db.executemany(
                "INSERT INTO foods VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        food["bls_code"],
                        food["source_group"],
                        food["name_de"],
                        food["name_en"],
                        food["note"],
                    )
                    for food in records
                ],
            )
            db.executemany(
                "INSERT INTO nutrient_components VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        item["component_code"],
                        item["index"],
                        item["name_de"],
                        item["name_en"],
                        item["unit"],
                        item["group_de"],
                        item["group_en"],
                        item["formula"],
                        item["formula_application"],
                        int(item["present_in_food_data"]),
                    )
                    for item in components
                ],
            )
            origins = sorted({fact["data_origin"] for food in records for fact in food["nutrients"].values() if fact["data_origin"] is not None})
            references = sorted({fact["reference"] for food in records for fact in food["nutrients"].values() if fact["reference"] is not None})
            origin_ids = {label: index for index, label in enumerate(origins, start=1)}
            reference_ids = {citation: index for index, citation in enumerate(references, start=1)}
            db.executemany("INSERT INTO data_origins VALUES (?, ?)", [(origin_ids[label], label) for label in origins])
            db.executemany("INSERT INTO source_references VALUES (?, ?)", [(reference_ids[citation], citation) for citation in references])
            nutrient_rows = (
                (
                    food["bls_code"],
                    code,
                    fact["value"] if isinstance(fact["value"], (int, float)) else None,
                    fact["value"] if isinstance(fact["value"], str) else None,
                    origin_ids.get(fact["data_origin"]),
                    reference_ids.get(fact["reference"]),
                )
                for food in records
                for code, fact in food["nutrients"].items()
            )
            db.executemany("INSERT INTO food_nutrients VALUES (?, ?, ?, ?, ?, ?)", nutrient_rows)
            db.commit()
            counts = {
                "foods": db.execute("SELECT COUNT(*) FROM foods").fetchone()[0],
                "nutrient_components": db.execute("SELECT COUNT(*) FROM nutrient_components").fetchone()[0],
                "food_nutrients": db.execute("SELECT COUNT(*) FROM food_nutrients").fetchone()[0],
                "data_origins": db.execute("SELECT COUNT(*) FROM data_origins").fetchone()[0],
                "source_references": db.execute("SELECT COUNT(*) FROM source_references").fetchone()[0],
            }
            if counts["foods"] != manifest["food_count"]:
                raise CatalogError("Source database food count does not match manifest")
            if counts["food_nutrients"] != manifest["food_nutrient_fact_count"]:
                raise CatalogError("Source database nutrient fact count does not match manifest")
        finally:
            db.close()
        if output.exists():
            output.unlink()
        temporary.replace(output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        # mkstemp's descriptor is not used after SQLite opens the path.
        try:
            import os

            os.close(fd)
        except OSError:
            pass
    return output


def load_curation(version: str) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    source_codes = {record["bls_code"] for record in load_normalized_foods(version)}
    overlays: dict[str, dict[str, Any]] = {}
    curation_root = ROOT / "curation" / "foods"
    for path in sorted(curation_root.glob("*/*.json")):
        overlay = read_json(path)
        if not isinstance(overlay, dict):
            raise CatalogError(f"Curation overlay must be a JSON object: {path}")
        code = overlay.get("bls_code")
        if not isinstance(code, str) or code not in source_codes:
            raise CatalogError(f"Curation file has unknown BLS code: {path}")
        if path.stem != code or path.parent.name != code[0]:
            raise CatalogError(f"Curation file path must be <group>/{code}.json: {path}")
        if code in overlays:
            raise CatalogError(f"Duplicate curation overlay for {code}")
        allowed = {"bls_code", "display_names", "aliases", "category_id"}
        if set(overlay) - allowed:
            raise CatalogError(f"Curation overlay contains forbidden fields: {path}")
        names = overlay.get("display_names", {})
        if not isinstance(names, dict) or set(names) - set(TRANSLATED_LOCALES):
            raise CatalogError(f"Invalid display_names in {path}")
        for locale, translation in names.items():
            if (
                not isinstance(translation, dict)
                or set(translation) != {"value", "method"}
                or not isinstance(translation.get("value"), str)
                or not translation["value"].strip()
            ):
                raise CatalogError(f"Empty or malformed {locale} translation in {path}")
            if translation.get("method") not in {"human", "machine", "machine-reviewed"}:
                raise CatalogError(f"Missing translation method for {code}/{locale}")
        aliases = overlay.get("aliases", [])
        if not isinstance(aliases, list):
            raise CatalogError(f"Aliases must be a list in {path}")
        for alias in aliases:
            if not isinstance(alias, dict) or set(alias) != {"language_code", "value", "kind", "method"}:
                raise CatalogError(f"Malformed alias in {path}")
            if (
                alias["language_code"] not in SUPPORTED_LOCALES
                or not isinstance(alias["value"], str)
                or not alias["value"].strip()
            ):
                raise CatalogError(f"Invalid alias in {path}")
            if alias["kind"] not in {"synonym", "spelling", "transliteration", "regional"}:
                raise CatalogError(f"Invalid alias kind in {path}")
            if alias["method"] not in {"source", "human", "machine", "machine-reviewed"}:
                raise CatalogError(f"Invalid alias method in {path}")
        overlays[code] = overlay

    category_path = ROOT / "curation" / "categories.json"
    category_doc = read_json(category_path)
    if set(category_doc) != {"categories"} or not isinstance(category_doc["categories"], list):
        raise CatalogError("curation/categories.json must contain a categories list")
    categories: dict[str, dict[str, Any]] = {}
    for category in category_doc["categories"]:
        if (
            not isinstance(category, dict)
            or set(category) - {"category_id", "names", "emoji"}
            or not {"category_id", "names"}.issubset(category)
        ):
            raise CatalogError("Each category needs category_id and names, with optional emoji")
        if category.get("emoji") is not None and not isinstance(category["emoji"], str):
            raise CatalogError(f"Category {category.get('category_id')} has an invalid emoji")
        category_id = category.get("category_id")
        names = category.get("names")
        if not isinstance(category_id, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", category_id):
            raise CatalogError(f"Invalid category ID: {category_id!r}")
        if category_id in categories:
            raise CatalogError(f"Duplicate category ID: {category_id}")
        if not isinstance(names, dict) or set(names) != set(SUPPORTED_LOCALES):
            raise CatalogError(f"Category {category_id} needs names in {SUPPORTED_LOCALES}")
        if any(not isinstance(names[locale], str) or not names[locale].strip() for locale in SUPPORTED_LOCALES):
            raise CatalogError(f"Category {category_id} has an empty localized name")
        categories[category_id] = category
    for code, overlay in overlays.items():
        category_id = overlay.get("category_id")
        if category_id is not None and category_id not in categories:
            raise CatalogError(f"Unknown category {category_id!r} on {code}")
    return overlays, categories


def normalized_match_key(value: str | None) -> str:
    decomposed = unicodedata.normalize("NFKD", value or "")
    ascii_text = decomposed.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", ascii_text).strip()


def prepare_legacy_mapping(*, replace: bool) -> Path:
    if not LEGACY_DATABASE.is_file():
        raise CatalogError(f"Legacy database snapshot is missing: {LEGACY_DATABASE}")
    output = ROOT / "curation" / "migrations" / "legacy-base-foods.json"
    if output.exists():
        existing = read_json(output)
        if not replace:
            raise CatalogError(f"Mapping already exists; pass --replace to regenerate suggestions: {output}")
        if any(entry.get("status") != "pending" for entry in existing.get("entries", [])):
            raise CatalogError("Refusing to discard reviewed legacy mappings")

    source_db = sqlite3.connect(LEGACY_DATABASE)
    try:
        products = source_db.execute(
            "SELECT barcode, name_de, name_en, category FROM products ORDER BY barcode"
        ).fetchall()
        legacy_categories = [row[0] for row in source_db.execute("SELECT key FROM categories ORDER BY key")]
    finally:
        source_db.close()

    source_foods = load_normalized_foods("4.0")
    indexes: dict[str, dict[str, set[str]]] = {"de": {}, "en": {}}
    for locale, field in (("de", "name_de"), ("en", "name_en")):
        for food in source_foods:
            key = normalized_match_key(food[field])
            if key:
                indexes[locale].setdefault(key, set()).add(food["bls_code"])

    entries = []
    for product_id, name_de, name_en, category_id in products:
        candidates: dict[str, set[str]] = {}
        for locale, name in (("de", name_de), ("en", name_en)):
            key = normalized_match_key(name)
            for code in indexes[locale].get(key, set()) if key else ():
                candidates.setdefault(code, set()).add(locale)
        entries.append(
            {
                "legacy_product_id": product_id,
                "legacy_name_de": name_de,
                "legacy_name_en": name_en,
                "legacy_category_id": category_id,
                "suggested_matches": [
                    {"bls_code": code, "exact_name_locales": sorted(locales)}
                    for code, locales in sorted(candidates.items())
                ],
                "target_bls_code": None,
                "status": "pending",
                "review_note": None,
            }
        )

    document = {
        "mapping_version": 1,
        "legacy_database": LEGACY_DATABASE.relative_to(ROOT).as_posix(),
        "legacy_database_sha256": sha256(LEGACY_DATABASE),
        "legacy_category_ids": legacy_categories,
        "target_catalog": "bls:4.0",
        "matching_policy": "Suggestions use normalized exact German and English names; all targets require review.",
        "entries": entries,
    }
    write_json(output, document)
    suggested_count = sum(bool(item["suggested_matches"]) for item in entries)
    print(f"Prepared {len(entries)} legacy records at {output.relative_to(ROOT)}")
    print(f"Exact-name suggestions for review: {suggested_count}; no target mappings were auto-approved")
    return output


def load_legacy_mapping(version: str) -> list[dict[str, Any]]:
    path = ROOT / "curation" / "migrations" / "legacy-base-foods.json"
    document = read_json(path)
    if document.get("target_catalog") != f"bls:{version}":
        raise CatalogError(f"Legacy mapping targets a different catalog version: {path}")
    if document.get("legacy_database_sha256") != sha256(LEGACY_DATABASE):
        raise CatalogError("Legacy mapping was prepared against a different baseline database")
    expected_db = sqlite3.connect(LEGACY_DATABASE)
    try:
        expected = {
            row[0]: {"legacy_name_de": row[1], "legacy_name_en": row[2], "legacy_category_id": row[3]}
            for row in expected_db.execute("SELECT barcode, name_de, name_en, category FROM products")
        }
        expected_categories = sorted(row[0] for row in expected_db.execute("SELECT key FROM categories"))
    finally:
        expected_db.close()
    if document.get("legacy_category_ids") != expected_categories:
        raise CatalogError("Legacy mapping category list differs from the baseline database")
    entries = document.get("entries")
    if not isinstance(entries, list) or len(entries) != len(expected):
        raise CatalogError(f"Legacy mapping must contain all {len(expected)} baseline products")
    source_codes = {food["bls_code"] for food in load_normalized_foods(version)}
    seen: set[str] = set()
    for entry in entries:
        required_fields = {
            "legacy_product_id",
            "legacy_name_de",
            "legacy_name_en",
            "legacy_category_id",
            "suggested_matches",
            "target_bls_code",
            "status",
            "review_note",
        }
        if not isinstance(entry, dict) or set(entry) != required_fields:
            raise CatalogError("Legacy mapping entries must be objects")
        legacy_id = entry.get("legacy_product_id")
        if legacy_id not in expected or legacy_id in seen:
            raise CatalogError(f"Unexpected or duplicate legacy product ID: {legacy_id!r}")
        seen.add(legacy_id)
        if any(entry.get(key) != value for key, value in expected[legacy_id].items()):
            raise CatalogError(f"Legacy source fields were changed for {legacy_id}")
        if not isinstance(entry.get("suggested_matches"), list):
            raise CatalogError(f"Legacy suggestions must be a list for {legacy_id}")
        for suggestion in entry["suggested_matches"]:
            if (
                not isinstance(suggestion, dict)
                or set(suggestion) != {"bls_code", "exact_name_locales"}
                or suggestion.get("bls_code") not in source_codes
                or not isinstance(suggestion.get("exact_name_locales"), list)
                or not suggestion["exact_name_locales"]
                or set(suggestion["exact_name_locales"]) - {"de", "en"}
            ):
                raise CatalogError(f"Invalid BLS candidate suggestion for {legacy_id}")
        status = entry.get("status")
        target = entry.get("target_bls_code")
        if status not in {"pending", "mapped", "no_equivalent"}:
            raise CatalogError(f"Invalid legacy mapping status for {legacy_id}")
        if status == "mapped" and target not in source_codes:
            raise CatalogError(f"Mapped legacy target is not a BLS food for {legacy_id}: {target}")
        if status != "mapped" and target is not None:
            raise CatalogError(f"Only mapped entries may have a target BLS code: {legacy_id}")
        if status == "no_equivalent" and not str(entry.get("review_note") or "").strip():
            raise CatalogError(f"A no_equivalent mapping needs a review note: {legacy_id}")
    return entries


def build_app(version: str, output: Path, *, preview: bool, force: bool) -> Path:
    validate_source(version)
    records = load_normalized_foods(version)
    components = read_json(normalized_root(version) / "nutrient_components.json")
    overlays, categories = load_curation(version)
    legacy_mappings = load_legacy_mapping(version)
    if not preview:
        if len(overlays) != len(records):
            raise CatalogError(f"Strict build needs curation for all {len(records)} foods; found {len(overlays)}")
        for food in records:
            overlay = overlays[food["bls_code"]]
            missing = [locale for locale in TRANSLATED_LOCALES if locale not in overlay.get("display_names", {})]
            if missing:
                raise CatalogError(f"{food['bls_code']} is missing translations: {', '.join(missing)}")
            category_id = overlay.get("category_id")
            if not category_id:
                raise CatalogError(f"{food['bls_code']} has no category assignment")
        pending_legacy = [entry["legacy_product_id"] for entry in legacy_mappings if entry["status"] == "pending"]
        if pending_legacy:
            raise CatalogError(f"Strict build has {len(pending_legacy)} unresolved legacy food mappings")

    if output.exists() and not force:
        raise CatalogError(f"Output exists; pass --force to replace generated artifact: {output}")
    if not LEGACY_DATABASE.is_file():
        raise CatalogError(f"Legacy schema snapshot is missing: {LEGACY_DATABASE}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output.with_name(f".{output.name}.tmp")
    temp_path.unlink(missing_ok=True)
    db = sqlite3.connect(temp_path)
    try:
        db.execute("PRAGMA foreign_keys = ON")
        legacy = sqlite3.connect(LEGACY_DATABASE)
        try:
            legacy.backup(db)
        finally:
            legacy.close()
        # Preserve the legacy table definitions, columns, constraints, and indexes.
        db.execute("DELETE FROM products")
        db.execute("DELETE FROM categories")
        db.execute("DELETE FROM metadata")
        db.executescript(
            """
            CREATE TABLE nutrient_components (
                component_code TEXT PRIMARY KEY,
                name_de TEXT,
                name_en TEXT,
                unit TEXT,
                group_de TEXT,
                group_en TEXT,
                formula TEXT,
                formula_application TEXT,
                present_in_food_data INTEGER NOT NULL
            );
            CREATE TABLE data_origins (
                origin_id INTEGER PRIMARY KEY,
                label TEXT NOT NULL UNIQUE
            );
            CREATE TABLE source_references (
                reference_id INTEGER PRIMARY KEY,
                citation TEXT NOT NULL UNIQUE
            );
            CREATE TABLE food_nutrients (
                barcode TEXT NOT NULL REFERENCES products(barcode),
                component_code TEXT NOT NULL REFERENCES nutrient_components(component_code),
                value_numeric REAL,
                value_text TEXT,
                origin_id INTEGER REFERENCES data_origins(origin_id),
                reference_id INTEGER REFERENCES source_references(reference_id),
                PRIMARY KEY (barcode, component_code),
                CHECK (value_numeric IS NULL OR value_text IS NULL),
                CHECK (value_numeric IS NOT NULL OR value_text IS NOT NULL OR origin_id IS NOT NULL OR reference_id IS NOT NULL)
            );
            CREATE TABLE food_source_metadata (
                barcode TEXT PRIMARY KEY REFERENCES products(barcode),
                source_name TEXT NOT NULL,
                source_id TEXT NOT NULL,
                source_version TEXT NOT NULL,
                source_license TEXT NOT NULL,
                source_doi TEXT NOT NULL,
                note TEXT
            );
            CREATE TABLE food_aliases (
                barcode TEXT NOT NULL REFERENCES products(barcode),
                language_code TEXT NOT NULL,
                alias TEXT NOT NULL,
                kind TEXT NOT NULL,
                method TEXT NOT NULL,
                PRIMARY KEY (barcode, language_code, alias)
            );
            CREATE TABLE legacy_food_mappings (
                legacy_product_id TEXT PRIMARY KEY,
                legacy_name_de TEXT NOT NULL,
                legacy_name_en TEXT NOT NULL,
                legacy_category_id TEXT NOT NULL,
                target_barcode TEXT REFERENCES products(barcode),
                status TEXT NOT NULL CHECK (status IN ('pending', 'mapped', 'no_equivalent')),
                suggested_matches_json TEXT NOT NULL,
                review_note TEXT,
                CHECK ((status = 'mapped' AND target_barcode IS NOT NULL) OR
                       (status <> 'mapped' AND target_barcode IS NULL))
            );
            CREATE INDEX food_nutrients_component_idx ON food_nutrients(component_code);
            CREATE INDEX food_aliases_language_alias_idx ON food_aliases(language_code, alias);
            CREATE INDEX legacy_food_mappings_target_idx ON legacy_food_mappings(target_barcode);
            """
        )

        category_rows = []
        for category_id, category in sorted(categories.items()):
            names = category["names"]
            category_rows.append(
                (category_id, names["de"], names["en"], category.get("emoji"), names["fr"], names["it"], names["ja"])
            )
        fallback = (
            {"__unmapped__": ("Noch nicht zugeordnet", "Unmapped", "Non classé", "Non classificato", "未分類")}
            if preview
            else {}
        )
        for key, names in fallback.items():
            if key not in categories:
                category_rows.append((key, *names[:2], None, *names[2:]))
        db.executemany("INSERT INTO categories VALUES (?, ?, ?, ?, ?, ?, ?)", category_rows)

        category_by_id = {category["category_id"]: category for category in categories.values()}
        origins = sorted({fact["data_origin"] for food in records for fact in food["nutrients"].values() if fact["data_origin"] is not None})
        references = sorted({fact["reference"] for food in records for fact in food["nutrients"].values() if fact["reference"] is not None})
        origin_ids = {label: index for index, label in enumerate(origins, start=1)}
        reference_ids = {citation: index for index, citation in enumerate(references, start=1)}
        products = []
        source_meta = []
        aliases_to_insert = []
        all_nutrients = []
        for food in records:
            code = food["bls_code"]
            overlay = overlays.get(code, {})
            translations = overlay.get("display_names", {})
            category_id = overlay.get("category_id") or "__unmapped__"
            category = category_by_id.get(category_id)
            category_names = category["names"] if category else {
                "de": "Noch nicht zugeordnet",
                "en": "Unmapped",
                "fr": "Non classé",
                "it": "Non classificato",
                "ja": "未分類",
            }
            names = {
                "de": food["name_de"],
                "en": food["name_en"],
                **{locale: translations.get(locale, {}).get("value") for locale in TRANSLATED_LOCALES},
            }
            product_id = f"bls:{code}"
            values = food["nutrients"]

            def value(component: str) -> float | int | None:
                fact = values.get(component)
                if fact is None or isinstance(fact["value"], str):
                    return None
                return fact["value"]

            kcal, kj = value("ENERCC"), value("ENERCJ")
            products.append(
                (
                    product_id,
                    food["name_de"],
                    food["name_de"],
                    food["name_en"],
                    category_id,
                    category_names["de"],
                    category_names["en"],
                    None if kcal is None else int(math.floor(float(kcal) + 0.5)),
                    value("PROT625"),
                    value("CHO"),
                    value("FAT"),
                    None if kj is None else int(math.floor(float(kj) + 0.5)),
                    value("FIBT"),
                    value("SUGAR"),
                    value("NACL"),
                    value("NA"),
                    value("CA"),
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    names["fr"],
                    category_names["fr"],
                    names["it"],
                    category_names["it"],
                    names["ja"],
                    category_names["ja"],
                )
            )
            source_meta.append((product_id, "BLS", code, version, LICENSE_ID, DOI, food["note"]))
            aliases_to_insert.extend(
                (product_id, alias["language_code"], alias["value"], alias["kind"], alias["method"])
                for alias in overlay.get("aliases", [])
            )
            all_nutrients.extend(
                (
                    product_id,
                    nutrient_code,
                    fact["value"] if isinstance(fact["value"], (int, float)) else None,
                    fact["value"] if isinstance(fact["value"], str) else None,
                    origin_ids.get(fact["data_origin"]),
                    reference_ids.get(fact["reference"]),
                )
                for nutrient_code, fact in values.items()
            )

        db.executemany("INSERT INTO products VALUES (" + ",".join("?" for _ in range(29)) + ")", products)
        db.executemany("INSERT INTO food_source_metadata VALUES (?, ?, ?, ?, ?, ?, ?)", source_meta)
        db.executemany("INSERT INTO food_aliases VALUES (?, ?, ?, ?, ?)", aliases_to_insert)
        db.executemany(
            "INSERT INTO legacy_food_mappings VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    item["legacy_product_id"],
                    item["legacy_name_de"],
                    item["legacy_name_en"],
                    item["legacy_category_id"],
                    f"bls:{item['target_bls_code']}" if item["target_bls_code"] else None,
                    item["status"],
                    json.dumps(item["suggested_matches"], ensure_ascii=False, separators=(",", ":")),
                    item["review_note"],
                )
                for item in legacy_mappings
            ],
        )
        db.executemany(
            "INSERT INTO nutrient_components VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    item["component_code"],
                    item["name_de"],
                    item["name_en"],
                    item["unit"],
                    item["group_de"],
                    item["group_en"],
                    item["formula"],
                    item["formula_application"],
                    int(item["present_in_food_data"]),
                )
                for item in components
            ],
        )
        db.executemany("INSERT INTO data_origins VALUES (?, ?)", [(origin_ids[label], label) for label in origins])
        db.executemany("INSERT INTO source_references VALUES (?, ?)", [(reference_ids[citation], citation) for citation in references])
        db.executemany("INSERT INTO food_nutrients VALUES (?, ?, ?, ?, ?, ?)", all_nutrients)
        source_manifest = read_json(normalized_root(version) / "manifest.json")
        metadata = {
            "version": f"bls-{version}",
            "catalog_id": "train-libre-base-foods",
            "source_name": "BLS",
            "source_version": version,
            "source_doi": DOI,
            "source_license": LICENSE_ID,
            "schema_version": "1",
            "tool_version": TOOL_VERSION,
            "source_manifest_sha256": hashlib.sha256(json_bytes(source_manifest)).hexdigest(),
            "build_mode": "preview" if preview else "release-candidate",
        }
        db.executemany("INSERT INTO metadata VALUES (?, ?)", metadata.items())
        db.commit()
        expected_nutrients = source_manifest["food_nutrient_fact_count"]
        actual_nutrients = db.execute("SELECT COUNT(*) FROM food_nutrients").fetchone()[0]
        if actual_nutrients != expected_nutrients:
            raise CatalogError(f"App asset has {actual_nutrients} nutrient facts; expected {expected_nutrients}")
        actual_products = db.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        if actual_products != len(records):
            raise CatalogError("App asset product count does not match source food count")
        category_counts = dict(db.execute("SELECT category, COUNT(*) FROM products GROUP BY category"))
        legacy_mapping_counts = dict(
            db.execute("SELECT status, COUNT(*) FROM legacy_food_mappings GROUP BY status")
        )
        asset_counts = {
            "products": actual_products,
            "categories": db.execute("SELECT COUNT(*) FROM categories").fetchone()[0],
            "nutrient_facts": actual_nutrients,
            "legacy_mappings": db.execute("SELECT COUNT(*) FROM legacy_food_mappings").fetchone()[0],
        }
    except Exception:
        db.close()
        temp_path.unlink(missing_ok=True)
        raise
    db.close()
    if output.exists():
        if not force:
            temp_path.unlink(missing_ok=True)
            raise CatalogError(f"Output exists; pass --force to replace generated artifact: {output}")
        output.unlink()
    temp_path.replace(output)
    build_report = {
        "status": "preview" if preview else "passed",
        "source_catalog": f"bls:{version}",
        "artifact": output.relative_to(ROOT).as_posix() if output.is_relative_to(ROOT) else output.name,
        "artifact_sha256": sha256(output),
        "legacy_database_sha256": sha256(LEGACY_DATABASE),
        "counts": asset_counts,
        "products_by_category": category_counts,
        "legacy_mappings_by_status": legacy_mapping_counts,
        "category_assignment_coverage": sum(count for key, count in category_counts.items() if key != "__unmapped__"),
        "translation_overlay_count": len(overlays),
        "build_mode": "preview" if preview else "release-candidate",
    }
    write_json(ROOT / "reports" / f"app-build-{version}.json", build_report)
    return output


def command_import(args: argparse.Namespace) -> None:
    manifest = import_source(args.version, replace=args.replace)
    print(f"Imported {manifest['food_count']} BLS foods into {normalized_root(args.version)}")
    print(f"Preserved {manifest['food_nutrient_fact_count']} nutrient facts and {manifest['component_definition_count']} component definitions")


def command_validate(args: argparse.Namespace) -> None:
    report = validate_source(args.version)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Report: reports/source-validation-{args.version}.json")


def command_source_db(args: argparse.Namespace) -> None:
    output = build_source_db(args.version, force=args.force)
    print(f"Built normalized source database: {output.relative_to(ROOT)}")


def command_app(args: argparse.Namespace) -> None:
    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output
    result = build_app(args.version, output, preview=args.preview, force=args.force)
    print(f"Built {'preview' if args.preview else 'strict'} Train Libre catalog: {result.relative_to(ROOT)}")


def command_legacy_mapping(args: argparse.Namespace) -> None:
    prepare_legacy_mapping(replace=args.replace)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)

    imp = sub.add_parser("import-source", help="Normalize the official workbook to per-food JSON")
    imp.add_argument("--version", default="4.0")
    imp.add_argument("--replace", action="store_true", help="Replace generated records for this version only")
    imp.set_defaults(func=command_import)

    val = sub.add_parser("validate-source", help="Compare normalized records field by field with the workbook")
    val.add_argument("--version", default="4.0")
    val.set_defaults(func=command_validate)

    source_db = sub.add_parser("build-source-db", help="Build the normalized, lossless source SQLite database")
    source_db.add_argument("--version", default="4.0")
    source_db.add_argument("--force", action="store_true", help="Replace this generated output database")
    source_db.set_defaults(func=command_source_db)

    app = sub.add_parser("build-app", help="Build the Train Libre-compatible asset from source plus curation")
    app.add_argument("--version", default="4.0")
    app.add_argument("--output", default="dist/train_libre_base_foods.db")
    app.add_argument("--preview", action="store_true", help="Allow missing translations/categories for local inspection")
    app.add_argument("--force", action="store_true", help="Replace this generated output database")
    app.set_defaults(func=command_app)

    legacy_mapping = sub.add_parser(
        "prepare-legacy-mapping",
        help="Create a reviewable migration draft with exact-name BLS candidates",
    )
    legacy_mapping.add_argument("--replace", action="store_true", help="Regenerate the draft")
    legacy_mapping.set_defaults(func=command_legacy_mapping)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        args.func(args)
        return 0
    except CatalogError as exc:
        print(f"catalog error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
