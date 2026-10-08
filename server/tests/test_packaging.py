"""Contract tests for the release packaging boundary (offline, no artifacts)."""

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN_ROOT))

from build_package import build, collect_files  # noqa: E402


class CollectFilesTest(unittest.TestCase):
    def test_cache_and_secret_directories_are_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "plugin.json").write_text("{}", encoding="utf-8")
            cache = root / "__pycache__"
            cache.mkdir()
            (cache / "module.cpython-313.pyc").write_text("x", encoding="utf-8")
            collected = [item.as_posix() for item in collect_files(root)]
            self.assertEqual(collected, ["plugin.json"])
            self.assertFalse(any("pycache" in item for item in collected))

    def test_loose_forbidden_files_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "stray.pyc").write_text("x", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                collect_files(root)

    def test_local_secret_files_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("SECRET=1", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                collect_files(root)

    def test_local_store_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "dataforseo-oauth.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                collect_files(root)


class BuildTest(unittest.TestCase):
    def test_build_archive_contains_manifest_and_no_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = build(Path(tmp))
            archive = Path(result["archive"])
            self.assertTrue(archive.is_file())
            self.assertEqual(
                result["version"],
                json.loads((PLUGIN_ROOT / "plugin.json").read_text(encoding="utf-8"))["version"],
            )
            with zipfile.ZipFile(archive) as bundle:
                names = bundle.namelist()
                self.assertIn("seo-topic-planner/RELEASE-MANIFEST.json", names)
                for name in names:
                    self.assertNotIn("__pycache__", name)
                    self.assertNotIn(".DS_Store", name)
                    self.assertFalse(name.endswith((".pyc", ".pyo")))
                    self.assertFalse(name.startswith(".env"))
                manifest = json.loads(bundle.read("seo-topic-planner/RELEASE-MANIFEST.json"))
                self.assertFalse(manifest["credentials_included"])
                self.assertFalse(manifest["customer_data_included"])


if __name__ == "__main__":
    unittest.main()
