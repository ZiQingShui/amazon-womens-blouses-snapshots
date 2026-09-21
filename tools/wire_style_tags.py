# -*- coding: utf-8 -*-
"""从正式版 docs/index.html 生成「款式标签预览版」docs/index-style.html。

背景：用户要求款式标签**先不要进正式版**，单独出一个版本试用。
所以预览版不做手工维护（会和正式版分叉），而是**每次从正式版重新生成**：

    python tools/wire_style_tags.py           # 生成 docs/index-style.html + dist 同步
    python tools/wire_style_tags.py --check   # 只检查锚点是否还能匹配（正式版改名/重构后会失败，提示要修脚本）

生成的预览版 = 正式版 + 四处能力：
  1. 加载 docs/data/style-tags.json（打标工作台的结果）
  2. 商品卡片上显示 袖型 / 季节 / 主风格 三个标签 chip
  3. 筛选区新增第 4 组「款式风格」（袖型 / 季节 / 主风格，带计数）
  4. **点卡片上的标签可就地编辑**（弹窗改三个维度 → 存 localStorage，与打标工作台同一个 key；
     「款式风格」组标题右侧「导出我的修改」把改动落盘成 manual JSON，回到 work/ 即可永久生效）
"""
import json
import pathlib
import sys

PROJ = pathlib.Path(__file__).resolve().parent.parent
SRC = PROJ / "docs/index.html"
DST = PROJ / "docs/index-style.html"
DATA = PROJ / "docs/data/style-tags.json"
CSS_FILE = PROJ / "tools/preview_style.css"
JS_FILE = PROJ / "tools/preview_style.js"

# 每个替换：(说明, 原文, 新文)
EDITS = [
    ("filters 加三个字段",
     'multiCategory:""};',
     'multiCategory:"",sleeve:"",season:"",style:""};'),

    ("bindings 加三个下拉",
     'filterMultiCategory:"multiCategory"};',
     'filterMultiCategory:"multiCategory",filterSleeve:"sleeve",filterSeason:"season",filterStyle:"style"};'),

    ("预览版样式：注入 tools/preview_style.css",
     '.fgroup-title:after{content:"";flex:1;height:1px;background:#e6ecf5}',
     '.fgroup-title:after{content:"";flex:1;height:1px;background:#e6ecf5}__PREVIEW_CSS__'),

    ("init 里加载标签数据 + 本机改动",
     'const [registry,tree]=await Promise.all([json("data/categories.json"),json("data/category-tree.json")]);',
     'const [registry,tree,styleDoc]=await Promise.all([json("data/categories.json"),json("data/category-tree.json"),'
     'json("data/style-tags.json").catch(()=>null)]);STYLE_TAGS=(styleDoc&&styleDoc.tags)||{};'
     'STYLE_DOC_DATE=(styleDoc&&styleDoc.updatedAt)||"";loadLocalStyleTags();loadCustomOptions();syncStyleOptions();'),

    ("选项顺序常量 + syncStyleOptions（下拉选项按实际标签值填充）",
     'function syncBrandOptions(){',
     'const SLEEVE_ORDER=["长袖","短袖","3/4袖","泡泡袖","喇叭袖","无袖"],'
     'SEASON_ORDER=["春","夏","秋","冬"],'
     'STYLE_ORDER=["优雅","通勤","西部","度假","波西米亚","休闲","复古","时髦"];\n'
     '__PREVIEW_JS__\n'
     'function syncBrandOptions(){'),

    ("卡片标签的点击监听（捕获）",
     'productGrid.addEventListener("click",',
     'productGrid.addEventListener("click",event=>{const sc=event.target.closest(".style-chips");'
     'if(!sc)return;event.stopPropagation();event.preventDefault();'
     'openStyleEditor(sc.dataset.parent,sc.dataset.asin)},true);\n'
     'productGrid.addEventListener("click",'),

    # 导出按钮：绑定 + 初始化显示（改过几项就显示出来）
    ("导出按钮绑定",
     '$("fastRisers").addEventListener("clic',
     '$("styExport").addEventListener("click",exportLocalStyleTags);'
     '$("styClear").addEventListener("click",()=>{const n=Object.keys(localTags).length;'
     'if(!n)return;if(!confirm(`清除本机这 ${n} 项改动？\n改完如果还没导出过，清除后就找不回来了。`))return;'
     'localTags={};saveLocalStyleTags();if(current)render();styFlash("已清除本机改动，现在显示的是标签库里的值")});'
     'saveLocalStyleTags();\n'
     '$("fastRisers").addEventListener("clic'),

    ("全局变量 STYLE_TAGS",
     'const filters={',
     'let STYLE_TAGS={},STYLE_DOC_DATE="",LOCAL_KEY="styleTagsManual_",localTags={};\nconst filters={'),

    ("filteredRows 加款式过滤",
     'if(query&&!`${p.brand||""} ${p.title||""} ${p.asin||""}`.toLowerCase().includes(query))return false;',
     'const styleTag=tagOf(p),styleOk=!!(styleTag&&styleTag.confirmed);'
     'if(active.sleeve&&(!styleOk||styleTag.sleeve!==active.sleeve))return false;'
     'if(active.season&&(!styleOk||!(styleTag.season||[]).includes(active.season)))return false;'
     'if(active.style&&(!styleOk||styleTag.stylePrimary!==active.style))return false;'
     'if(query&&!`${p.brand||""} ${p.title||""} ${p.asin||""}`.toLowerCase().includes(query))return false;'),

    ("optionMeta 三个分支 + 计数映射",
     'const key={filterBrand:"brand",filterPromo:"promo",filterMovement:"movement",filterRank:"rank",'
     'filterMainBsr:"mainBsr",filterSubBsr:"subBsr",filterAge:"age",filterMultiCategory:"multiCategory"}[id];',
     'else if(id==="filterSleeve"){const map={长袖:["长袖款","blue"],短袖:["短袖款","blue"],"3/4袖":["七分/四分之三袖","blue"],'
     '泡泡袖:["泡泡袖（造型袖）","orange"],喇叭袖:["喇叭袖/灯笼袖","orange"],无袖:["无袖或吊带","green"]};'
     'if(value)[description,tone]=map[value]||["该袖型","blue"];else description="不限袖型"}'
     'else if(id==="filterSeason"){const map={春:["适合春季","green"],夏:["适合夏季","blue"],秋:["适合秋季","orange"],冬:["适合冬季","red"]};'
     'if(value)[description,tone]=map[value]||["该季节","blue"];else description="不限季节"}'
     'else if(id==="filterStyle"){if(value){description=`主风格为「${value}」`;tone="blue"}else description="不限主风格"}'
     'const key={filterBrand:"brand",filterPromo:"promo",filterMovement:"movement",filterRank:"rank",'
     'filterMainBsr:"mainBsr",filterSubBsr:"subBsr",filterAge:"age",filterMultiCategory:"multiCategory",'
     'filterSleeve:"sleeve",filterSeason:"season",filterStyle:"style"}[id];'),

    ("filterLabel 映射（已选条件栏）",
     'multiCategory:"filterMultiCategory"}[key]',
     'multiCategory:"filterMultiCategory",sleeve:"filterSleeve",season:"filterSeason",style:"filterStyle"}[key]'),

    ("enhanceSelect 的 label 表（漏了会叫「筛选条件」）",
     'filterMultiCategory:"类目覆盖"}',
     'filterMultiCategory:"类目覆盖",filterSleeve:"袖型",filterSeason:"季节",filterStyle:"主风格"}'),

    ("卡片插入标签行",
     '${promoHtml}</div><div class="history">',
     '${promoHtml}</div>${styleChips}<div class="history">'),

    ("卡片标签的构造（可点击编辑）",
     'return `<article class="card"',
     'const styleTag=tagOf(p),mch=styleTag&&!styleTag.confirmed?" machine":"",styleChips=styleTag?`<div class="style-chips${localTags[p.parentAsin||p.asin]?" edited":""}"'
     ' data-parent="${esc(p.parentAsin||p.asin)}" data-asin="${esc(p.asin)}" title="点击修改款式标签">'
     '${styleTag.sleeve?`<span class="sc sc-sleeve${mch}">${esc(styleTag.sleeve)}</span>`:""}'
     '${(styleTag.season||[]).map(x=>`<span class="sc sc-season${mch}">${esc(x)}</span>`).join("")}'
     '${styleTag.stylePrimary?`<span class="sc sc-style${mch}">${esc(styleTag.stylePrimary)}</span>`:""}'
     '<span class="sty-hint"><svg viewBox="0 0 16 16" width="10" height="10" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11.3 2.4l2.3 2.3L5.2 13 2.6 13.4 3 10.8z"/></svg>修改</span></div>`:"";\n'
     'return `<article class="card"'),
]

NEW_GROUP = (
    '<div class="fgroup"><div class="fgroup-title">款式风格'
    '<button class="sty-export" id="styExport" type="button" hidden>导出我的修改</button>'
    '<button class="sty-export danger" id="styClear" type="button" hidden>清除本机改动</button>'
    '</div><div class="filter-grid fg-4">'
    '<label class="field compact"><span>袖型</span><select id="filterSleeve" aria-label="袖型">'
    '<option value="">全部袖型</option></select></label>'
    '<label class="field compact"><span>季节</span><select id="filterSeason" aria-label="季节">'
    '<option value="">全部季节</option></select></label>'
    '<label class="field compact wide"><span>主风格</span><select id="filterStyle" aria-label="主风格">'
    '<option value="">全部风格</option></select></label>'
    '</div></div>'
)


def build(src_text: str) -> str:
    s = src_text
    for name, old, new in EDITS:
        if s.count(old) != 1:
            raise SystemExit("✗ 锚点失效（正式版可能改过）：%s —— 出现 %d 次" % (name, s.count(old)))
        s = s.replace(old, new, 1)
    # 注入外部 CSS / JS（预览版新增部分；改这两块不用动本脚本）
    if "__PREVIEW_CSS__" in s:
        s = s.replace("__PREVIEW_CSS__", CSS_FILE.read_text(encoding="utf-8"), 1)
    if "__PREVIEW_JS__" in s:
        s = s.replace("__PREVIEW_JS__", JS_FILE.read_text(encoding="utf-8"), 1)

    # 筛选区新增第 4 组：插在最后一个 fgroup 之后（即 </section> 之前）
    anchor = '<div class="fgroup-title">价格与状态'
    i = s.index(anchor)
    end = s.index("</section>", i)
    s = s[:end] + NEW_GROUP + s[end:]
    # title 标注，避免和正式版混淆
    import re
    m = re.search(r"<title>(.*?)</title>", s, re.S)
    s = s.replace(m.group(0), "<title>%s（款式标签预览版）</title>" % m.group(1), 1)
    return s


def main():
    if "--check" in sys.argv:
        for f in (CSS_FILE, JS_FILE):
            print("%s %s（%d 字节）" % ("OK" if f.exists() else "缺失", f.name,
                                        f.stat().st_size if f.exists() else 0))
        src = SRC.read_text(encoding="utf-8")
        bad = [(n, src.count(o)) for n, o, _ in EDITS if src.count(o) != 1]
        if bad:
            print("✗ 有 %d 个锚点失效，需要更新脚本：" % len(bad))
            for n, c in bad:
                print("   %s（出现 %d 次）" % (n, c))
            return 1
        print("✓ 全部 %d 个锚点仍匹配，可以重跑生成" % len(EDITS))
        return 0

    src = SRC.read_text(encoding="utf-8")
    out = build(src)
    DST.write_text(out, encoding="utf-8")
    dist = PROJ / "dist/index-style.html"
    dist.write_text(out, encoding="utf-8")

    tags = json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else {}
    n = len(tags.get("tags", tags))
    print("已生成 %s" % DST)
    print("  %d KB · 标签库 %d 个父体（%s）" % (len(out) // 1024, n, DATA.name if DATA.exists() else "缺失！"))
    print("已同步 dist/index-style.html")
    print()
    print("访问：http://127.0.0.1:8000/index-style.html?category=2368365011&ranking=new-releases")


if __name__ == "__main__":
    sys.exit(main())
