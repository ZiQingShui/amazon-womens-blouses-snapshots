from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from merge_uploaded_ecomtool import valid_price  # noqa: E402


class PriceValidityTests(unittest.TestCase):
    def test_zero_and_unavailable_values_are_not_prices(self):
        for raw in ("0", "0.00", "$0", "不可售", "nan", "", None):
            with self.subTest(raw=raw):
                self.assertIsNone(valid_price(raw))

    def test_positive_prices_remain_available(self):
        self.assertEqual(valid_price("15.08"), "15.08")
        self.assertEqual(valid_price("$24.99"), "24.99")

    def test_repaired_snapshots_have_no_zero_display_prices(self):
        paths = [
            ROOT / "docs/data/daily/2026/09/2026-09-15.json",
            ROOT / "docs/data/daily/2026/09/2026-09-16.json",
            ROOT / "docs/data/categories/2368383011/daily/2026/09/2026-09-16.json",
        ]
        for path in paths:
            with self.subTest(path=path):
                items = json.loads(path.read_text(encoding="utf-8"))["items"]
                self.assertEqual(len(items), 100)
                self.assertFalse(any(item["price"] == "$0" for item in items))
        current = json.loads(paths[1].read_text(encoding="utf-8"))
        self.assertEqual(current["items"][1]["price"], "未显示/无法获取")
        self.assertEqual(current["items"][78]["price"], "$15.08")


if __name__ == "__main__":
    unittest.main()
