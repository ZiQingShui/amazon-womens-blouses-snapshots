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

# 每个替换：(说明, 原文, 新文)
EDITS = [
    ("filters 加三个字段",
     'multiCategory:""};',
     'multiCategory:"",sleeve:"",season:"",style:""};'),

    ("bindings 加三个下拉",
     'filterMultiCategory:"multiCategory"};',
     'filterMultiCategory:"multiCategory",filterSleeve:"sleeve",filterSeason:"season",filterStyle:"style"};'),

    ("CSS：款式 chip + 主风格跨格",
     '.fgroup-title:after{content:"";flex:1;height:1px;background:#e6ecf5}',
     '.fgroup-title:after{content:"";flex:1;height:1px;background:#e6ecf5}'
     '.filter-grid .field.wide{grid-column:span 2}'
     '.style-chips{display:flex;flex-wrap:wrap;gap:4px;margin-top:6px}'
     '.sc{font-size:10.5px;font-weight:750;padding:2px 7px;border-radius:6px;line-height:1.5}'
     '.sc-sleeve{background:#eef4ff;color:#175cd3}'
     '.sc-season{background:#e9f7f1;color:#087a55}'
     '.sc-style{background:#fdeaf4;color:#c11574}'
     '.style-chips{cursor:pointer}'
     '.style-chips:hover .sc{filter:brightness(.94)}'
     '.style-chips.edited .sc:last-child{box-shadow:0 0 0 2px #f5c98a}'
     # 就地编辑弹窗（append 到 body，避开卡片 hover transform 造成的层叠上下文）
     '.sty-mask{position:fixed;inset:0;z-index:200;background:rgba(16,32,59,.45);display:flex;'
     'align-items:center;justify-content:center;padding:18px}'
     '.sty-box{background:#fff;border-radius:14px;width:100%;max-width:540px;max-height:88vh;overflow:auto;'
     'padding:15px 17px;box-shadow:0 22px 55px rgba(16,32,59,.3)}'
     '.sty-head{display:flex;gap:11px;align-items:flex-start;margin-bottom:13px}'
     '.sty-head img{width:64px;height:64px;object-fit:contain;background:#f7f9fc;border-radius:9px;flex:0 0 64px}'
     '.sty-head .t{font-size:12.5px;line-height:1.5;color:#475467;max-height:58px;overflow:hidden}'
     '.sty-row{margin-bottom:11px}'
     '.sty-row b{display:block;font-size:12px;margin-bottom:5px;color:#1d2939;font-weight:800}'
     '.sty-row em{font-style:normal;font-size:11px;color:#98a2b3;font-weight:600;margin-left:5px}'
     '.sty-chips{display:flex;flex-wrap:wrap;gap:6px}'
     '.sty-chip{padding:6px 11px;border:1px solid #d5e0ed;border-radius:8px;background:#f9fbfd;'
     'font-size:12.5px;font-weight:650;color:#344054;cursor:pointer}'
     '.sty-chip.on{background:#175cd3;border-color:#175cd3;color:#fff}'
     '.sty-foot{display:flex;gap:8px;justify-content:flex-end;margin-top:13px;padding-top:12px;'
     'border-top:1px solid #eef1f6}'
     '.sty-btn{padding:7px 13px;border:1px solid #d5e0ed;border-radius:9px;background:#fff;'
     'font:inherit;font-size:13px;font-weight:650;color:#344054;cursor:pointer}'
     '.sty-btn.pri{background:#175cd3;border-color:#175cd3;color:#fff}'
     '.sty-export{margin-left:7px;padding:2px 9px;border:1px solid #d5e0ed;border-radius:7px;background:#fff;'
     'font:inherit;font-size:10.5px;font-weight:750;color:#175cd3;letter-spacing:0;cursor:pointer}'
     '.sty-toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:#1d2939;color:#fff;'
     'padding:9px 17px;border-radius:9px;font-size:13px;font-weight:650;z-index:300;opacity:0;'
     'transition:opacity .2s;pointer-events:none}'
     '.sty-toast.show{opacity:1}'),

    ("init 里加载标签数据 + 本机改动",
     'const [registry,tree]=await Promise.all([json("data/categories.json"),json("data/category-tree.json")]);',
     'const [registry,tree,styleDoc]=await Promise.all([json("data/categories.json"),json("data/category-tree.json"),'
     'json("data/style-tags.json").catch(()=>null)]);STYLE_TAGS=(styleDoc&&styleDoc.tags)||{};'
     'STYLE_DOC_DATE=(styleDoc&&styleDoc.updatedAt)||"";loadLocalStyleTags();syncStyleOptions();'),

    ("下拉标签名 + 选项填充函数",
     'function syncBrandOptions(){',
     'const SLEEVE_ORDER=["长袖","短袖","3/4袖","泡泡袖","喇叭袖","无袖"],SEASON_ORDER=["春","夏","秋","冬"],'
     'STYLE_ORDER=["优雅","通勤","西部","度假","波西米亚","休闲","复古","时髦"];\n'
     'function syncStyleOptions(){const seen={sleeve:new Set(),season:new Set(),style:new Set()};'
     'Object.values(STYLE_TAGS).forEach(t=>{if(t.sleeve)seen.sleeve.add(t.sleeve);'
     '(t.season||[]).forEach(x=>seen.season.add(x));if(t.stylePrimary)seen.style.add(t.stylePrimary)});'
     'const fill=(id,order,set,allLabel)=>{const select=$(id),keep=select.value;'
     'select.innerHTML=`<option value="">${allLabel}</option>`+order.filter(v=>set.has(v))'
     '.map(v=>`<option value="${v}">${v}</option>`).join("");select.value=keep;'
     'if(!select.options.length||![...select.options].some(o=>o.value===keep))select.value=""};'
     'fill("filterSleeve",SLEEVE_ORDER,seen.sleeve,"全部袖型");'
     'fill("filterSeason",SEASON_ORDER,seen.season,"全部季节");'
     'fill("filterStyle",STYLE_ORDER,seen.style,"全部风格")}\n'
     # 就地编辑：与打标工作台共用同一个 localStorage key
     'function loadLocalStyleTags(){LOCAL_KEY="styleTagsManual_"+(STYLE_DOC_DATE||"");'
     'try{localTags=JSON.parse(localStorage.getItem(LOCAL_KEY)||"{}")}catch(e){localTags={}}}\n'
     'function saveLocalStyleTags(){try{localStorage.setItem(LOCAL_KEY,JSON.stringify(localTags))}catch(e){}'
     'const b=document.getElementById("styExport");if(b){const n=Object.keys(localTags).length;'
     'b.hidden=!n;b.textContent="导出我的修改"+(n?` (${n})`:"")}}\n'
     'function tagOf(p){const key=p.parentAsin||p.asin,base=STYLE_TAGS[key]||STYLE_TAGS[p.asin]||null,'
     'm=localTags[key];if(!m)return base;return Object.assign({},base||{},{sleeve:m.sleeve||"",'
     'season:m.season||[],stylePrimary:m.style||"",source:"manual"})}\n'
     'function styFlash(msg){let t=document.getElementById("styToast");'
     'if(!t){t=document.createElement("div");t.id="styToast";t.className="sty-toast";document.body.appendChild(t)}'
     't.textContent=msg;t.classList.add("show");clearTimeout(t._h);'
     't._h=setTimeout(()=>t.classList.remove("show"),2000)}\n'
     'function openStyleEditor(parent,asin){'
     'const p=((current&&current.items)||[]).find(x=>x.asin===asin)||{},t=tagOf({parentAsin:parent,asin:asin})||{};'
     'const draft={sleeve:t.sleeve||"",season:[...(t.season||[])],style:t.stylePrimary||""};'
     'const mask=document.createElement("div");mask.className="sty-mask";'
     'const rows=[["sleeve","袖型",SLEEVE_ORDER,0],["season","季节",SEASON_ORDER,1],["style","主风格",STYLE_ORDER,0]];'
     'mask.innerHTML=`<div class="sty-box" role="dialog" aria-label="修改款式标签"><div class="sty-head">'
     '<img src="${p.image?safeImageUrl(p.image):""}" alt=""><div><b style="font-size:13px">${esc((p.title||asin).slice(0,90))}</b>'
     '<div class="t">${esc(asin)}${p.brand?" · "+esc(p.brand):""}</div></div></div>`+'
     'rows.map(([dim,label,order,multi])=>`<div class="sty-row"><b>${label}'
     '<em>${multi?"可多选":"单选"}</em></b><div class="sty-chips" data-dim="${dim}">`+'
     'order.map(v=>`<button type="button" class="sty-chip ${(multi?draft[dim].includes(v):draft[dim]===v)?"on":""}" data-v="${v}">${v}</button>`).join("")+`</div></div>`).join("")+'
     '`<div class="sty-foot"><button type="button" class="sty-btn" data-act="reset">恢复机器判断</button>'
     '<button type="button" class="sty-btn" data-act="cancel">取消</button>'
     '<button type="button" class="sty-btn pri" data-act="save">保存</button></div></div>`;'
     'document.body.appendChild(mask);const close=()=>mask.remove();'
     'mask.addEventListener("click",event=>{'
     'if(event.target===mask){close();return}'
     'const chip=event.target.closest(".sty-chip");if(chip){'
     'const dim=chip.parentNode.dataset.dim,v=chip.dataset.v;'
     'if(dim==="season"){const i=draft.season.indexOf(v);i<0?draft.season.push(v):draft.season.splice(i,1)}'
     'else draft[dim]=draft[dim]===v?"":v;'
     '[...chip.parentNode.children].forEach(c=>c.classList.toggle("on",'
     'dim==="season"?draft.season.includes(c.dataset.v):draft[dim]===c.dataset.v));return}'
     'const act=(event.target.closest("[data-act]")||{}).dataset;'
     'if(!act||!act.act)return;'
     'if(act.act==="cancel")close();'
     'else if(act.act==="reset"){delete localTags[parent];saveLocalStyleTags();if(current)render();close();styFlash("已恢复机器判断")}'
     'else if(act.act==="save"){localTags[parent]={sleeve:draft.sleeve,season:[...draft.season],style:draft.style,ts:Date.now()};'
     'saveLocalStyleTags();if(current)render();close();styFlash("已保存 · 点「导出我的修改」落盘")}});'
     'document.addEventListener("keydown",function esc(e){if(e.key==="Escape"){close();document.removeEventListener("keydown",esc)}})}\n'
     'function exportLocalStyleTags(){const n=Object.keys(localTags).length;'
     'if(!n){styFlash("还没有改过任何标签");return}'
     'const out={updatedAt:new Date().toISOString(),source:"dashboard-edit",date:STYLE_DOC_DATE,tags:localTags};'
     'const a=document.createElement("a");'
     'a.href=URL.createObjectURL(new Blob([JSON.stringify(out,null,1)],{type:"application/json"}));'
     'a.download="style-tags-manual-"+(STYLE_DOC_DATE||"latest")+".json";a.click();'
     'styFlash(`已导出 ${n} 条 · 放进 work/ 后跑 tag_style.py 即永久生效`)}\n'
     'function syncBrandOptions(){'),

    # 点卡片上的标签 → 就地编辑（捕获阶段，抢在打开单品历史之前）
    ("卡片标签的点击监听（捕获）",
     'productGrid.addEventListener("click",',
     'productGrid.addEventListener("click",event=>{const sc=event.target.closest(".style-chips");'
     'if(!sc)return;event.stopPropagation();event.preventDefault();'
     'openStyleEditor(sc.dataset.parent,sc.dataset.asin)},true);\n'
     'productGrid.addEventListener("click",'),

    # 导出按钮：绑定 + 初始化显示（改过几项就显示出来）
    ("导出按钮绑定",
     '$("fastRisers").addEventListener("clic',
     '$("styExport").addEventListener("click",exportLocalStyleTags);saveLocalStyleTags();\n'
     '$("fastRisers").addEventListener("clic'),

    ("全局变量 STYLE_TAGS",
     'const filters={',
     'let STYLE_TAGS={},STYLE_DOC_DATE="",LOCAL_KEY="styleTagsManual_",localTags={};\nconst filters={'),

    ("filteredRows 加款式过滤",
     'if(query&&!`${p.brand||""} ${p.title||""} ${p.asin||""}`.toLowerCase().includes(query))return false;',
     'const styleTag=tagOf(p);'
     'if(active.sleeve&&(!styleTag||styleTag.sleeve!==active.sleeve))return false;'
     'if(active.season&&(!styleTag||!(styleTag.season||[]).includes(active.season)))return false;'
     'if(active.style&&(!styleTag||styleTag.stylePrimary!==active.style))return false;'
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
     'const styleTag=tagOf(p),styleChips=styleTag?`<div class="style-chips${localTags[p.parentAsin||p.asin]?" edited":""}"'
     ' data-parent="${esc(p.parentAsin||p.asin)}" data-asin="${esc(p.asin)}" title="点击修改款式标签">'
     '${styleTag.sleeve?`<span class="sc sc-sleeve">${esc(styleTag.sleeve)}</span>`:""}'
     '${(styleTag.season||[]).map(x=>`<span class="sc sc-season">${esc(x)}</span>`).join("")}'
     '${styleTag.stylePrimary?`<span class="sc sc-style">${esc(styleTag.stylePrimary)}</span>`:""}</div>`:"";\n'
     'return `<article class="card"'),
]

NEW_GROUP = (
    '<div class="fgroup"><div class="fgroup-title">款式风格'
    '<button class="sty-export" id="styExport" type="button" hidden>导出我的修改</button>'
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
