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
            "data/category-tree.json",
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

    def test_sites_worker_embeds_current_snapshot_manifest(self):
        worker = (ROOT / "dist" / "server" / "index.js").read_text(encoding="utf-8")
        first_line = worker.splitlines()[1]
        prefix = "const STATIC_ASSETS = "
        self.assertTrue(first_line.startswith(prefix))
        assets = json.loads(first_line[len(prefix):-1])
        embedded_manifest = json.loads(assets["/data/manifest.json"]["body"])
        embedded_latest = json.loads(assets["/data/latest.json"]["body"])
        self.assertEqual(assets["/index.html"]["body"], (ROOT / "docs" / "index.html").read_text(encoding="utf-8"))
        self.assertEqual(embedded_manifest, self.read_json("dist", "data/manifest.json"))
        self.assertEqual(embedded_latest, self.read_json("dist", "data/latest.json"))
        self.assertNotIn("/amazon_womens_blouses_new_releases_data.js", assets)

    def test_upload_controls_and_worker_route_exist(self):
        html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
        worker = (ROOT / "dist" / "server" / "index.js").read_text(encoding="utf-8")
        self.assertIn('id="openRankingUpload"', html)
        self.assertIn('id="rankingUploadFile"', html)
        self.assertIn('fetch("/api/ranking-uploads"', html)
        self.assertIn('if (url.pathname === "/api/ranking-uploads")', worker)

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

    def test_custom_select_hides_native_control_in_header_and_filters(self):
        html = (ROOT / "dist" / "index.html").read_text(encoding="utf-8")
        self.assertIn("select.enhanced-native{", html)
        self.assertNotIn(".field select.enhanced-native{", html)
        self.assertIn(".snapshot-picker:focus-within{z-index:60}", html)
        self.assertIn('weekday=["周日","周一","周二","周三","周四","周五","周六"]', html)
        self.assertIn("snapshotDateLabel(entry.date,entry.capturedAt)", html)
        self.assertIn("snapshotDateLabel(x.date,x.capturedAt)", html)

    def test_open_dashboard_refreshes_new_snapshots_without_manual_reload(self):
        html = (ROOT / "dist" / "index.html").read_text(encoding="utf-8")
        self.assertIn("async function categoryManifest(category,force=false)", html)
        self.assertIn("async function refreshLatestSnapshot()", html)
        self.assertIn('window.addEventListener("focus",refreshLatestSnapshot)', html)
        self.assertIn('setInterval(refreshLatestSnapshot,60000)', html)

    def test_data_status_is_integrated_into_sidebar_brand(self):
        html = (ROOT / "dist" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="brandDataStatus"', html)
        self.assertNotIn('class="workbench-status"', html)

    def test_sidebar_contains_persistent_category_form(self):
        html = (ROOT / "dist" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="addCategoryButton"', html)
        self.assertIn('id="categoryNodeInput"', html)
        self.assertIn('fetch("/api/categories"', html)
        self.assertIn("＋ 新增类目", html)
        self.assertIn("类目节点", html)
        self.assertIn("添加到看板", html)
        self.assertNotIn("＋ Add Category", html)

    def test_custom_categories_can_be_removed_without_deleting_history(self):
        html = (ROOT / "dist" / "index.html").read_text(encoding="utf-8")
        worker = (ROOT / "tools" / "build_worker.py").read_text(encoding="utf-8")
        self.assertIn('data-delete-category=', html)
        self.assertIn('method:"DELETE"', html)
        self.assertIn("已经保存的历史快照不会被删除", html)
        self.assertIn("async function deleteCategory", worker)
        self.assertIn("系统内置类目不能删除", worker)
        self.assertIn("DELETE FROM categories WHERE node = ?", worker)
        self.assertIn("deletable: true", worker)

    def test_manual_capture_button_queues_and_tracks_requests(self):
        html = (ROOT / "dist" / "index.html").read_text(encoding="utf-8")
        worker = (ROOT / "tools" / "build_worker.py").read_text(encoding="utf-8")
        migration = (ROOT / "drizzle" / "0003_manual_capture_requests.sql").read_text(encoding="utf-8")
        self.assertIn('id="manualCapture"', html)
        self.assertIn("立即抓取", html)
        self.assertIn('fetch("/api/capture-requests"', html)
        self.assertIn("async function pollCaptureRequest()", html)
        self.assertIn('url.pathname === "/api/capture-requests"', worker)
        self.assertIn("crypto.randomUUID()", worker)
        self.assertIn("CREATE TABLE capture_requests", migration)
        self.assertIn("idx_capture_requests_daily_category", migration)
        self.assertIn('json("/api/capture-worker")', html)
        self.assertIn('captureWorkerOnline', html)
        self.assertIn('workerAuthorized(request, env)', worker)
        self.assertIn('url.pathname === "/api/capture-worker"', worker)
        self.assertTrue((ROOT / "tools" / "manual_capture_worker.py").exists())
        self.assertTrue((ROOT / "tools" / "manual_capture_task.md").exists())

    def test_manual_capture_can_refresh_only_current_day(self):
        publisher = (ROOT / "tools" / "publish_snapshot.py").read_text(encoding="utf-8")
        worker = (ROOT / "tools" / "build_worker.py").read_text(encoding="utf-8")
        self.assertIn("--replace-current-day", publisher)
        self.assertIn("只能用于当天快照", publisher)
        self.assertIn("['failed', 'completed'].includes(existing.status)", worker)

    def test_full_category_browser_and_ranking_switch_are_present(self):
        html = (ROOT / "dist" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="categoryBrowserDialog"', html)
        self.assertIn('id="categoryColumns"', html)
        self.assertIn('data-ranking="new-releases"', html)
        self.assertIn('data-ranking="best-sellers"', html)
        self.assertIn('currentRanking==="best-sellers"?"bestsellers":"new-releases"', html)
        self.assertIn('json("/api/category-tree?all=1")', html)
        self.assertIn("async function loadCategoryChildren(entry)", html)
        self.assertIn("点击类目可继续展开下级节点", html)
        self.assertIn('该类目尚未配置热销榜采集', html)
        self.assertIn('requestedRankingParam', html)
        self.assertNotIn('id="categoryBrowserButton"', html)
        self.assertIn('id="openManualCategory"', html)

    def test_dashboard_recomputes_truthful_coverage_for_old_snapshots(self):
        html = (ROOT / "dist" / "index.html").read_text(encoding="utf-8")
        self.assertIn("function actualCoverage(items)", html)
        self.assertIn("促销 ${coverage.promotion||0}/100", html)

    def test_rank_trend_does_not_connect_across_missing_snapshots(self):
        html = (ROOT / "dist" / "index.html").read_text(encoding="utf-8")
        self.assertIn("segments.filter(part=>part.length>1)", html)
        self.assertIn('p.sourceIndex>0?"重新入榜":"首次记录"', html)

    def test_mobile_cards_use_deferred_rendering_without_hiding_top_100(self):
        html = (ROOT / "dist" / "index.html").read_text(encoding="utf-8")
        self.assertIn("content-visibility:auto", html)
        self.assertIn('rows.map(p=>card(p,cmp.oldMap,compare))', html)

    def test_seed_categories_include_hierarchical_paths(self):
        registry = self.read_json("docs", "data/categories.json")
        paths = {item["node"]: item.get("path", []) for item in registry["categories"]}
        self.assertEqual(len(paths["2368365011"]), 5)
        self.assertEqual(len(paths["2368383011"]), 6)
        self.assertEqual(paths["370783011"], ["Amazon Devices & Accessories", "Amazon Device Accessories"])

    def test_archived_images_may_use_the_project_github_pages_host(self):
        self.assertTrue(
            publish_snapshot.is_allowed_url(
                "https://ziqingshui.github.io/amazon-womens-blouses-snapshots/data/images/2026-09-14/2368365011/B000000001.png",
                "image",
            )
        )

    def test_official_amazon_category_catalog_contains_real_hierarchy(self):
        catalog = self.read_json("docs", "data/category-tree.json")
        self.assertEqual(catalog["source"], "https://www.amazon.com/gp/new-releases")
        nodes = {str(item.get("node")): item for item in catalog["entries"] if item.get("node")}
        self.assertEqual(nodes["2368343011"]["name"], "Tops, Tees & Blouses")
        self.assertEqual(nodes["2368365011"]["path"][-1], "Blouses & Button-Down Shirts")
        self.assertEqual(nodes["2368383011"]["path"][-2:], ["Blouses & Button-Down Shirts", "Button-Down Shirts"])
        self.assertEqual(nodes["2619526011"]["name"], "Appliances")
        self.assertEqual(nodes["7141124011"]["name"], "Clothing, Shoes & Jewelry")
        self.assertEqual(nodes["2102313011"]["path"], ["Amazon Devices & Accessories", "Amazon Devices"])

    def test_worker_can_load_uncached_category_children(self):
        worker = (ROOT / "tools" / "build_worker.py").read_text(encoding="utf-8")
        self.assertIn("async function remoteBrowseChildren", worker)
        self.assertIn("browseNodeLookup/${parentNode}.html", worker)
        self.assertIn('source: "live"', worker)

    def test_category_columns_scroll_independently(self):
        html = (ROOT / "dist" / "index.html").read_text(encoding="utf-8")
        self.assertIn(".category-columns{display:flex;height:clamp(300px,52vh,450px);overflow-x:auto;overflow-y:hidden", html)
        self.assertIn(".category-column{box-sizing:border-box;width:245px;min-width:245px;height:100%", html)
        self.assertIn("overflow-x:hidden;overflow-y:auto;overscroll-behavior:contain;scrollbar-gutter:stable", html)
        self.assertIn(".category-column-title{position:sticky;top:0", html)

    def test_category_addition_does_not_require_admin_login(self):
        html = (ROOT / "dist" / "index.html").read_text(encoding="utf-8")
        worker = (ROOT / "tools" / "build_worker.py").read_text(encoding="utf-8")
        self.assertNotIn("管理员登录", html)
        self.assertNotIn("需要管理员使用 ChatGPT 登录", worker)
        self.assertNotIn("function isOwner", worker)

    def test_category_tree_schema_supports_both_rankings(self):
        migration = (ROOT / "drizzle" / "0001_category_tree.sql").read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE category_nodes", migration)
        self.assertIn("parent_node TEXT", migration)
        self.assertIn("supports_new_releases", migration)
        self.assertIn("supports_best_sellers", migration)

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

    def test_placeholder_values_do_not_inflate_field_coverage(self):
        self.assertFalse(publish_snapshot.has_coverage_value({"listingDate": "未显示/无法获取"}, "listingDate"))
        self.assertFalse(publish_snapshot.has_coverage_value({"promotionStatus": "unknown"}, "promotion"))
        self.assertFalse(publish_snapshot.has_coverage_value({"mainBsr": None}, "mainBsr"))
        self.assertTrue(publish_snapshot.has_coverage_value({"listingDate": "2026-09-14"}, "listingDate"))
        self.assertTrue(publish_snapshot.has_coverage_value({"promotionStatus": "none"}, "promotion"))
        self.assertTrue(publish_snapshot.has_coverage_value({"mainBsr": 1}, "mainBsr"))

    def test_structurally_complete_snapshot_requires_real_detail_collection(self):
        items = self.read_json("dist", "data/latest.json")["items"]
        unchecked = [dict(item) for item in items]
        unchecked[0].pop("detailStatus", None)
        unchecked[0].pop("detailAttempts", None)
        quality = publish_snapshot.validate(unchecked, "Ecomtool MCP + Amazon 商品详情页")
        self.assertFalse(quality["publishable"])
        self.assertEqual(quality["detailChecked"], 99)

    def test_non_mcp_detail_source_cannot_be_published(self):
        items = self.read_json("dist", "data/latest.json")["items"]
        quality = publish_snapshot.validate(items, "SellerSprite browser export; Ecomtool MCP unavailable")
        self.assertFalse(quality["publishable"])
        self.assertFalse(quality["detailSourceValid"])

    def test_valid_button_down_snapshot_is_indexed_without_invalid_date(self):
        manifest = self.read_json("docs", "data/categories/2368383011/manifest.json")
        status = self.read_json("docs", "data/categories/2368383011/status.json")
        snapshot = self.read_json("docs", "data/categories/2368383011/daily/2026/09/2026-09-16.json")
        self.assertEqual(manifest["latest"], "2026-09-16")
        self.assertIn("2026-09-16", [entry["date"] for entry in manifest["snapshots"]])
        self.assertNotIn("2026-09-15", [entry["date"] for entry in manifest["snapshots"]])
        self.assertEqual(status["status"], "ok")
        self.assertEqual(len(snapshot["items"]), 100)

    def test_history_streak_breaks_when_a_calendar_day_is_missing(self):
        item = {"asin": "B000000001"}
        earlier = [
            {"snapshotDate": "2026-09-10", "items": [item]},
            {"snapshotDate": "2026-09-12", "items": [item]},
        ]
        current = [{"asin": "B000000001"}]
        publish_snapshot.add_history(current, earlier, "2026-09-13")
        self.assertEqual(current[0]["history"]["appearances"], 3)
        self.assertEqual(current[0]["history"]["streak"], 2)

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
