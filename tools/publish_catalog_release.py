#!/usr/bin/env python3
"""Build, package, and publish a BLS food-catalog release to this repository."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG_REPOSITORY = "rfivesix/train-libre-bls-catalog"
DEFAULT_TAG = "bls-foods-stable"


class PublishError(Exception):
    """Expected release publication failure."""


def run(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )
    except FileNotFoundError as exc:
        raise PublishError(f"Required command is not installed: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        details = (exc.stderr or exc.stdout or "").strip()
        raise PublishError(
            f"Command failed ({' '.join(command)}): {details or exc.returncode}"
        ) from exc


def prepare(version: str, catalog_version: str, channel: str) -> Path:
    python = sys.executable
    print(f"Validating BLS {catalog_version} source data...", flush=True)
    run(
        [python, "tools/catalog.py", "validate-source", "--version", catalog_version],
        capture=True,
    )
    print("Building strict Train Libre food catalog...", flush=True)
    run([
        python,
        "tools/catalog.py",
        "build-app",
        "--version",
        catalog_version,
        "--force",
    ])
    print("Packaging compressed release and manifest...", flush=True)
    run([
        python,
        "tools/package_catalog_release.py",
        "--version",
        version,
        "--catalog-version",
        catalog_version,
        "--channel",
        channel,
    ])
    return ROOT / "dist" / "release"


def repository_details(repository: str) -> tuple[str, str]:
    result = run(
        [
            "gh",
            "repo",
            "view",
            repository,
            "--json",
            "visibility,defaultBranchRef",
            "--jq",
            "[.visibility, .defaultBranchRef.name] | @tsv",
        ],
        capture=True,
    )
    fields = result.stdout.strip().split("\t")
    if len(fields) != 2 or fields[0] != "PUBLIC" or not fields[1]:
        raise PublishError(
            f"Release target {repository} must be public and have a default branch. "
            "Make the catalog repository public, then rerun the command."
        )
    return fields[0], fields[1]


def existing_release(repository: str, tag: str) -> dict[str, object] | None:
    result = subprocess.run(
        [
            "gh",
            "release",
            "view",
            tag,
            "--repo",
            repository,
            "--json",
            "isDraft,isImmutable",
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        if "release not found" in result.stderr.lower():
            return None
        raise PublishError(f"Cannot inspect release {tag}: {result.stderr.strip()}")
    return json.loads(result.stdout)


def publish(
    *,
    output_dir: Path,
    repository: str,
    tag: str,
    version: str,
    catalog_version: str,
    dry_run: bool,
) -> None:
    assets = {
        "database": output_dir / "train_libre_base_foods.db.gz",
        "notice": output_dir / "THIRD_PARTY_NOTICES.md",
        "manifest": output_dir / "catalog_manifest.json",
    }
    missing = [str(path) for path in assets.values() if not path.is_file()]
    if missing:
        raise PublishError(f"Release package is incomplete: {', '.join(missing)}")

    _, default_branch = repository_details(repository)
    release = existing_release(repository, tag)
    if release and release.get("isDraft"):
        raise PublishError(f"Refusing to replace draft release {tag}; publish or delete it first")
    if release and release.get("isImmutable"):
        raise PublishError(
            f"Release {tag} is immutable. Use a mutable stable release channel or configure a new tag."
        )

    notes = (
        f"Train Libre BLS food catalog {version} (BLS {catalog_version}).\n\n"
        "The attached manifest contains download and database checksums, schema compatibility, "
        "record counts, and attribution. The database is distributed under CC BY 4.0; "
        "see THIRD_PARTY_NOTICES.md."
    )
    if dry_run:
        action = "update" if release else "create"
        print(f"Dry run: would {action} {repository} release {tag} from {default_branch}.")
        for key, path in assets.items():
            print(f"  {key}: {path} ({path.stat().st_size} bytes)")
        return

    if release is None:
        print(f"Creating and publishing {repository} release {tag}...", flush=True)
        run(
            [
                "gh",
                "release",
                "create",
                tag,
                str(assets["database"]),
                str(assets["notice"]),
                str(assets["manifest"]),
                "--repo",
                repository,
                "--target",
                default_branch,
                "--title",
                "Train Libre Food Catalog (stable)",
                "--notes",
                notes,
            ]
        )
        print(f"Created public release: https://github.com/{repository}/releases/tag/{tag}")
        return

    print(f"Updating and publishing {repository} release {tag}...", flush=True)
    # Publish the manifest last. Until it is replaced, clients will reject the
    # new database by checksum and retain their current valid catalog.
    for key in ("database", "notice", "manifest"):
        run(
            [
                "gh",
                "release",
                "upload",
                tag,
                str(assets[key]),
                "--repo",
                repository,
                "--clobber",
            ]
        )
    run(
        [
            "gh",
            "release",
            "edit",
            tag,
            "--repo",
            repository,
            "--title",
            "Train Libre Food Catalog (stable)",
            "--notes",
            notes,
        ]
    )
    print(f"Updated public release: https://github.com/{repository}/releases/tag/{tag}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="Release version, e.g. 4.0.0")
    parser.add_argument("--catalog-version", default="4.0")
    parser.add_argument("--channel", choices=("stable", "beta"), default="stable")
    parser.add_argument("--repository", default=CATALOG_REPOSITORY)
    parser.add_argument("--tag", default=DEFAULT_TAG)
    parser.add_argument("--dry-run", action="store_true", help="Build and validate without publishing")
    args = parser.parse_args()

    try:
        print("Checking GitHub CLI authentication...", flush=True)
        run(["gh", "auth", "status"], capture=True)
        output_dir = prepare(args.version, args.catalog_version, args.channel)
        publish(
            output_dir=output_dir,
            repository=args.repository,
            tag=args.tag,
            version=args.version,
            catalog_version=args.catalog_version,
            dry_run=args.dry_run,
        )
    except (OSError, PublishError, json.JSONDecodeError) as exc:
        print(f"release publication error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
