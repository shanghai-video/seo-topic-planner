"""Build the SEO Topic Planner release ZIP with generated artifacts excluded.

The archive ships only source, manifest, skills and UI files.  Caches, local
stores, secrets, prior archives and test output are rejected outright so a
release cannot silently ship them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FORBIDDEN_NAMES = {".DS_Store", ".coverage", "topic-planner-store.json", "dataforseo-oauth.json"}
FORBIDDEN_SUFFIXES = {".pyc", ".pyo", ".zip", ".db", ".sqlite", ".sqlite3", ".log"}
FORBIDDEN_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".venv", "dist", ".git"}


def collect_files(root: Path = ROOT) -> list[Path]:
    """Return shippable files relative to ``root`` or raise on a forbidden file."""
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in FORBIDDEN_DIRS for part in relative.parts):
            continue
        if path.is_dir():
            continue
        if path.name.startswith(".env"):
            raise RuntimeError(f"forbidden file in package: {relative.as_posix()}")
        if path.suffix.lower() in FORBIDDEN_SUFFIXES or path.name in FORBIDDEN_NAMES:
            raise RuntimeError(f"forbidden file in package: {relative.as_posix()}")
        # Repository metadata such as ``.gitignore`` or a ``.github`` workflow is
        # not part of the portable plugin package.  Secrets and caches were
        # already rejected (or skipped) above.
        if any(part.startswith(".") for part in relative.parts):
            continue
        files.append(relative)
    return files


def build(output_dir: Path | None = None) -> dict[str, object]:
    version = str(json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))["version"])
    files = collect_files()
    destination = Path(output_dir) if output_dir is not None else ROOT / "dist"
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / f"seo-topic-planner-{version}.zip"
    manifest = {
        "name": ROOT.name,
        "version": version,
        "files": [item.as_posix() for item in files],
        "credentials_included": False,
        "customer_data_included": False,
    }
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for relative in files:
            bundle.write(ROOT / relative, arcname=(Path(ROOT.name) / relative).as_posix())
        bundle.writestr(
            (Path(ROOT.name) / "RELEASE-MANIFEST.json").as_posix(),
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    return {"archive": str(archive), "sha256": digest, "file_count": len(files) + 1, "version": version}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=None)
    arguments = parser.parse_args()
    print(json.dumps(build(arguments.output_dir), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
