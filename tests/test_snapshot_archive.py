import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import add_category, publish_snapshot


ROOT = Path(__file__).parents[1]


class SnapshotArchiveTests(unittest.TestCase):
    def read_json(self, root: str, relative: str):
        return json.loads((ROOT / root / relative).read_text(encoding="utf-8"))

    def test_dist_and_docs_are_identical(self):
        for relative in (
            "index.html",
            "data/categories.json",
            "data/manifest.json",
            "data/latest.json",
            "data/status.json",
            "data/categories/2368383011/manifest.json",
            "data/categories/2368383011/status.json",
        ):
            self.assertEqual(
                (ROOT / "dist" / relative).read_bytes(),
                (ROOT / "docs" / relative).read_bytes(),
                relative,
            )

    def test_manifest_points_to_complete_snapshots(self):
        manifest = self.read_json("docs", "data/manifest.json")
        self.assertEqual(manifest["latest"], manifest["snapshots"][0]["date"])
        self.assertGreaterEqual(len(manifest["snapshots"]), 2)
        for entry in manifest["snapshots"]:
            snapshot = self.read_json("docs", entry["file"])
            items = snapshot["items"]
            self.assertTrue(snapshot["quality"]["publishable"])
            self.assertEqual([row["rank"] for row in items], list(range(1, 101)))
            self.assertEqual(len({row["asin"] for row in items}), 100)
            self.assertTrue(all(row.get("title") and row.get("image") for row in items))

    def test_daily_archives_are_immutable_mirrors(self):
        manifest = self.read_json("docs", "data/manifest.json")
        for entry in manifest["snapshots"]:
            self.assertEqual(
                (ROOT / "dist" / entry["file"]).read_bytes(),
                (ROOT / "docs" / entry["file"]).read_bytes(),
                entry["date"],
            )

    def test_existing_daily_archive_cannot_be_overwritten(self):
        source = ROOT / "dist" / "data" / "latest.json"
        date = self.read_json("dist", "data/latest.json")["snapshotDate"]
        with tempfile.TemporaryDirectory() as temp:
            roots = (Path(temp) / "dist", Path(temp) / "docs")
            sentinels = []
            for root in roots:
                destination = publish_snapshot.archive_path(root, date)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("do not replace", encoding="utf-8")
                sentinels.append(destination)
            with patch.object(publish_snapshot, "PUBLIC_ROOTS", roots):
                with self.assertRaises(SystemExit):
                    publish_snapshot.publish(source, date, f"{date}T09:00:00+08:00", "test")
            self.assertTrue(all(path.read_text(encoding="utf-8") == "do not replace" for path in sentinels))

    def test_unsafe_urls_fail_validation(self):
        items = self.read_json("dist", "data/latest.json")["items"]
        items[0]["url"] = "javascript:alert(1)"
        items[1]["image"] = "http://example.com/image.jpg"
        quality = publish_snapshot.validate(items)
        self.assertFalse(quality["publishable"])
        self.assertEqual(quality["invalidUrls"], {"product": 1, "image": 1})

    def test_category_archives_are_isolated(self):
        root = Path("public")
        default = publish_snapshot.archive_path(root, "2026-09-12")
        button_down = publish_snapshot.archive_path(root, "2026-09-12", "2368383011")
        self.assertEqual(default.as_posix(), "public/data/daily/2026/09/2026-09-12.json")
        self.assertEqual(
            button_down.as_posix(),
            "public/data/categories/2368383011/daily/2026/09/2026-09-12.json",
        )

    def test_category_registry_contains_requested_node(self):
        registry = self.read_json("docs", "data/categories.json")
        nodes = {item["node"] for item in registry["categories"]}
        self.assertIn("2368365011", nodes)
        self.assertIn("2368383011", nodes)

    def test_manual_category_addition_initializes_both_public_roots(self):
        with tempfile.TemporaryDirectory() as temp:
            roots = (Path(temp) / "dist", Path(temp) / "docs")
            seed = {"schemaVersion": 1, "default": "2368365011", "categories": []}
            for root in roots:
                path = root / "data" / "categories.json"
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(seed), encoding="utf-8")
            with patch.object(add_category, "PUBLIC_ROOTS", roots):
                added = add_category.add_category("1234567890", "Example Category")
            self.assertEqual(added["node"], "1234567890")
            for root in roots:
                registry = json.loads((root / "data" / "categories.json").read_text(encoding="utf-8"))
                self.assertEqual(registry["categories"][0]["name"], "Example Category")
                manifest = json.loads(
                    (root / "data" / "categories" / "1234567890" / "manifest.json").read_text(encoding="utf-8")
                )
                self.assertEqual(manifest["snapshots"], [])


if __name__ == "__main__":
    unittest.main()
