#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""给商品打款式标签：风格 / 袖型 / 季节（第一版：关键词规则层）。

用法：
  python tools/tag_style.py --date 2026-09-21

输出 work/style-tags-{date}.json，按**父体**存：
  {parentAsin: {"asin", "brand", "title", "sleeve", "season": [], "style": [],
                "source": "rule"|"ai"|"manual", "confidence": 0..1}}

设计要点：
· 打标按**父体**（款式是同款共享属性，与 parentAsin / listingDate 同类），
  一次标注全家受益；标签库会累积，每天只需给新父体打标。
· **季节与风格是多值**：标题普遍写成 "Clothing for Fall Spring"（一衣两季）、
  "Dressy Casual Boho"（多风格混搭），所以是数组，筛选时用「包含」而非「等于」。
· **袖型是单值**，但造型优先于长度：泡泡袖/喇叭袖比"长袖"更有辨识度，
  所以 "Puff Long Sleeve" 归为「泡泡袖」而不是「长袖」。
· 每个标签带 source 与 confidence，人工校核结果 source=manual 且优先级最高（不被规则覆盖）。

⚠ 标签**不写进每日快照**（daily/*.json 是不可变归档），单独存表，前端映射上去。
"""
import argparse
import collections
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORK = ROOT / "work"

# ── 标签体系（可调整；改这里等于改口径）────────────────────────────
# 袖型：单值，按造型 > 长度 的优先级取第一个命中的
SLEEVE = [
    ("泡泡袖", [r"puff(?:ed)? sleeve", r"puff short sleeve"]),
    ("喇叭袖", [r"bell sleeve", r"flutter sleeve", r"flare sleeve", r"trumpet sleeve"]),
    ("无袖",   [r"sleeveless", r"tank top", r"strapless", r"tube top"]),
    ("3/4袖",  [r"3/4 sleeve", r"three quarter sleeve", r"quarter sleeve", r"3/4 length sleeve"]),
    ("短袖",   [r"short sleeve", r"short-sleeve", r"cap sleeve", r"half sleeve"]),
    ("长袖",   [r"long sleeve", r"long-sleeve", r"long sleeve shirt"]),
]

# 季节：多值。标题不提季节时，用材质/款式词做弱推断（置信度低一点）
SEASON = {
    "春": [r"\bspring\b", r"spring fall", r"spring summer"],
    "夏": [r"\bsummer\b", r"beach", r"resort wear"],
    "秋": [r"\bfall\b", r"\bautumn\b", r"fall fashion", r"fall clothes"],
    "冬": [r"\bwinter\b", r"christmas", r"holiday"],
}
# 弱推断：材质/关键词 → 季节（confidence 打折）
SEASON_HINT = {
    "秋": [r"flannel", r"plaid", r"shacket", r"corduroy", r"sweater", r"knit", r"fleece"],
    "冬": [r"velvet", r"fleece", r"thermal", r"wool"],
    "夏": [r"linen", r"chiffon", r"mesh", r"sheer", r"sleeveless", r"crop top"],
    "春": [r"floral", r"pastel"],
}

# 面料：多值（一衣多料很常见，如 cotton linen）
FABRIC = [
    ("棉",     [r"\bcotton\b", r"cotton blend", r"\bpoplin\b"]),
    ("亚麻",   [r"\blinen\b", r"linen blend"]),
    ("真丝",   [r"\bsilk\b", r"silky", r"mulberry"]),
    ("缎面",   [r"\bsatin\b", r"sateen"]),
    ("雪纺",   [r"chiffon"]),
    ("牛仔",   [r"\bdenim\b"]),
    ("蕾丝",   [r"\blace\b", r"crochet", r"eyellet", r"eyelet"]),
    ("法兰绒", [r"flannel"]),
    ("灯芯绒", [r"corduroy"]),
    ("丝绒",   [r"\bvelvet\b", r"velour"]),
    ("针织",   [r"\bknit\b", r"knitted", r"ribbed"]),
    ("摇粒绒", [r"\bfleece\b", r"sherpa"]),
    ("华夫格", [r"waffle"]),
    ("网纱",   [r"\bmesh\b", r"\bsheer\b", r"tulle"]),
    ("牛津纺", [r"\boxford\b"]),
    ("纱布",   [r"\bgauze\b"]),
    ("羊毛",   [r"\bwool\b", r"merino", r"cashmere"]),
    ("皮/仿皮", [r"\bleather\b", r"\bfaux leather\b", r"suede", r"\bpu\b"]),
]

# 风格：多值
STYLE = {
    "通勤":   [r"business casual", r"work outfit", r"work wear", r"office", r"business work",
               r"work blouse", r"professional"],
    "优雅":   [r"dressy", r"elegant", r"formal", r"silky", r"satin blouse", r"chic"],
    "休闲":   [r"\bcasual\b", r"everyday", r"basic", r"loose fit", r"relaxed"],
    "波西米亚": [r"boho", r"bohemian", r"peasant", r"embroidered", r"tassel"],
    "度假":   [r"vacation", r"holiday", r"beach", r"resort", r"tropical", r"cruise"],
    "复古":   [r"vintage", r"retro", r"y2k", r"70s", r"80s", r"90s"],
    "西部":   [r"western", r"cowboy", r"cowgirl", r"country concert", r"rodeo"],
    "时髦":   [r"trendy", r"fashion", r"2026", r"cute", r"stylish"],
}


def norm(s):
    return (s or "").lower()


def pick_sleeve(title):
    t = norm(title)
    for name, pats in SLEEVE:
        if any(re.search(p, t) for p in pats):
            return name, "rule", 0.9
    return None, None, 0.0


def pick_season(title, sleeve=None):
    t = norm(title)
    # ① 标题明说（最可信）
    hits = [k for k, pats in SEASON.items() if any(re.search(p, t) for p in pats)]
    if hits:
        return sorted(set(hits)), "rule", 0.9
    # ② 材质词推断
    hints = {k for k, pats in SEASON_HINT.items() if any(re.search(p, t) for p in pats)}
    # ③ 袖型联合推断：长度决定大体季节（法兰绒/羊毛等厚料再补冬）
    if sleeve == "长袖":
        hints |= {"秋", "春"}
        if any(re.search(p, t) for p in [r"flannel", r"plaid", r"shacket", r"knit", r"sweater",
                                         r"fleece", r"corduroy", r"velvet", r"wool", r"thermal"]):
            hints |= {"冬"}
    elif sleeve in ("短袖", "无袖"):
        hints |= {"夏", "春"}
    elif sleeve == "3/4袖":
        hints |= {"春", "秋"}
    if hints:
        return sorted(hints), "rule(inferred)", 0.5
    return [], None, 0.0


# 成分百分比 → 中文面料名（来自五点描述/产品概述，比标题里的宣传词更可靠）
COMPOSITION = [
    (r"cotton", "棉"), (r"polyester", "涤纶"), (r"spandex|elastane", "氨纶"),
    (r"rayon|viscose", "粘胶"), (r"modal", "莫代尔"), (r"nylon", "锦纶"),
    (r"acrylic", "腈纶"), (r"wool|merino|cashmere", "羊毛"), (r"silk", "真丝"),
    (r"linen", "亚麻"), (r"lyocell|tencel|modal", "天丝"),
]


def pick_composition(text):
    """解析「95% Cotton, 5% Spandex」→ 取占比 ≥20% 的纤维（按占比降序）。

    只看有没有关键词不行：几乎每件都含 polyester，会被标成「涤纶」而掩盖真正的主料。
    """
    t = norm(text or "")
    best = {}
    for pat, cn in COMPOSITION:
        pcts = []
        w = "(?:%s)" % pat          # ⚠ 必须包成非捕获组：wool|merino 会劈开整条正则
        for m in re.finditer(r"(\d{1,3})\s*%[^,;.\n]{0,18}" + w, t):
            pcts.append(int(m.group(1)))
        for m in re.finditer(w + r"[^,;.\n]{0,18}(\d{1,3})\s*%", t):
            pcts.append(int(m.group(1)))
        if pcts:
            p = max(pcts)
            if cn not in best or p > best[cn]:
                best[cn] = p
    ranked = sorted(best.items(), key=lambda kv: -kv[1])
    picks = [cn for cn, pct in ranked if pct >= 20]
    if not picks and ranked:
        picks = [ranked[0][0]]          # 都没到 20% 就取占比最高的那个
    return picks


def pick_fabric(title, extra=""):
    """面料 = 标题材质词 ∪ 成分百分比（extra 传五点描述+产品概述）。"""
    t = norm(title)
    desc = [name for name, pats in FABRIC if any(re.search(p, t) for p in pats)]
    comp = pick_composition(extra or "")
    out, seen = [], set()
    for v in comp + desc:                # 纤维（按占比）在前，描述词（缎面/蕾丝…）在后
        if v and v not in seen:
            seen.add(v); out.append(v)
    out = out[:3]                        # 最多 3 个，避免卡片上一排标签
    if comp:
        return out, "rule(composition)", 0.8
    return out, ("rule" if desc else None), (0.85 if desc else 0.0)



    t = norm(title)
    hits = [name for name, pats in FABRIC if any(re.search(p, t) for p in pats)]
    return sorted(set(hits)), ("rule" if hits else None), (0.85 if hits else 0.0)


def pick_style(title):
    t = norm(title)
    hits = [k for k, pats in STYLE.items() if any(re.search(p, t) for p in pats)]
    return sorted(set(hits)), ("rule" if hits else None), (0.85 if hits else 0.0)


STYLE_PRIORITY = ["波西米亚", "西部", "度假", "复古", "优雅", "通勤", "时髦", "休闲"]


def primary_style(styles):
    """主风格：按辨识度取优先级，都不命中则取第一个；没有就空串。"""
    for k in STYLE_PRIORITY:
        if k in (styles or []):
            return k
    return (styles or [""])[0]


def load_manual(date, path=None):
    """打标工作台/看板导出的人工确认结果；存在即覆盖规则与 AI 的判断。

    ⚠ 会合并**全部** `work/style-tags-manual-*.json`（按文件名日期升序，后写的优先）：
    人工确认按父体存、与日期无关，昨天确认过的今天必须继续生效，
    否则每天重跑就把用户之前的工作打回机器值（2026-09-22 修）。
    """
    files = []
    if path:
        files.append(pathlib.Path(path))
    else:
        target = WORK / ("style-tags-manual-%s.json" % date)
        if target.exists():
            files.append(target)
        others = sorted(p for p in WORK.glob("style-tags-manual-*.json") if p != target)
        files.extend(others)
    merged = {}
    for p in files:
        if not p.exists():
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        tags = d.get("tags", d) if isinstance(d, dict) else {}
        for k, v in tags.items():
            if isinstance(v, dict) and any(k in v for k in ("sleeve", "season", "fabric", "style", "imageType")):
                merged[k] = v
    return merged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--out", help="默认 work/style-tags-{date}.json")
    ap.add_argument("--manual", help="打标工作台导出的人工结果；默认自动找 work/style-tags-manual-{date}.json")
    args = ap.parse_args()
    manual = load_manual(args.date, args.manual)

    parsed = json.loads((WORK / ("ecomtool-%s-parsed.json" % args.date)).read_text(encoding="utf-8"))
    # 面料不只靠标题：详情里的「五点描述/产品概述」常写着成分（95% Cotton, 5% Spandex）
    details_file = WORK / ("ecomtool-details-%s.json" % args.date)
    details = json.loads(details_file.read_text(encoding="utf-8")) if details_file.exists() else {}
    ma = json.loads((WORK / ("market-analysis-%s.json" % args.date)).read_text(encoding="utf-8"))

    # 商品去重（同一 ASIN 可能同时在新品/热销榜）
    items, seen = [], set()
    for rk in ("new-releases", "bestsellers"):
        for node, rows in parsed.get(rk, {}).get("by_node", {}).items():
            for r in rows:
                if r["asin"] in seen:
                    continue
                seen.add(r["asin"])
                items.append(r)

    tags, tally_sleeve, tally_season, tally_style, tally_fabric = {}, collections.Counter(), collections.Counter(), collections.Counter(), collections.Counter()
    unresolved = []
    for r in items:
        parent = (ma.get(r["asin"], {}) or {}).get("parentAsin") or r["asin"]
        if parent in tags:
            continue
        sl, sl_src, sl_cf = pick_sleeve(r["title"])
        se, se_src, se_cf = pick_season(r["title"], sl)
        st, st_src, st_cf = pick_style(r["title"])
        det = details.get(r["asin"], {}) or {}
        fb_text = " ".join(str(x or "") for x in (det.get("五点描述"), det.get("产品概述"), det.get("标题")))
        fb, fb_src, fb_cf = pick_fabric(r["title"], fb_text)
        src = "rule" if (sl_src or se_src or st_src or fb_src) else "unresolved"
        conf = round(max(sl_cf, se_cf, st_cf, fb_cf), 2)
        rec = {
            "asin": r["asin"], "brand": r.get("category") and None or None,
            "titleSample": r["title"][:170],
            "sleeve": sl, "season": se, "fabric": fb, "style": st,
            # 主图类型：文字里没有信号（要看图），机器不给建议，只等人工确认
            "imageType": "",
            "source": src, "confidence": conf,
        }
        # 人工确认的结果优先级最高，且永久有效（重跑不会被机器判断打回）
        m = manual.get(parent)
        if m:
            rec.update({
                "sleeve": m.get("sleeve") or sl,
                "season": m.get("season") or se,
                "fabric": m.get("fabric") or fb,
                "imageType": m.get("imageType") or "",
                "style": m.get("style") or st,
                "source": "manual", "confidence": 1.0,
            })
        rec["stylePrimary"] = primary_style(rec["style"])
        tags[parent] = rec
        tally_sleeve[sl or "（未定）"] += 1
        tally_season.update(se or ["（未定）"])
        tally_style.update(st or ["（未定）"])
        tally_fabric.update(fb or ["（未定）"])
        if not sl or not se or not st:
            unresolved.append({"parent": parent, "asin": r["asin"], "title": r["title"][:170],
                               "sleeve": sl, "season": se, "style": st})

    out = pathlib.Path(args.out) if args.out else WORK / ("style-tags-%s.json" % args.date)
    out.write_text(json.dumps(tags, ensure_ascii=False, indent=1), encoding="utf-8")

    # 同步一份到看板数据目录（index-style.html 预览版读这里；json() 带 no-store，刷新即生效）
    dashboard = WORK.parent / "docs/data/style-tags.json"
    if dashboard.parent.exists():
        dashboard.write_text(json.dumps({
            "updatedAt": args.date, "source": "tag_style.py",
            "dimension": {"sleeve": "单值", "season": "多值", "fabric": "多值", "style": "多值 + stylePrimary 单值主风格", "imageType": "单值/纯人工"},
            "tags": tags,
        }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print("已同步到", dashboard)

    n = len(tags)
    n_manual = sum(1 for r in tags.values() if r["source"] == "manual")
    print("父体 %d 个，已打标（其中人工确认 %d 个）" % (n, n_manual))
    print()
    print("袖型:", dict(tally_sleeve.most_common()))
    print("季节:", dict(tally_season.most_common()))
    print("风格:", dict(tally_style.most_common()))
    print("面料:", dict(tally_fabric.most_common()))
    print()
    print("完整度：袖型 %.1f%% · 季节 %.1f%% · 风格 %.1f%%" % (
        100.0 * (n - tally_sleeve["（未定）"]) / n,
        100.0 * (n - tally_season["（未定）"]) / n,
        100.0 * (n - tally_style["（未定）"]) / n))
    print("有任一维度未定的父体：%d 个" % len(unresolved))
    if unresolved:
        (WORK / ("_unresolved_style_%s.json" % args.date)).write_text(
            json.dumps(unresolved, ensure_ascii=False, indent=1), encoding="utf-8")
        print("  清单已写入 work/_unresolved_style_%s.json（交给 AI/人工补）" % args.date)
    print("已写出", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
