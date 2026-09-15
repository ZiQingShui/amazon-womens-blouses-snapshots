import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import add_category, build_worker, publish_snapshot


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
        self.assertEqual(embedded_manifest, self.read_json("dist", "data/manifest.json"))
        self.assertEqual(embedded_latest, self.read_json("dist", "data/latest.json"))
        self.assertNotIn("/amazon_womens_blouses_new_releases_data.js", assets)

    def test_manifest_points_to_complete_snapshots(self):
        manifest = self.read_json("docs", "data/manifest.json")
        self.assertEqual(manifest["latest"], manifest["snapshots"][0]["date"])
        self.assertGreaterEqual(len(manifest["snapshots"]), 2)
        for entry in manifest["snapshots"]:
            snapshot = self.read_json("docs", entry["file"])
            items = snapshot["items"]
            # 硬性完整性：与采集质量无关，任何一期都必须满足。
            self.assertEqual([row["rank"] for row in items], list(range(1, 101)))
            self.assertEqual(len({row["asin"] for row in items}), 100)
            self.assertTrue(all(row.get("title") and row.get("image") for row in items))
            # manifest 不得与快照内写死的 quality 脱节。
            self.assertEqual(entry["publishable"], snapshot["quality"]["publishable"], entry["date"])

    def test_archived_quality_matches_recomputed_quality(self):
        """存档里的 quality 必须能被当前 validate() 复现。

        2026-09-13 那期曾长期标注 8 个字段全 100%，而 mainBsr 实际只有
        74/100：校验口径在发布当天被收紧，但没人回算历史存档。
        """
        for entry in self.read_json("docs", "data/manifest.json")["snapshots"]:
            snapshot = self.read_json("docs", entry["file"])
            fresh = publish_snapshot.validate(snapshot["items"])
            self.assertEqual(snapshot["quality"], fresh, entry["date"])
            self.assertEqual(entry["fieldCoverage"], fresh["fieldCoverage"], entry["date"])
            self.assertEqual(entry["provenance"], fresh["provenance"], entry["date"])
            self.assertEqual(entry["coverageFailures"], fresh["coverageFailures"], entry["date"])

    def test_manifest_never_trusts_the_quality_stored_in_archives(self):
        """rebuild_manifest 必须从 items 现算，而不是照抄存档里的 quality。"""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            forged = {
                "snapshotDate": "2026-01-01",
                "capturedAt": "2026-01-01T08:30:00+08:00",
                "items": [],
                "quality": {"count": 100, "publishable": True, "fieldCoverage": {"brand": 100}},
            }
            target = publish_snapshot.archive_path(root, "2026-01-01")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(forged), encoding="utf-8")
            rebuilt = publish_snapshot.build_manifest(root, publish_snapshot.DEFAULT_NODE)
        self.assertEqual(rebuilt["snapshots"][0]["count"], 0)
        self.assertFalse(rebuilt["snapshots"][0]["publishable"])
        self.assertEqual(rebuilt["snapshots"][0]["fieldCoverage"]["brand"], 0)

    def test_degraded_snapshots_fail_the_coverage_gate(self):
        manifest = self.read_json("docs", "data/manifest.json")
        degraded = [entry for entry in manifest["snapshots"] if entry["provenance"]["degraded"]]
        self.assertTrue(degraded, "至少应有一期带降级标记，用于验证门禁生效")
        for entry in degraded:
            quality = publish_snapshot.validate(self.read_json("docs", entry["file"])["items"])
            self.assertFalse(quality["publishable"], entry["date"])
            self.assertTrue(quality["coverageFailures"], entry["date"])

    def test_healthy_snapshots_pass_the_coverage_gate(self):
        manifest = self.read_json("docs", "data/manifest.json")
        healthy = [entry for entry in manifest["snapshots"] if not entry["provenance"]["degraded"]]
        self.assertTrue(healthy)
        for entry in healthy:
            quality = publish_snapshot.validate(self.read_json("docs", entry["file"])["items"])
            self.assertTrue(quality["publishable"], entry["date"])
            self.assertEqual(quality["coverageFailures"], {}, entry["date"])

    def test_provenance_separates_fallback_from_reuse(self):
        items = [
            {"asin": "B1", "detailStatus": "fallback_after_mcp_failure", "detailSource": "Ecomtool browser extension"},
            {"asin": "B2", "detailSource": "近三日历史快照（仅复用标题、品牌、上架日）"},
            {"asin": "B3", "reusedFields": ["title"], "detailSource": "Amazon product detail page"},
            {"asin": "B4", "detailSource": "Amazon product detail page"},
        ]
        result = publish_snapshot.provenance(items)
        self.assertEqual(result["fallbackRows"], 1)
        self.assertEqual(result["reusedRows"], 2)
        self.assertTrue(result["degraded"])
        self.assertEqual(result["sources"]["Amazon product detail page"], 2)

    def test_worker_bundle_is_built_from_current_docs(self):
        """提交的 Worker bundle 必须与当前 docs/ 一致，且不含二进制资源。

        docs 下的商品图曾让 assets() 抛 UnicodeDecodeError，而
        publish_snapshot 末尾会重建 bundle，于是整条发布链路在写盘后崩掉。
        """
        bundle = build_worker.assets()
        self.assertTrue(bundle)
        self.assertFalse(
            [route for route in bundle if route.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))],
            "二进制资源不应进入 Worker bundle",
        )
        worker = (ROOT / "dist" / "server" / "index.js").read_text(encoding="utf-8")
        embedded = json.loads(worker.split("const STATIC_ASSETS = ", 1)[1].split("\n", 1)[0].rstrip(";"))
        self.assertEqual(embedded, bundle, "dist/server/index.js 尚未按最新 docs/ 重建")

    def test_worker_validates_category_nodes_with_the_same_rule_as_cli(self):
        worker = (ROOT / "tools" / "build_worker.py").read_text(encoding="utf-8")
        self.assertIn("!/^\\d{6,14}$/.test(node)", worker)
        self.assertNotIn("!/^\\d{1,14}$/.test(node)", worker)

    def test_unparsable_input_is_recorded_as_a_failure(self):
        """脏输入要写进 status.json 并走失败路径，而不是抛裸异常。"""
        with tempfile.TemporaryDirectory() as temp:
            roots = (Path(temp) / "dist", Path(temp) / "docs")
            with patch.object(publish_snapshot, "PUBLIC_ROOTS", roots):
                with self.assertRaises(SystemExit):
                    publish_snapshot.publish(
                        Path("does-not-exist.json"), "2026-09-20", "2026-09-20T08:30:00+08:00", "test"
                    )
            for root in roots:
                status = json.loads((root / "data" / "status.json").read_text(encoding="utf-8"))
                self.assertEqual(status["status"], "failed")
                self.assertIn("输入数据无法规整", status["reason"])

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
        self.assertTrue(registry["categories"])
        for item in registry["categories"]:
            # 每个类目都要带层级路径与 Amazon 类目标识，否则看板会把类目树压平。
            self.assertTrue(item.get("path"), item["node"])
            self.assertTrue(item.get("departmentSlug"), item["node"])

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
        """占位文字（「未显示/无法获取」）不能算作有效字段。

        样本固定用 2026-09-13 那一期：它含 16 条 listingDate 占位、24 条
        promotion 未知，覆盖率必须如实反映，而不是像旧版那样记成 100。
        这里刻意不读 latest.json —— latest 会随新快照滚动，旧的断言会因此失效。
        """
        snapshot = self.read_json("docs", "data/daily/2026/09/2026-09-13.json")
        quality = publish_snapshot.validate(snapshot["items"])
        self.assertEqual(quality["fieldCoverage"]["listingDate"], 84)
        self.assertEqual(quality["fieldCoverage"]["promotion"], 76)
        self.assertEqual(quality["fieldCoverage"]["mainBsr"], 74)

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
                added = add_category.add_category(
                    "1234567890", "Example Category", None, ["Women", "Tops, Tees & Blouses", "Example"], "fashion"
                )
            self.assertEqual(added["node"], "1234567890")
            # CLI 必须写出与看板 POST /api/categories 相同的字段集，
            # 否则类目树会缺层级，仓库自带的 path 断言也会失败。
            self.assertEqual(added["path"], ["Women", "Tops, Tees & Blouses", "Example"])
            self.assertEqual(added["departmentSlug"], "fashion")
            self.assertIn("fashion/1234567890", added["url"])
            for root in roots:
                registry = json.loads((root / "data" / "categories.json").read_text(encoding="utf-8"))
                self.assertEqual(registry["categories"][0]["name"], "Example Category")
                self.assertTrue(registry["categories"][0]["path"])
                manifest = json.loads(
                    (root / "data" / "categories" / "1234567890" / "manifest.json").read_text(encoding="utf-8")
                )
                self.assertEqual(manifest["snapshots"], [])


if __name__ == "__main__":
    unittest.main()
