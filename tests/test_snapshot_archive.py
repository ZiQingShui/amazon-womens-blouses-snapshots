import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import add_category, build_enriched, build_worker, publish_snapshot


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

    def test_upload_controls_and_worker_route_removed(self):
        html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
        worker = (ROOT / "dist" / "server" / "index.js").read_text(encoding="utf-8")
        self.assertNotIn('id="openRankingUpload"', html)
        self.assertNotIn('id="rankingUploadFile"', html)
        self.assertNotIn('fetch("/api/ranking-uploads"', html)
        self.assertNotIn('if (url.pathname === "/api/ranking-uploads")', worker)

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

        detailSourceValid 是发布时 detail_source 参数的函数，而非 items
        可复现的字段——历史快照发布时旧代码未传 detail_source（存为 null），
        故对比时排除该字段，只校验 items 本身可复现的覆盖率口径。
        """
        for entry in self.read_json("docs", "data/manifest.json")["snapshots"]:
            snapshot = self.read_json("docs", entry["file"])
            detail_source = (snapshot.get("sources") or {}).get("productDetails")
            fresh = publish_snapshot.validate(snapshot["items"], detail_source)
            stored = {k: v for k, v in snapshot["quality"].items() if k != "detailSourceValid"}
            recomputed = {k: v for k, v in fresh.items() if k != "detailSourceValid"}
            self.assertEqual(stored, recomputed, entry["date"])
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
        if not degraded:
            # 2026-09-20 清理了 09-17 之前的快照，带降级标记的那几期随之删除；
            # 现在归档里可能一期都没有。门禁逻辑本身由上面的人造样本用例覆盖，
            # 这条只是拿真实数据复核，没有样本就该跳过而不是判失败。
            self.skipTest("当前归档中没有带降级标记的快照（09-17 之前的期已清理）")
        for entry in degraded:
            quality = publish_snapshot.validate(self.read_json("docs", entry["file"])["items"])
            self.assertFalse(quality["publishable"], entry["date"])
            self.assertTrue(quality["coverageFailures"], entry["date"])

    def test_healthy_snapshots_pass_the_coverage_gate(self):
        manifest = self.read_json("docs", "data/manifest.json")
        healthy = [entry for entry in manifest["snapshots"] if entry["publishable"]]
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

    def test_custom_select_hides_native_control_in_filters(self):
        html = (ROOT / "dist" / "index.html").read_text(encoding="utf-8")
        self.assertIn("select.enhanced-native{", html)
        self.assertNotIn(".field select.enhanced-native{", html)
        self.assertIn('weekday=["周日","周一","周二","周三","周四","周五","周六"]', html)
        self.assertIn("snapshotDateLabel(entry.date,entry.capturedAt)", html)
        self.assertIn("snapshotDateLabel(x.date,x.capturedAt)", html)

    def test_header_dates_use_two_handle_slider_not_dropdown(self):
        html = (ROOT / "dist" / "index.html").read_text(encoding="utf-8")
        # 快照日期 / 对比日期改用双端滑杆：一条轴管两个日期，拖把手切换。
        self.assertIn('id="dateSlider"', html)
        self.assertIn('id="dsTrack"', html)
        self.assertIn('id="dsHa"', html)
        self.assertIn('id="dsHb"', html)
        self.assertIn(".controls .ds-native{display:none}", html)
        self.assertIn("function renderDateSlider(){", html)
        self.assertIn("function commitDateSlider(which,slot){", html)
        self.assertIn("function initDateSlider(){", html)
        self.assertIn("function nearestDateHandle(clientX,track){", html)
        # 原生 select 仍作为状态载体保留（供 change 事件与 URL 状态复用），但不再渲染旧下拉。
        self.assertIn('id="dateSelect" class="ds-native"', html)
        self.assertIn('id="compareDateSelect" class="ds-native"', html)
        self.assertNotIn("enhanceSelect(dateSelect", html)
        self.assertNotIn("snapshot-picker", html)

    def test_comparison_can_be_turned_off_from_the_slider(self):
        html = (ROOT / "dist" / "index.html").read_text(encoding="utf-8")
        # 旧下拉里「不对比」是第一项；换成滑杆后必须仍然够得到，否则无法只看某一期自身。
        self.assertIn('id="dsCmpToggle"', html)
        self.assertIn(".date-slider .ds-cmptog{", html)
        self.assertIn('event.key==="Delete"||event.key==="Backspace"', html)
        # 最旧一期没有更早的基准 → 开启按钮要禁用
        self.assertIn("cmpTog.disabled=iSnap<=0", html)
        # 关闭后文案走「不对比」分支且变灰
        self.assertIn('$("dsCmpText").classList.toggle("off",cmpOff)', html)
        # 手动关闭后要能连续看多期自身（切日期不自动恢复），换类目才重置
        self.assertIn("let compareOff=false,pinnedCompare=null;", html)
        self.assertIn("cache.clear();compareOff=false;pinnedCompare=null;", html)

    def test_hand_picked_baseline_survives_snapshot_drag(self):
        html = (ROOT / "dist" / "index.html").read_text(encoding="utf-8")
        # 手动选过对比基准后，拖快照不能把它重置成「快照减一天」——只要基准仍早于快照就保留。
        self.assertIn("pinnedCompare=cmpDate;", html)
        self.assertIn(
            "const keepCompare=pinnedCompare&&earlier.some(entry=>entry.date===pinnedCompare)?pinnedCompare:selectedDate;",
            html)
        self.assertIn('select.value=!compareOff&&earlier.some(entry=>entry.date===keepCompare)?keepCompare:""', html)
        # 关闭对比 / 换类目时必须忘记这个基准
        self.assertIn("if(k===0){compareOff=true;pinnedCompare=null;", html)

    def test_slider_renders_a_window_not_the_whole_history(self):
        html = (ROOT / "dist" / "index.html").read_text(encoding="utf-8")
        # 轨道只有 424px：期数超过约 19 期两个把手就会重叠，所以只渲染最近 N 期。
        self.assertIn("const SLIDER_WINDOW=14,SLIDER_PAGE=7;", html)
        self.assertIn("function sliderWindow(){", html)
        self.assertIn("win:ordered.slice(from,end+1)", html)
        self.assertIn("function pageSlider(dir){", html)
        self.assertIn('$("dsWinPrev").addEventListener("click",()=>pageSlider(-1))', html)
        self.assertIn('$("dsWinNext").addEventListener("click",()=>pageSlider(1))', html)
        self.assertIn('id="dsWinRange"', html)
        self.assertIn("sliderWinEndDate=null;", html)
        # 刻度只在「月份首日」补月份做锚点：窗口第一格不该补（否则 09-10 会显示成 9/10，与相邻的 11/12 重复）
        self.assertIn("cross=dd===1", html)
        self.assertNotIn("cross=i===0||!prev||prev.slice(5,7)!==x.date.slice(5,7)", html)
        # 窗口必须始终包含快照；对比在同窗口内才渲染把手
        self.assertIn("if(focus<end-W+1||focus>end)end=focus;", html)
        self.assertIn("if(kCmp<0){hb.hidden=true}", html)

    def test_no_compare_is_a_draggable_slot_on_the_rail(self):
        html = (ROOT / "dist" / "index.html").read_text(encoding="utf-8")
        # 「不对比」是轨道上第 0 格（快照占 1..n 格），所以把手永远可见、拖过去关、拖回来开。
        self.assertIn(">不对比</span>", html)
        self.assertIn("slotPct=k=>", html)
        self.assertIn("Math.round(Math.max(0,Math.min(1,ratio))*W)", html)
        self.assertIn("if(k===0){compareOff=true;", html)
        self.assertIn("const k=Math.max(1,Math.min(W,slot))", html)
        self.assertIn(".date-slider .ds-h.off{", html)
        # 刻度改为绝对定位以精确对齐格子
        self.assertIn(".date-slider .ds-ticks{position:relative;", html)
        # 把手不再被隐藏（否则关掉后无法拖回）
        self.assertIn("hb.hidden=false;", html)

    def test_open_dashboard_refreshes_new_snapshots_without_manual_reload(self):
        html = (ROOT / "dist" / "index.html").read_text(encoding="utf-8")
        self.assertIn("async function categoryManifest(category,force=false)", html)
        self.assertIn("async function refreshLatestSnapshot()", html)
        self.assertIn('window.addEventListener("focus",refreshLatestSnapshot)', html)
        self.assertIn('setInterval(refreshLatestSnapshot,60000)', html)

    def test_data_status_is_integrated_into_sidebar_brand(self):
        html = (ROOT / "dist" / "index.html").read_text(encoding="utf-8")
        # 侧栏品牌区（BSR Radar/数据状态）已按需求移除；数据状态由空状态视图与 quality 区承载。
        self.assertNotIn('id="brandDataStatus"', html)
        self.assertNotIn("workbench-brand", html)
        self.assertNotIn("workbench-logo", html)
        self.assertNotIn('class="workbench-status"', html)

    def test_sidebar_has_no_write_category_entries(self):
        html = (ROOT / "dist" / "index.html").read_text(encoding="utf-8")
        # 看板是纯静态只读视图：新增/删除类目改由 add_category.py 或改数据文件完成，
        # 界面不再保留任何依赖后端 /api 的写入入口。
        self.assertNotIn('id="addCategoryButton"', html)
        self.assertNotIn('id="openManualCategory"', html)
        self.assertNotIn('id="categoryBrowserDialog"', html)
        self.assertNotIn('id="categoryDialog"', html)
        self.assertNotIn('data-delete-category=', html)
        self.assertNotIn("＋ 新增类目", html)
        self.assertNotIn('fetch("/api/', html)
        self.assertNotIn("removeCategory", html)
        self.assertNotIn("loadCategoryCatalog", html)

    def test_manual_capture_button_queues_and_tracks_requests(self):
        html = (ROOT / "dist" / "index.html").read_text(encoding="utf-8")
        worker = (ROOT / "tools" / "build_worker.py").read_text(encoding="utf-8")
        migration = (ROOT / "drizzle" / "0003_manual_capture_requests.sql").read_text(encoding="utf-8")
        # 前端「立即抓取」按钮及采集轮询逻辑已移除。
        self.assertNotIn('id="manualCapture"', html)
        self.assertNotIn("立即抓取", html)
        self.assertNotIn('fetch("/api/capture-requests"', html)
        self.assertNotIn("async function pollCaptureRequest()", html)
        self.assertNotIn('json("/api/capture-worker")', html)
        # 服务端采集接口、数据库表与本机处理器保留。
        self.assertIn('url.pathname === "/api/capture-requests"', worker)
        self.assertIn("crypto.randomUUID()", worker)
        self.assertIn("CREATE TABLE capture_requests", migration)
        self.assertIn("idx_capture_requests_daily_category", migration)
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

    def test_active_filter_state_bar_sits_above_the_controls(self):
        html = (ROOT / "dist" / "index.html").read_text(encoding="utf-8")
        # 已选条件栏从面板底部移到顶部，带「已选」标签与「全部清空」。
        self.assertIn('id="filterState"', html)
        self.assertIn('id="clearAllFilters"', html)
        self.assertIn(".filter-state{", html)
        self.assertIn(".filter-state[hidden]{display:none}", html)
        self.assertIn('class="fs-label">已选<', html)
        self.assertIn('$("filterState").hidden=!active.length', html)
        self.assertIn('$("clearAllFilters").addEventListener("click",resetAllFilters)', html)
        self.assertIn("function resetAllFilters(){", html)
        # 标题行那个「清空条件」与「全部清空」功能重复，已删除，只保留后者。
        self.assertNotIn("resetFilters", html)
        self.assertNotIn("清空条件", html)
        self.assertNotIn(".reset{", html)
        # 位置：在筛选控件之前（面板顶部），而不是旧的面板末尾
        self.assertLess(html.index('id="filterState"'), html.index('class="fgroup"'))
        self.assertNotIn('<div class="active-filters" id="activeFilters" aria-live="polite"></div></section>', html)
        # 2026-09-20：条件全部平铺，不再有「更多筛选」折叠按钮与隐藏面板
        # 2026-09-21：款式标签（袖型/季节/主风格）合并进正式版 → 3 组变 4 组
        self.assertNotIn("moreFilters", html)
        self.assertNotIn("advancedFilters", html)
        self.assertNotIn("advanced-grid", html)
        self.assertEqual(html.count('class="fgroup"'), 4)

    def test_style_tag_capability_is_wired_into_dashboard(self):
        """款式标签已合并进正式版：标签库、卡片 chip、三个下拉、就地编辑、自定义项。"""
        html = (ROOT / "docs/index.html").read_text(encoding="utf-8")
        self.assertIn('json("data/style-tags.json")', html)       # 加载标签库
        self.assertIn('style-chips', html)                         # 卡片上的标签行（模板里带变量后缀）
        for sel in ("filterSleeve", "filterSeason", "filterFabric", "filterPattern", "filterStyle", "filterImageType"):
            self.assertIn('id="%s"' % sel, html)                   # 第 4 组的六个下拉
        self.assertIn("sc-pattern", html)                          # 卡片上的图案 chip（2026-09-22 加）
        self.assertIn('["pattern", "图案", false]', html)           # 弹窗第 4 行：图案（单选）
        self.assertIn("openStyleEditor", html)                     # 点标签就地编辑
        self.assertIn("styleTagsCustomOptions", html)              # 自定义选项
        self.assertIn("editOptions", html)                         # 只列 建议+自定义
        self.assertIn("styleOk=!!(styleTag&&styleTag.confirmed)", html)  # 筛选只算已确认
        self.assertIn("const productHref = safeProductUrl(p.url)", html)  # 弹窗商品页链接
        self.assertEqual(html.count('class="sty-open"'), 1)         # 只有标题末尾一个入口
        self.assertIn("打开商品页</a>", html)                       # 标题本身不做链接
        self.assertIn(".sty-head > a{flex:0 0 340px;width:340px;height:440px", html)  # 图片框写死 340×440
        self.assertIn("height:min(88vh,720px)", html)               # 弹窗尺寸固定（不随图片比例变）

    def test_comparison_falls_back_to_asin_when_previous_lacks_parent_asin(self):
        """对比期缺父体数据时必须回退到 ASIN 比对。

        2026-09-18 现象：09-10~09-16 七期快照的 parentAsin 全为空（0/100），而看板默认
        「同款合并」按 parentAsin 匹配 → 对比这些日期时 100 件全被判成「新进」，脉搏没有升降。
        修法：pk() 改由 parentKeyActive 驱动，只有两期父体覆盖率都够时才用父体键。
        """
        html = (ROOT / "dist" / "index.html").read_text(encoding="utf-8")
        self.assertIn("function hasParentData(items){", html)
        self.assertIn("function refreshParentKey(){", html)
        # 覆盖率阈值 80%
        self.assertIn("items.filter(x=>x.parentAsin).length>=items.length*0.8", html)
        # pk 由 parentKeyActive 决定，且 comparison 复用 pk（不再自己写一份 key 逻辑）
        self.assertIn("function pk(x){return parentKeyActive?(x.parentAsin||x.asin):x.asin}", html)
        self.assertIn("key=pk,", html)
        self.assertNotIn("key=parentView?x=>(x.parentAsin||x.asin):x=>x.asin", html)
        # 四个加载/切换时机都要重算
        self.assertGreaterEqual(html.count("refreshParentKey()"), 4)
        self.assertIn("parentKeyActive=false;", html)
        # 图例要有降级提示
        self.assertIn('class="lg-note"', html)
        self.assertIn("按子ASIN对比", html)

    def test_start_server_bat_is_windows_encoded(self):
        """start-server.bat 必须能被 cmd 正确解析（换行符 + 编码 + UNC 支持）。

        2026-09-18 真实事故：该文件是 LF 换行 + UTF-8 无 BOM，在共享机上双击后
        cmd 把命令行切碎，报出一堆 'cho' / 'ined' / '-m' 不是内部或外部命令，
        中文也全是乱码。原因是 cmd 需要 CRLF 换行、并按系统 ANSI（中文是 GBK）读文件。
        用 Write/编辑器重写这个文件时极易再犯，所以在此设卡。
        """
        raw = (ROOT / "start-server.bat").read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "bat 不应有 UTF-8 BOM")
        lf, crlf = raw.count(b"\n"), raw.count(b"\r\n")
        self.assertEqual(lf, crlf, "bat 必须全部用 CRLF 换行（实测 CRLF %d / LF %d）" % (crlf, lf))
        text = raw.decode("gbk")  # 解不出来就说明不是 GBK，中文 Windows 上会乱码
        self.assertIn('pushd "%~dp0"', text)  # pushd 才能处理 UNC 网络路径
        self.assertIn("--directory docs", text)
        # 注释里可以提到 cd /d（解释为什么不这么写），但可执行行里不能出现——
        # cd 的 /d 开关不支持 UNC，会让工作目录落到 C://WINDOWS。
        code = "\n".join(l for l in text.splitlines() if not l.strip().upper().startswith("REM"))
        self.assertNotIn("cd /d", code)

    def test_dropdown_chevron_is_svg_and_vertically_centred(self):
        html = (ROOT / "dist" / "index.html").read_text(encoding="utf-8")
        # 箭头必须是内联 SVG：原来的字体字符 ⌄（U+2304）在 Inter 下回退成类似小写 v 的形状，形状不受控。
        self.assertNotIn('smart-chevron">⌄', html)
        self.assertIn('class="smart-chevron" aria-hidden="true"><svg viewBox="0 0 16 16"', html)
        # 必须绝对定位垂直居中：.field.compact .smart-trigger 的 padding-top:12px 会把 flex 子项一起推下 6px。
        self.assertIn(".smart-chevron{position:absolute;right:8px;top:50%;transform:translateY(-50%)", html)
        self.assertIn(".smart-trigger{position:relative;", html)
        self.assertIn("padding:0 40px 0 11px", html)
        # 展开态旋转要带上 translateY，否则旋转时丢掉垂直居中。
        self.assertIn("transform:translateY(-50%) rotate(180deg)", html)
        # hover 规则必须排除展开态，否则 hover 的优先级会盖掉展开高亮。
        self.assertIn(':hover:not(:disabled):not([aria-expanded="true"]) .smart-chevron', html)

    def test_date_slider_head_wraps_instead_of_overflowing(self):
        html = (ROOT / "dist" / "index.html").read_text(encoding="utf-8")
        # 头部内容实测 327px，而滑杆在 ≤1440px 只有 304~364px：必须有折行兜底，否则溢出容器（700px 时撑出横向滚动）。
        self.assertIn("flex-wrap:wrap;font-size:11.5px", html)
        self.assertIn("@media(max-width:1180px){.date-slider{width:min(340px,44vw);min-width:240px}.date-slider .ds-head .dash{display:none}}", html)

    def test_ranking_switch_is_present_in_sidebar_category_menu(self):
        html = (ROOT / "dist" / "index.html").read_text(encoding="utf-8")
        # 榜单切换已并入侧栏类目菜单：类目下挂新品榜/热销榜子项，顶部切换按钮已移除。
        self.assertIn("category-ranking-item", html)
        self.assertIn('data-ranking="${rk}"', html)
        self.assertIn('"new-releases","新品榜"', html)
        self.assertIn('"best-sellers","热销榜"', html)
        self.assertNotIn('id="rankingPicker"', html)
        self.assertNotIn('class="ranking-picker"', html)
        self.assertIn('currentRanking==="best-sellers"?"bestsellers":"new-releases"', html)
        worker_source = (ROOT / "tools" / "build_worker.py").read_text(encoding="utf-8")
        self.assertIn('该类目尚未配置热销榜采集', worker_source)
        self.assertIn('requestedRankingParam', html)
        # 远端类目树浏览器（依赖 /api/category-tree）已随写入入口一并移除。
        self.assertNotIn('id="categoryBrowserDialog"', html)
        self.assertNotIn('id="categoryColumns"', html)
        self.assertNotIn('id="categoryBrowserButton"', html)
        self.assertNotIn('id="openManualCategory"', html)
        self.assertNotIn('/api/category-tree', html)
        self.assertNotIn("async function loadCategoryChildren(entry)", html)
        self.assertNotIn("点击类目可继续展开下级节点", html)

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
            self.assertTrue(item.get("path"), item["node"])
            self.assertTrue(item.get("departmentSlug"), item["node"])
        paths = {item["node"]: item.get("path", []) for item in registry["categories"]}
        self.assertEqual(len(paths["2368365011"]), 5)
        self.assertEqual(len(paths["2368383011"]), 6)
        self.assertNotIn("370783011", paths)

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
        # latest 应等于 snapshots 里的最新日期（随新快照滚动，不写死具体日期）
        latest_date = manifest["latest"]
        self.assertIn(latest_date, [entry["date"] for entry in manifest["snapshots"]])
        snapshot = self.read_json("docs", "data/categories/2368383011/daily/2026/09/2026-09-17.json")
        self.assertIn("2026-09-17", [entry["date"] for entry in manifest["snapshots"]])
        self.assertNotIn("2026-09-15", [entry["date"] for entry in manifest["snapshots"]])
        self.assertEqual(status["status"], "ok")
        self.assertEqual(len(snapshot["items"]), 100)

    def test_archived_placeholder_coverage_is_truthful(self):
        """占位文字（「未显示/无法获取」）不能算作有效字段。

        样本固定用某一期归档。这里刻意不读 latest.json —— latest 会随新快照
        滚动，旧的断言会因此失效。

        覆盖率一律**按占位条数动态推导**，不写死数字。两个原因：
        1. 补录会把占位换成真实值（09-20 那次补录让 listingDate 由 84 变 97）；
        2. 2026-09-20 清理了 09-17 之前的快照，旧样本期 2026-09-13 已不存在，
           早期那批含 promotion / mainBsr 占位的数据也随之消失 —— 现在剩余各期
           这两个字段都是满覆盖，写死的旧值（76 / 74）不再成立。

        核心要守住的是「占位不计入有效」，不是任何具体数字。
        """
        items = self.read_json("docs", "data/daily/2026/09/2026-09-19.json")["items"]
        quality = publish_snapshot.validate(items)

        def gaps(field):
            return sum(
                1 for x in items
                if x.get(field) in ("未显示/无法获取", "", None)
            )

        for field in ("listingDate", "promotion", "mainBsr"):
            expected = len(items) - gaps(field)
            self.assertEqual(
                quality["fieldCoverage"][field], expected,
                "%s 覆盖率应如实扣除占位（占位 %d 条）" % (field, gaps(field)),
            )

        # 样本期本身要含占位，否则上面那段恒等式-trivial（全 100 也会通过）
        # 就失去了验证意义。随着数据补录推进，这里可能需要换一期样本。
        self.assertGreater(gaps("listingDate"), 0, "样本期应含 listingDate 占位")

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


class PromotionParsingTests(unittest.TestCase):
    """促销字段的取值优先级：ASIN 监控数据 > 商品详情。

    背景：`amazon_get_product_info` 的 `Coupon` 列自 2026-09-18 起服务端恒返回 0
    （原始 HTML 里那一列就是 0，同表「促销折扣」列却正常），改用
    `tools/fetch_asin_tracking.py` 抓的 ASIN 监控数据（Coupon / Promotion折扣 / 是否Deal）供数。
    监控侧 coupon 是**百分比**（"10%"），详情侧是**美元**（"2.50"），两套写法不能混。
    """

    def _item(self, detail, tracking):
        row = {"asin": "B0TEST0001", "rank": 7, "imageId": "", "title": "t", "reviews": 3, "rating": 4.5}
        return build_enriched.build_item(row, detail, "2026-09-20", tracking)

    def test_tracking_wins_over_detail(self):
        item = self._item({"Coupon": "0", "促销折扣": "0"},
                          {"coupon": "10%", "promoDiscount": "30%", "deal": True})
        self.assertEqual(item["promotions"], ["Coupon 10%", "30% off", "Deal"])
        self.assertEqual(item["promotionStatus"], "detected")
        self.assertEqual(item["promotionSource"], "Ecomtool MCP ASIN 监控")
        self.assertTrue(item["deal"])

    def test_coupon_keeps_its_own_unit(self):
        self.assertEqual(self._item({}, {"coupon": "8%"})["promotions"], ["Coupon 8%"])
        self.assertEqual(self._item({"Coupon": "2.50"}, None)["promotions"], ["Coupon $2.50"])

    def test_deal_alone_counts_as_promotion(self):
        item = self._item({}, {"coupon": "0", "promoDiscount": "0", "deal": True})
        self.assertEqual(item["promotions"], ["Deal"])
        self.assertEqual(item["promotionStatus"], "detected")

    def test_falls_back_to_detail_without_tracking(self):
        item = self._item({"Coupon": "0", "促销折扣": "20%"}, None)
        self.assertEqual(item["promotions"], ["20% off"])
        self.assertNotIn("deal", item)
        self.assertIn("商品详情", item["promotionSource"])

    def test_no_promotion_is_none_not_unknown(self):
        item = self._item({}, {"coupon": "0", "promoDiscount": "0", "deal": False})
        self.assertEqual(item["promotion"], "暂无促销")
        self.assertEqual(item["promotionStatus"], "none")


    def test_lowest_price_badge_becomes_its_own_type(self):
        """「最低价标识」= Lowest price in 30 days 要成为独立类型（详情独有的字段）。"""
        item = self._item({"最低价标识": "Lowest price in 30 days"}, {"coupon": "0", "deal": False})
        self.assertEqual(item["promotions"], ["30天最低价"])
        self.assertEqual(item["promoTypes"], ["lowest30"])

    def test_detail_deal_used_when_tracking_says_none(self):
        """监控侧说没 Deal、详情侧「是否活动」= Deal 时，取并集（两边抓取时点不同）。"""
        item = self._item({"是否活动": "Deal"}, {"coupon": "0", "deal": False})
        self.assertEqual(item["promotions"], ["Deal"])
        self.assertEqual(item["promoTypes"], ["deal"])

    def test_promo_types_cover_all_four_and_keep_order(self):
        """四种类型都要能识别，顺序与标签一致、且不重复。"""
        item = self._item({"最低价标识": "Lowest price in 30 days", "是否活动": "Deal"},
                          {"coupon": "10%", "promoDiscount": "20%", "deal": True})
        self.assertEqual(item["promotions"], ["Coupon 10%", "20% off", "Deal", "30天最低价"])
        self.assertEqual(item["promoTypes"], ["coupon", "discount", "deal", "lowest30"])
        self.assertEqual(len(item["promoTypes"]), len(set(item["promoTypes"])))


    def test_card_image_keeps_framing_but_raises_resolution(self):
        """卡片商品图：按屏幕密度给档位，但**构图必须与旧的 230 方形缩略图一致**。

        2026-09-22 用户报「弹窗的图明显比卡片清晰」。根因：卡片用的是 safeImageUrl 的 230×230，
        卡片显示 221px、2x 屏要 442 物理像素 → 放大 1.9 倍。
        修法是把原参数**整组等比放大一倍**（UL300/SR300,200/SR230,230 → UL600/SR600,400/SR460,460），
        实测两张图内容占画布比都是 0.33、内容框 114×153 → 228×307，构图逐像素一致。
        ⚠ 别改成 _AC_SR600,600_（贴边裁切，内容占 74%）或 _AC_SL1000_（竖版图，会把图框从
        221×221 撑成 221×278、卡片变高）。
        """
        html = (ROOT / "docs/index.html").read_text(encoding="utf-8")
        self.assertIn("function cardImageAttrs(value)", html)
        self.assertIn('_AC_UL600_SR600,400__SR460,460_', html)     # 2x 档（等比放大）
        self.assertIn("' 1x, '+esc(base)+' 2x\"'", html)           # srcset 两档：1x 用 230、2x 用 460
        # 卡片 img 用的是新函数，且带上异步解码
        self.assertIn('decoding="async" ${cardImageAttrs(p.image)}', html)
        # 小图（快速上升/退出列表）仍走 230 档，别一起放大
        self.assertGreaterEqual(html.count("safeImageUrl(p.image)"), 2)


    def test_card_asin_follows_parent_child_view(self):
        """卡片底部的 ASIN 要跟父/子维度切换（2026-09-23 用户要求）。

        父ASIN维度 → 显示 pk(p)（= parentAsin||asin，父视图去重键）；
        子ASIN维度 → 显示子 ASIN。复用对比逻辑里现成的 pk()，不要另写一套。
        「商品详情」链接保持指向子 ASIN（父体页会跳默认变体，链接不改）。
        """
        html = (ROOT / "docs/index.html").read_text(encoding="utf-8")
        self.assertIn('<span class="asin" title="${parentKeyActive?', html)
        self.assertIn('${parentKeyActive?"父 ":"子 "}${esc(pk(p))}</span>', html)   # 前缀 + pk()
        self.assertIn('${parentKeyActive?"父 ":"子 "}${esc(pk(hero))}</span>', html)  # 历史弹窗头部同款
        # 写死 p.asin 的旧写法不能回来
        self.assertNotIn('<span class="asin">${esc(p.asin)}</span>', html)

    def test_compare_slider_can_reenable_after_turning_off(self):
        """日期滑杆：对比关掉之后必须能重新开回来（2026-09-23 用户报的 bug）。

        症状：点 ✕ 关掉对比后，再点 ＋ 没反应、拖轨道也没反应 —— **两条恢复路径全死**。
        根因有两处：
        1. ＋ 按钮的槽位 **off-by-one**：`commitDateSlider("cmp", iSnap-from+1)` 算出来是
           **快照自己**的位置，而 commit 里「不能和自己比」（cmpIdx>=snapIdx 直接 return）
           会把它静默吞掉。正确的是 `iSnap-from`（= 上一期）。
        2. 对比关闭时对比手柄（#dsHb）是 hidden 的，而 pointerdown 里有
           `if(which==="cmp"&&$("dsHb").hidden)return;` → 整条轨道的左侧也点不了。
           （nearestDateHandle 在 iCmp=-1 时把左端当 "cmp"，本来是合理的手势，被这个守卫挡死了。）
        修法：＋ 按钮改为直接算「上一期」= ordered[iSnap-1]，必要时平移窗口；删掉那条拖拽守卫
        （commit 自己会拒绝非法位置，不需要提前拦）。
        ⚠ `dateSliderOrder()` 是 **旧→新**（manifest.snapshots 反转），所以「上一期」= iSnap-1，别写反。
        """
        html = (ROOT / "docs/index.html").read_text(encoding="utf-8")
        # ＋ 按钮：直接取「上一期」ordered[iSnap-1]，且上一期不在窗口里时要先平移窗口
        self.assertIn("const prevIdx=w.iSnap-1", html)
        self.assertIn("if(prevIdx<w.from||prevIdx>w.end)", html)
        # 旧的 off-by-one 写法不能回来（那是本 bug 的根因）
        self.assertNotIn('commitDateSlider("cmp",$("compareDateSelect").value?0:(w.iSnap-w.from+1))', html)
        # 拖拽守卫不能回来（它把对比关闭后的整条轨道锁死了）
        self.assertNotIn('if(which==="cmp"&&$("dsHb").hidden)return;', html)

    def test_history_dialog_separates_fixed_header_from_scroll(self):
        """单品历史弹窗：固定头部与滚动内容之间要有**通栏**隔断。

        2026-09-22 用户反馈「这个地方应该要做一个隔断，不然这样太生硬了」——
        头部（`.product-overview`，flex:0 0 auto）与滚动区（`.history-scroll`，flex:1）原本 gap=0、
        没有任何分隔，内容滚上来会直接贴住统计卡。
        演进（别再回退）：
        1) 「1px 线 + 16px 白渐隐」→ 用户说「不是很明显」
        2) 4 个方案（底色分区 / 投影 / 灰腰带 / 强渐隐）→ 用户选**投影隔断**
        3) 投影挂在 `.product-overview` 上时左右各留 24px（外层 `#productHistoryContent` 有 24px padding），
           两端断掉，用户说「这两边有点突兀」→ 改挂 `::after`，用 `left/right:-24px` 抵消外层 padding 顶到弹窗边缘
        ⚠ `z-index:1` 不能省（否则被后面的滚动区盖住）；负 spread 要小（-6；给 -20 等于没有）
        """
        html = (ROOT / "docs/index.html").read_text(encoding="utf-8")
        self.assertIn("padding-bottom:18px;position:relative;z-index:1;background:#fff", html)
        self.assertIn(".product-overview::after", html)
        self.assertIn("left:-24px;right:-24px;bottom:0;height:1px", html)                    # 通栏：抵消外层 24px padding
        self.assertIn("box-shadow:0 11px 18px -6px rgba(16,32,59,.22)", html)                # 投影
        # 不能退回"只给头部加 border/box-shadow"的写法（左右会断头）
        self.assertNotIn("padding-bottom:18px;border-bottom:1px solid var(--line)", html)
        # 滚动区仍是独立滚动、且不吃掉滚轮
        self.assertIn(".history-scroll{flex:1;min-height:0;overflow-y:auto;overscroll-behavior:contain", html)
        # 标题上方的品牌行已按用户要求删除（2026-09-22："这里的品牌去掉"），标题与图片顶部对齐
        self.assertNotIn("history-kicker", html)
        self.assertIn(".history-title{margin:0 0 8px;font-size:22px", html)


    def test_untagged_flag_and_tag_state_filter_are_wired(self):
        """「待打标」提示与筛选项（2026-09-22 用户："没有打过标签的，能做提示吗…筛选那边还是要有可以筛选的，待打标"）。

        - 卡片：`styleTodo=!(styleTag&&styleTag.confirmed)`，未确认时在照片右上角挂 `.todo-flag`「待打标」
          （那个角落本来是空的：左上名次、右下价格）。保存后随 render 自然消失。
        - 判定口径是**未确认**，不是"完全没标签"——只有机器建议的也算未打标。
        - 兜底：连机器建议都没有的商品，标签行**照样渲染**，里面放 `.sty-add`「＋ 打标」，
          否则那张卡没有任何入口能打开打标弹窗（原来 `styleChips = styleTag ? … : ""`）。
        - 筛选：第 3 组「价格与状态」加 `filterTagState`（全部/待打标/已打标），该组因此从 `.fg-4` 改 `.fg-5`。
        """
        html = (ROOT / "docs/index.html").read_text(encoding="utf-8")
        self.assertIn('id="filterTagState"', html)
        self.assertIn('<option value="todo">待打标</option>', html)
        self.assertIn('tagState:""', html)                                   # filters 对象
        self.assertIn('filterTagState:"打标状态"', html)                      # enhanceSelect 的 labels
        self.assertIn('filterTagState:"tagState"', html)                     # optionMeta / bindings
        self.assertIn('tagState:"filterTagState"', html)                     # filterLabel
        self.assertIn('active.tagState==="todo"&&styleOk)return false', html)
        self.assertIn("styleTodo=!(styleTag&&styleTag.confirmed)", html)
        self.assertIn("todo-flag", html)
        self.assertIn("sty-add", html)                                       # 无标签时的兜底入口
        # 角标与 hover 的「点击查看历史」同占右上角 → hover 时角标必须让位（否则叠字）
        self.assertIn(".photo:hover .todo-flag,.photo:focus-visible .todo-flag{opacity:0", html)
        # 第 3 组改 5 列（4 列会把这 5 个控件挤成两行）
        self.assertIn('class="filter-grid fg-5"><label class="field price-field"', html)


    def test_keyword_board_is_wired_and_data_published(self):
        """关键词搜索排名板块（2026-09-22 新增，与榜单视图并列的第二个主视图）。

        数据链路：config/keywords-core.json（**人工指定的词表**，2026-09-23 起）
        → tools/fetch_keyword_search.py（Ecomtool 抓取）→ tools/publish_keyword_search.py
        → docs/data/keyword-search/{date}.json + manifest.json。
        ⚠ 记录口径是**第一页全部位置**（`pageTop`，60~63 个），每条用 `isAd` 标明是广告位还是自然位、
        用 `organicRank` 给自然位编号 —— Ecomtool 的「页面排名」是广告与自然位混排的。
        2026-09-23 用户反馈「才 11 个位置，需要抓第一页的所有」之前，只记了「页面前 10 + 自然位前 5」，
        而首屏广告就占 1~6 位，等于几乎看不到自然位。
        ⚠ 视图切换靠 `body[data-view="keyword"]`，榜单那些区块的代码没动；
        侧栏入口监听必须用**捕获阶段**，否则已有的平滑滚动处理器会在目标还 display:none 时算位置。
        """
        html = (ROOT / "docs/index.html").read_text(encoding="utf-8")
        self.assertIn('id="keywordBoard"', html)
        self.assertIn('id="navKeyword"', html)
        self.assertIn('id="navRanking"', html)                       # 不能给 href="#"（querySelector 会抛错）
        self.assertIn("function renderKeywordDetail", html)
        self.assertIn("function setMainView", html)
        self.assertIn("keyword-search/manifest.json", html)
        self.assertIn('"data/keyword-search/" + date + ".json"', html)
        self.assertIn('body:not([data-view="keyword"]) .keyword-board{display:none!important}', html)
        self.assertIn("setMainView(\"keyword\")},true)", html)       # 捕获阶段切换视图（见上面说明）
        # 抓取脚本：记录**第一页全部位置**（2026-09-23 用户："才 11 个位置，需要抓第一页的所有"）
        fetch = (ROOT / "tools/fetch_keyword_search.py").read_text(encoding="utf-8")
        self.assertIn('"pageTop": items', fetch)                      # 第一页全部，不截断
        self.assertNotIn('PAGE_TOP_N', fetch)                         # 不再只取前 10
        self.assertIn('MAX_PAGE = 1', fetch)                          # 但只抓第一页
        self.assertIn('x["organicRank"] = i', fetch)                  # 自然位另编名次（广告位没有）
        self.assertIn('"isAd": "广告" in str(r.get("是否广告", ""))', fetch)
        self.assertIn('r"^B[0-9A-Z]{9}$"', fetch)                     # 跳过重复表头行
        # 词表：2026-09-23 起改成**人工指定的 config/keywords-core.json**（14 词），
        # ⚠ 必须在 config/ 而不是 work/ —— push_to_github.py 的 SKIP_DIRS 含 work，否则清单不进版本库
        cfg = ROOT / "config/keywords-core.json"
        self.assertTrue(cfg.exists(), "人工词表 config/keywords-core.json 不存在")
        wanted = [k["keyword"] for k in json.loads(cfg.read_text(encoding="utf-8"))["keywords"]]
        self.assertEqual(len(wanted), 14, "人工词表应是 14 个词")
        self.assertIn("button down shirts for women", wanted)
        self.assertIn("women's blouses & button-down shirts", wanted)   # 含撇号与 & 的词要能存进 JSON
        self.assertIn('ROOT / "config" / "keywords-core.json"', fetch)  # 采集默认读这份
        # ⚠ 别再按 xlsx 的「相关性」列筛词：那 3 行是**真实高搜索量关键词**，
        # 老规则会把 12~13 万搜索量的核心词整条丢掉（2026-09-23 用户给的清单暴露出来）
        picker = (ROOT / "tools/pick_keywords.py").read_text(encoding="utf-8")
        self.assertNotIn('if "相关性" not in str(r[8] or "")', picker)

        # 已发布数据：以 manifest 里最新的一天为准（词表会被每次发布整体替换）
        mf = ROOT / "docs/data/keyword-search/manifest.json"
        self.assertTrue(mf.exists(), "关键词 manifest 没发布")
        manifest = json.loads(mf.read_text(encoding="utf-8"))
        self.assertTrue(manifest["dates"], "manifest 没有日期")
        # manifest 里的词表会被**按 monthlyVolume 降序重排**（看板左栏就是这么显示的），
        # 所以只比集合、不比顺序；要改顺序就改 publish_keyword_search.py 里那个 sorted()
        self.assertEqual(sorted(k["keyword"] for k in manifest["keywords"]), sorted(wanted),
                         "manifest 的词表应与 config/keywords-core.json 一致")
        doc = json.loads((ROOT / ("docs/data/keyword-search/%s.json" % manifest["dates"][0]))
                         .read_text(encoding="utf-8"))
        self.assertEqual(sorted(doc["keywords"]), sorted(wanted))
        # 广告位与自然位**两份都记、且每条标明是哪种**
        # （2026-09-23 用户："自然位置和广告位置都需要抓取，并表明"）
        self.assertIn('id="kwModes"', html)                          # 排名口径切换
        self.assertIn("data-kwmode=\"page\"", html)
        self.assertIn("KW_MODES", html)
        for mode in ('"organic"', '"ads"'):
            self.assertIn(mode, html)
        self.assertIn(".kw-card .pos.ad{background:#b54708}", html)   # 广告位角标颜色
        self.assertIn("function kwRows", html)
        # 只发布一份 pageTop = **第一页全部位置**，广告位与自然位都在这份里
        for rec in doc["keywords"].values():
            self.assertGreater(len(rec["pageTop"]), 0, "pageTop 没发布")
            # 第一页实测 60~63 个位置：低于 50 就说明又被截断了
            self.assertGreater(len(rec["pageTop"]), 50,
                               "pageTop 只记了 %d 个位置，第一页应该全记" % len(rec["pageTop"]))
            self.assertNotIn("organicTop", rec, "不该再单独存 organicTop（同一批数据存两份）")
            self.assertEqual([x["pageRank"] for x in rec["pageTop"]],
                             sorted(x["pageRank"] for x in rec["pageTop"]))   # 按页面位置升序
            self.assertEqual([x["pageRank"] for x in rec["pageTop"]],
                             list(range(1, len(rec["pageTop"]) + 1)))         # 位置连续、无缺号
            ads = [x for x in rec["pageTop"] if x["isAd"]]
            orgs = [x for x in rec["pageTop"] if not x["isAd"]]
            self.assertEqual(len(rec["pageTop"]) - len(ads), len(orgs))
            # 自然位连续编号 1..N，且编号必然不晚于它的页面位置（前面压着广告）
            self.assertEqual([x["organicRank"] for x in orgs], list(range(1, len(orgs) + 1)))
            self.assertTrue(all(x["pageRank"] >= x["organicRank"] for x in orgs))
            for x in rec["pageTop"]:
                self.assertRegex(x["asin"], r"^B[0-9A-Z]{9}$")
                self.assertIn("isAd", x)                             # 每条都要能看出是不是广告
                if x["isAd"]:
                    self.assertNotIn("organicRank", x)               # 广告位没有自然位名次
            # ⚠ 广告位**不是只堆在头部**：实测第一页 14 个广告散布到第 20 多位，
            # 自然位第 1 落在页面第 7 位左右，但后面还夹着广告（别写成 ads 全在 orgs 前面）
            self.assertTrue(ads, "第一页一条广告都没有？")
            self.assertLess(ads[0]["pageRank"], orgs[0]["pageRank"])              # 头部有广告
            self.assertTrue(any(x["pageRank"] > orgs[0]["pageRank"] for x in ads),
                            "广告位应散布在第一页各处，而非只在头部")
        sample = next(iter(doc["keywords"].values()))
        sample_ads = [x for x in sample["pageTop"] if x["isAd"]]
        sample_orgs = [x for x in sample["pageTop"] if not x["isAd"]]
        self.assertEqual(sample_orgs[0]["organicRank"], 1)       # 自然位从 1 开始编号
        self.assertEqual(sample_ads[0]["pageRank"], 1)           # 页面第 1 位是广告（这个品类如此）


    def test_code_review_fixes_stay_fixed(self):
        """2026-09-24 代码审查所修缺陷的守门断言。

        这些坑要么会**静默写坏数据**、要么会让页面**不可恢复地卡死**，回退代价很高，
        所以钉死在测试里。每条断言都写了"为什么不能改回去"。
        """
        html = (ROOT / "docs/index.html").read_text(encoding="utf-8")
        # ① 打标弹窗：keydown 必须在 close() 这个唯一出口里摘。
        #    以前只有 Escape/Enter 两个分支摘监听，点「保存/取消/清空/遮罩」关闭都会留下幽灵监听，
        #    之后在页面任意位置按回车都会把**已取消的草稿**写进 localStorage，还会再弹出打标窗。
        self.assertIn('const close = () => { document.removeEventListener("keydown", onKey); mask.remove(); };', html)
        self.assertIn("function onKey(e) {", html)
        # ② selectComparison：先判过期、再写 previous（反过来会让过期快照覆盖 previous，
        #    入榜/退出/价格变化全算错且界面无提示）
        self.assertIn("previous=loaded;refreshParentKey();", html)
        self.assertNotIn("previous=await load(entry);if(request!==snapshotRequest)return;", html)
        # ③ json 必须带超时，否则连接挂起时页面永久停在「正在载入快照…」
        self.assertIn("signal:controller.signal", html)
        self.assertIn("setTimeout(()=>controller.abort(),15000)", html)
        # ④ load 要做飞行中请求去重（以前只在 resolve 后写 cache，并发会重复下载同一份 JSON）
        self.assertIn("inflight.has(entry.file)", html)
        # ⑤ 跨类目索引写全局前必须校验是否过期
        self.assertIn("if(guard!==undefined&&guard!==snapshotRequest)return;", html)
        # ⑥ 日期切换要有失败兜底
        self.assertIn("function guardAsync(tag,promise)", html)
        self.assertIn('guardAsync("快照载入",selectSnapshot(', html)
        # ⑦ ageDays 必须像 age() 一样先校验格式：数据用哨兵串「未显示/无法获取」表达缺失，
        #    不校验会得到 NaN，而三个比较全为 false → 这些商品**反而通过**「上架天数」筛选
        self.assertIn("function ageDays(date){if(!date||!/^\\d{4}-\\d{2}-\\d{2}$/.test(String(date)))return null;", html)
        # ⑧ CSS：注释不能插在 `.kw-main` 与 `.kw-bar-right` 之间
        #    —— 会被解析成后代选择器 `.kw-main .kw-bar-right`，规则永不生效
        self.assertIn(".kw-bar-right{display:flex;align-items:flex-end;gap:10px;flex-wrap:wrap}", html)
        self.assertNotIn(".kw-main /*", html)

        # ⑨ build_enriched：输入新鲜度守门 + 产物名带日期 + 缺口非零退出
        #    （历史事故：用了昨天的输入文件，把 09-23 的数据发成 09-24 且毫无警告）
        be = (ROOT / "tools/build_enriched.py").read_text(encoding="utf-8")
        self.assertIn("输入新鲜度校验失败", be)
        self.assertIn("enriched-{ranking}-{node}-{snapshot_date}.json", be)
        self.assertIn('raise SystemExit("存在缺口', be)
        # ⑩ 价格判零必须用数值比较：`value == "0"` 会漏掉 "0.00" / "$0.00"
        self.assertNotIn('if not value or value == "0":', be)
        # ⑪ 覆盖率：$0.00 不算「有价格」（否则 MIN_COVERAGE["price"]=95 形同虚设）
        ps = (ROOT / "tools/publish_snapshot.py").read_text(encoding="utf-8")
        self.assertIn('if field == "price":', ps)
        # ⑫ 「只能改当天」的守门必须固定 UTC+8，不能跟本机时区走
        self.assertIn("datetime.now(timezone(timedelta(hours=8)))", ps)
        # ⑬ tag_style：`and None or None` 笔误（恒为 None）+ 人工文件损坏必须出声
        ts = (ROOT / "tools/tag_style.py").read_text(encoding="utf-8")
        self.assertNotIn('"brand": r.get("category") and None or None', ts)
        self.assertIn("其中的人工校核不会生效", ts)
        # ⑭ fetch_child_asins 的布尔优先级：`A and B or A` 等价于只看长度，B 是死条件
        fc = (ROOT / "tools/fetch_child_asins.py").read_text(encoding="utf-8")
        self.assertIn('if len(body) > 3000 and "result_table" in body:', fc)
        self.assertNotIn('"result_table" in body or len(body) > 3000', fc)
        # ⑮ 促销监控：固定等待路径必须封死（等不够会静默带回旧数据）；有旧数据时拒绝产出成品
        ft = (ROOT / "tools/fetch_asin_tracking.py").read_text(encoding="utf-8")
        self.assertIn("--run 必须配 --fresh-after", ft)
        self.assertIn("拒绝写出成品", ft)
        # ⑯ 关键词抓取：续跑缓存要辨日期；有失败/缺口要非零退出
        fk = (ROOT / "tools/fetch_keyword_search.py").read_text(encoding="utf-8")
        self.assertIn("缓存是 %s 的数据，重抓", fk)
        self.assertIn("本次数据不完整", fk)
        # ⑰ 关键词发布：缺词、位置数不足、日期不符都要拒绝
        pk = (ROOT / "tools/publish_keyword_search.py").read_text(encoding="utf-8")
        self.assertIn("拒绝发布", pk)
        # ⑱ 回算 quality 必须把 detail_source 传进去（漏传会把 detailSourceValid 静默写成 null）
        rq = (ROOT / "tools/recompute_quality.py").read_text(encoding="utf-8")
        self.assertIn('(payload.get("sources") or {}).get("productDetails")', rq)


if __name__ == "__main__":
    unittest.main()
