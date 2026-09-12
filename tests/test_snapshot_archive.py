import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import publish_snapshot


ROOT = Path(__file__).parents[1]


class SnapshotArchiveTests(unittest.TestCase):
    def read_json(self, root: str, relative: str):
        return json.loads((ROOT / root / relative).read_text(encoding="utf-8"))

    def test_dist_and_docs_are_identical(self):
        for relative in (
            "index.html",
            "data/manifest.json",
            "data/latest.json",
            "data/status.json",
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


if __name__ == "__main__":
    unittest.main()
