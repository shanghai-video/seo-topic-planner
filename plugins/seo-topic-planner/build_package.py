"""Build the SEO Topic Planner release ZIP with generated artifacts excluded.

The archive ships only source, manifest and skills.  Caches, local
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
# Operating-system metadata is skipped rather than rejected: the collector must
# never ship it, but a stray ``.DS_Store`` is not a reason to fail a release the
# way a leaked credential is.
OS_METADATA_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}
# A release is built from an explicit allowlist: only these exact names, source
# extensions and UI assets may ship.  Anything that looks like a credential,
# customer export, database or raw response is rejected outright so it can never
# ride along even if it appears in an otherwise-shippable directory.
FORBIDDEN_NAMES = {".coverage", "topic-planner-store.json", "dataforseo-oauth.json"}
FORBIDDEN_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".zip",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".log",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".crt",
    ".csv",
    ".gz",
    ".env",
}
# SQLite sidecar journals carry uncommitted customer data and must never ship.
SQLITE_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
# Generated research output and raw provider responses are customer data, not
# source: they are excluded so a release never carries them.
FORBIDDEN_DIRS = {
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "dist",
    "docs",
    ".git",
    "exports",
    "raw",
}
# Exact root files that are part of the distributable.
ALLOWED_ROOT_NAMES = {
    "plugin.json",
    "mcp.json",
    "README.md",
    "build_package.py",
    "validate_package.py",
    "ruff.toml",
    "LICENSE",
}
# Source/config extensions that are always safe to ship.
ALLOWED_SUFFIXES = {".py", ".toml", ".md"}
# Explicit directory allowlist: a file ships only when its posix path sits under
# one of these prefixes with one of the listed suffixes.  An unknown ``.py``
# anywhere else (a scratch script, a test helper, a stray module) is NOT
# collected, so the release is a path whitelist rather than an extension rule.
ALLOWED_PREFIX_RULES = (
    ("server/seo_topic_planner/", {".py"}),
    ("skills/", {".md"}),
)
ALLOWED_EXACT_PATHS = {
    "server/pyproject.toml",
    "server/run.py",
}
# Specific bundled data files that ship with the plugin (the Public Suffix List
# is required for correct registrable-domain matching).
ALLOWED_DATA_PATHS = {
    "server/seo_topic_planner/data/public_suffix_list.dat",
}
# Only user-facing Markdown ships: README.md and everything under skills/ (each
# skill's SKILL.md plus the reference files it loads on demand).
SHIPPABLE_MARKDOWN_NAMES = {"README.md", "SKILL.md"}


def collect_files(root: Path = ROOT) -> list[Path]:
    """Return shippable files relative to ``root`` or raise on a forbidden file.

    A file ships only when it is on the explicit allowlist.  A file that matches
    a forbidden credential/data pattern is rejected (not silently skipped) so a
    release fails loudly instead of leaking it.
    """

    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in FORBIDDEN_DIRS for part in relative.parts):
            continue
        # A symlink could point at a secret outside the tree even when its name
        # looks shippable, so the collector never follows one.
        if path.is_symlink():
            raise RuntimeError(f"symlink is not allowed in the package: {relative.as_posix()}")
        if path.is_dir():
            continue
        if path.name.startswith(".env"):
            raise RuntimeError(f"forbidden file in package: {relative.as_posix()}")
        if path.name in OS_METADATA_NAMES:
            continue
        if path.name.endswith(SQLITE_SIDECAR_SUFFIXES):
            raise RuntimeError(f"forbidden file in package: {relative.as_posix()}")
        suffix = path.suffix.lower()
        if suffix in FORBIDDEN_SUFFIXES or path.name in FORBIDDEN_NAMES:
            raise RuntimeError(f"forbidden file in package: {relative.as_posix()}")
        # Repository metadata such as ``.gitignore`` or a ``.github`` workflow is
        # not part of the portable plugin package.
        if any(part.startswith(".") for part in relative.parts):
            continue
        # Explicit path allowlist.  A file is shipped only when its exact path or
        # one of its directory prefixes and suffixes is listed.  An unknown
        # source file is never collected by extension alone.
        posix = relative.as_posix()
        if posix in ALLOWED_DATA_PATHS or posix in ALLOWED_EXACT_PATHS:
            files.append(relative)
            continue
        if path.name in ALLOWED_ROOT_NAMES:
            files.append(relative)
            continue
        if posix.startswith("skills/") and suffix == ".md":
            files.append(relative)
            continue
        if any(
            posix.startswith(prefix) and suffix in suffixes
            for prefix, suffixes in ALLOWED_PREFIX_RULES
        ):
            files.append(relative)
            continue
        # Everything else (unknown source, unlisted docs, JSON, data) is not part
        # of the release.
        continue
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
