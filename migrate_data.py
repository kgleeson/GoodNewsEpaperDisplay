"""Migrate old epaper_goodnews data into the new format.

Usage:
    python migrate_data.py --source /path/to/old/data --target ./data

What this does:
  - Copies PNG images from <source>/images/ to <target>/images/
    (skips .bmp files — the new pipeline no longer uses them)
  - Transforms metadata JSON files:
      * removes the obsolete `image.display_path` field
      * adds `device_type: ""` if missing
  - Writes transformed metadata to <target>/metadata/
  - Updates <target>/current.png and <target>/current.json symlinks
    to point to the most recent successful run

Run with --dry-run to preview changes without writing anything.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
LOGGER = logging.getLogger(__name__)


def _transform_metadata(data: dict, images_source: Path, images_target: Path) -> dict:
    """Return a copy of data with old-format fields cleaned up."""
    data = dict(data)

    # Add device_type if missing (old runs predated this field)
    if "device_type" not in data:
        data["device_type"] = ""

    # Clean up image sub-object
    image = data.get("image")
    if isinstance(image, dict):
        image = dict(image)
        # Remove obsolete BMP path — new pipeline only tracks the PNG
        image.pop("display_path", None)

        # Rewrite image_path to point to target; validate PNG exists in source
        raw_path = image.get("image_path", "")
        if raw_path:
            filename = Path(raw_path).name
            if not filename.endswith(".png"):
                filename = Path(filename).with_suffix(".png").name
            if (images_source / filename).exists():
                image["image_path"] = (images_target / filename).as_posix()
            else:
                LOGGER.warning("  Image file not found in source: %s", filename)

        data["image"] = image

    return data


def _find_current(metadata_dir: Path) -> tuple[Optional[Path], Optional[Path]]:
    """Return (latest_metadata_path, latest_image_path) from metadata files, newest first."""
    files = sorted(metadata_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for meta_path in files:
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            if data.get("status") == "success":
                image = data.get("image") or {}
                image_path_str = image.get("image_path")
                if image_path_str:
                    image_path = Path(image_path_str)
                    if image_path.exists():
                        return meta_path, image_path
        except Exception:
            continue
    # Fall back to most recent file regardless of status
    if files:
        return files[0], None
    return None, None


def _symlink_or_copy(source: Path, target: Path) -> None:
    if target.exists() or target.is_symlink():
        target.unlink()
    try:
        os.symlink(source.resolve(), target)
    except OSError:
        shutil.copy2(source, target)


def migrate(source_dir: Path, target_dir: Path, *, dry_run: bool = False) -> None:
    source_images = source_dir / "images"
    source_metadata = source_dir / "metadata"

    target_images = target_dir / "images"
    target_metadata = target_dir / "metadata"

    if not source_images.exists():
        LOGGER.error("Source images directory not found: %s", source_images)
        sys.exit(1)
    if not source_metadata.exists():
        LOGGER.error("Source metadata directory not found: %s", source_metadata)
        sys.exit(1)

    if not dry_run:
        target_images.mkdir(parents=True, exist_ok=True)
        target_metadata.mkdir(parents=True, exist_ok=True)

    # --- Copy PNG images; convert BMP→PNG for runs that never had a PNG ---
    png_files = list(source_images.glob("*.png"))
    LOGGER.info("Found %d PNG images to migrate", len(png_files))
    copied_images = 0
    for png in png_files:
        dest = target_images / png.name
        if dest.exists():
            LOGGER.debug("  Skipping existing image: %s", png.name)
            continue
        LOGGER.info("  Copying image: %s", png.name)
        if not dry_run:
            shutil.copy2(png, dest)
        copied_images += 1

    bmp_count = len(list(source_images.glob("*.bmp")))
    if bmp_count:
        LOGGER.info("Skipping %d .bmp files (no longer used)", bmp_count)

    # --- Transform and copy metadata ---
    meta_files = list(source_metadata.glob("*.json"))
    LOGGER.info("Found %d metadata files to migrate", len(meta_files))
    migrated_meta = 0
    for meta_path in meta_files:
        dest = target_metadata / meta_path.name
        if dest.exists():
            LOGGER.debug("  Skipping existing metadata: %s", meta_path.name)
            continue
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as exc:
            LOGGER.warning("  Skipping unreadable metadata %s: %s", meta_path.name, exc)
            continue

        transformed = _transform_metadata(data, source_images, target_images)
        LOGGER.info("  Migrating metadata: %s", meta_path.name)
        if not dry_run:
            dest.write_text(json.dumps(transformed, indent=2), encoding="utf-8")
        migrated_meta += 1

    # --- Update current symlinks ---
    current_meta, current_image = _find_current(target_metadata if not dry_run else source_metadata)
    if current_meta and current_image and not dry_run:
        current_png_link = target_dir / "current.png"
        current_json_link = target_dir / "current.json"
        _symlink_or_copy(current_image, current_png_link)
        _symlink_or_copy(current_meta, current_json_link)
        LOGGER.info("Updated current symlinks → %s", current_meta.name)

    LOGGER.info(
        "Migration %s: %d images, %d metadata files processed",
        "dry-run complete" if dry_run else "complete",
        copied_images,
        migrated_meta,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True, type=Path, help="Old data directory (from the Pi)")
    parser.add_argument("--target", required=True, type=Path, help="New data directory (default: ./data)")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    args = parser.parse_args()

    migrate(args.source, args.target, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
