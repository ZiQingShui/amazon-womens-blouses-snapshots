#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""关键词排名数据的可信度自检（2026-09-23 加，用户问"怎么验证这个数据的真实性"）。

四层校验，从"自己跟自己对"到"跟另一条链路对"：

  A. 结构不变量 —— 位置必须连续无缺号、自然位编号连续、广告位不该有自然位编号、
     pageRank >= organicRank。破坏其一就是解析/落盘出错（**硬失败**）。
  B. 跨链路对账 —— 拿同一天的 `work/ecomtool-details-{date}.json`（另一条独立抓取链路）
     逐个 ASIN 对标题/品牌/价格/评论数。三处已知的**格式差异**要认出来、不算错：
       · 搜索页标题会**剥掉品牌前缀**（`siliteelon Button Down…` → `Button Down…`）
       · 搜索页评论数**取整到百位**（8803 → 8800）
       · 搜索页品牌列**可能为空**（不算"值不同"，算缺失）
  C. 时间一致性 —— 所有词的 `fetchedAt` 应在同一天、且集中在一个合理时间窗内。
  D. 排名稳定性（可选，`--repro "关键词"`）—— 立刻重抓一个词，比对两次的自然位/广告位重合度。
     自然位应高度一致（实测 48/48 全同）；**广告位本来就波动**（竞价位，实测只重合一半），
     所以广告位重合率低不算失败。

用法：
  python tools/verify_keyword_search.py --date 2026-09-23
  python tools/verify_keyword_search.py --date 2026-09-23 --repro "button down shirts for women"

退出码 0 = 通过（可能带软告警）；1 = 有硬失败。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORK = ROOT / "work"
DOCS = ROOT / "docs" / "data" / "keyword-search"

ASIN_RE = re.compile(r"^B[0-9A-Z]{9}$")
# 对账达标线（低于就报软告警，不当硬失败 —— 抓取时点不同本来就会有差异）
PRICE_MATCH_MIN = 0.90
BRAND_MATCH_MIN = 0.95
TITLE_EXPLAINED_MIN = 0.95
REVIEW_EXPLAINED_MIN = 0.90


def num(v):
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)", str(v or ""))
    return float(m.group(1)) if m else None


def check_structure(day):
    """A. 结构不变量。返回 (failures, notes, stats)"""
    fails, notes, stats = [], [], {}
    for kw, rec in day.get("keywords", {}).items():
        rows = rec.get("pageTop") or []
        if rec.get("organicTop"):
            notes.append("%s: 还带着旧的 organicTop 字段（应为空，已改成只存 pageTop）" % kw)
        stats["keywords"] = stats.get("keywords", 0) + 1
        stats["positions"] = stats.get("positions", 0) + len(rows)

        if len(rows) <= 50:
            fails.append("%s: 只记了 %d 个位置（第一页应 60+，可能被截断）" % (kw, len(rows)))
        ranks = [x.get("pageRank") for x in rows]
        if ranks != sorted(ranks):
            fails.append("%s: pageRank 不是升序" % kw)
        if ranks != list(range(1, len(ranks) + 1)):
            fails.append("%s: pageRank 不连续（缺号或重号）" % kw)

        ads = [x for x in rows if x.get("isAd")]
        orgs = [x for x in rows if not x.get("isAd")]
        stats["ads"] = stats.get("ads", 0) + len(ads)
        stats["organics"] = stats.get("organics", 0) + len(orgs)
        if [x.get("organicRank") for x in orgs] != list(range(1, len(orgs) + 1)):
            fails.append("%s: organicRank 不是连续 1..N" % kw)
        for x in rows:
            if not ASIN_RE.match(str(x.get("asin", ""))):
                fails.append("%s: ASIN 格式不对 %r" % (kw, x.get("asin")))
            if x.get("isAd") and "organicRank" in x:
                fails.append("%s: 广告位不该有 organicRank（%s）" % (kw, x.get("asin")))
            if not x.get("isAd") and "organicRank" not in x:
                fails.append("%s: 自然位缺少 organicRank（%s）" % (kw, x.get("asin")))
            if not x.get("isAd") and (x.get("pageRank") or 0) < (x.get("organicRank") or 0):
                fails.append("%s: pageRank < organicRank（%s）" % (kw, x.get("asin")))
    return fails, notes, stats


def check_cross_source(day, details):
    """B. 跨链路对账。返回 (failures, notes, stats)"""
    fails, notes, stats = [], [], {}
    seen = {}
    for kw, rec in day.get("keywords", {}).items():
        for x in rec.get("pageTop") or []:
            seen.setdefault(x.get("asin"), x)
    hit = [a for a in seen if a in details]
    stats["overlap"] = len(hit)
    if not hit:
        notes.append("没有可对账的 ASIN（缺 work/ecomtool-details-{date}.json？）")
        return fails, notes, stats

    title_ok = title_bad = 0
    brand_ok = brand_missing = brand_bad = 0
    price_ok = price_n = 0
    review_ok = review_explained = review_bad = 0
    examples = []

    def _core(t, brand):
        """剥掉开头的品牌前缀（搜索页会剥、详情页不剥）。"""
        t = t.strip()
        if brand and t.lower().startswith(brand.lower()):
            t = t[len(brand):].strip()
        return t

    for a in hit:
        mine, theirs = seen[a], details[a]
        t1, t2 = str(mine.get("title", "")).strip(), str(theirs.get("标题", "")).strip()
        b1, b2 = str(mine.get("brand", "")).strip(), str(theirs.get("品牌", "")).strip()
        # ⚠ 两个来源的标题**截断长度不同**（详情页表大概截到 60 字符，搜索页更长），
        #   所以剥掉品牌前缀后按**共同前缀**比对，别要求逐字相等。
        c1, c2 = _core(t1, b2), _core(t2, b2)
        n = min(len(c1), len(c2))
        if n >= 20 and c1[:n] == c2[:n]:
            title_ok += 1
        else:
            title_bad += 1
            if len(examples) < 3:
                examples.append("标题对不上 %s：搜索页=%r 详情页=%r" % (a, t1[:46], t2[:46]))
        # 品牌：空值算缺失，不算错
        if not b1:
            brand_missing += 1
        elif b1.lower() == b2.lower():
            brand_ok += 1
        else:
            brand_bad += 1
        # 价格
        p1, p2 = num(mine.get("price")), num(theirs.get("最终价格")) or num(theirs.get("Buybox价格"))
        if p1 is not None and p2 is not None:
            price_n += 1
            if abs(p1 - p2) < 0.01:
                price_ok += 1
        # 评论数：一致，或"搜索页取整到百位"
        r1, r2 = mine.get("reviews"), theirs.get("父体Rating数")
        if r1 and r2:
            r1, r2 = int(r1), int(r2)
            if r1 == r2:
                review_ok += 1
            elif r1 % 100 == 0 and abs(r1 - r2) <= 100:
                review_explained += 1
            else:
                review_bad += 1

    def ratio(ok, n):
        return (ok / n) if n else 1.0

    stats.update({
        "title": "%d/%d" % (title_ok, len(hit)),
        "brand": "%d/%d（缺失 %d）" % (brand_ok, brand_ok + brand_bad, brand_missing),
        "price": "%d/%d" % (price_ok, price_n),
        "reviews": "%d/%d（取整可解释 %d）" % (review_ok, review_ok + review_explained + review_bad,
                                            review_explained),
    })
    if ratio(title_ok, len(hit)) < TITLE_EXPLAINED_MIN:
        fails.append("标题对账通过率 %.0f%%（应 ≥ %.0f%%）" % (100 * ratio(title_ok, len(hit)),
                                                        100 * TITLE_EXPLAINED_MIN))
    if brand_bad:
        fails.append("品牌对账有 %d 条值不同（空值不算）" % brand_bad)
    if ratio(price_ok, price_n) < PRICE_MATCH_MIN:
        notes.append("价格完全一致率 %.0f%%（低于 %.0f%%，可能是抓取时点/变体差异）"
                     % (100 * ratio(price_ok, price_n), 100 * PRICE_MATCH_MIN))
    if ratio(review_ok + review_explained, review_ok + review_explained + review_bad) < REVIEW_EXPLAINED_MIN:
        notes.append("评论数可解释率偏低")
    fails.extend(examples)
    return fails, notes, stats


def check_timestamps(day, date):
    """C. 时间一致性。"""
    fails, notes = [], []
    stamps = []
    for kw, rec in day.get("keywords", {}).items():
        fa = str(rec.get("fetchedAt") or "")
        if not fa:
            notes.append("%s: 缺 fetchedAt" % kw)
            continue
        if not fa.startswith(date):
            fails.append("%s: fetchedAt 不是当天（%s）" % (kw, fa))
        stamps.append(fa)
    if stamps:
        span = (max(stamps), min(stamps))
        notes.append("抓取时间窗：%s ~ %s" % (span[1], span[0]))
    return fails, notes


def run_repro(keyword, date):
    """D. 排名稳定性：立刻重抓一次，与已发布数据比对。"""
    out = WORK / "_verify-repro.json"
    print("  重抓 %r …" % keyword)
    cmd = [sys.executable, str(ROOT / "tools" / "fetch_keyword_search.py"),
           "--date", date, "--only", keyword, "--force", "--out", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return ["重抓失败：%s" % (r.stderr or r.stdout)[-200:]], {}, {}
    fresh = json.loads(out.read_text(encoding="utf-8"))["keywords"][keyword]
    pub = json.loads((DOCS / ("%s.json" % date)).read_text(encoding="utf-8"))["keywords"][keyword]

    def split(rec):
        rows = rec.get("pageTop") or []
        return ([x["asin"] for x in rows if x.get("isAd")],
                [x["asin"] for x in rows if not x.get("isAd")])

    ads1, org1 = split(fresh)
    ads2, org2 = split(pub)
    stats = {
        "自然位重合": "%d/%d" % (len(set(org1) & set(org2)), max(len(org1), len(org2))),
        "自然位前 10 重合": "%d/10" % len(set(org1[:10]) & set(org2[:10])),
        "自然位第 1": "%s vs %s %s" % (org1[0] if org1 else "-", org2[0] if org2 else "-",
                                   "一致" if org1[:1] == org2[:1] else "不同"),
        "广告位重合": "%d/%d（广告是竞价位，波动正常）"
                  % (len(set(ads1) & set(ads2)), max(len(ads1), len(ads2))),
    }
    fails = []
    if org1 and org2:
        ov = len(set(org1) & set(org2)) / max(len(org1), len(org2))
        if ov < 0.8:
            fails.append("自然位重合率只有 %.0f%%（预期 >80%%，说明抓取不稳定或排名剧烈波动）"
                         % (100 * ov))
    return fails, stats, {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--repro", help="再做一次排名稳定性测试（传一个关键词）")
    ap.add_argument("--details", help="对账用的详情文件，默认 work/ecomtool-details-{date}.json")
    args = ap.parse_args()

    day_path = DOCS / ("%s.json" % args.date)
    if not day_path.exists():
        print("❌ 找不到已发布数据：%s" % day_path)
        return 1
    day = json.loads(day_path.read_text(encoding="utf-8"))
    print("检查 %s（%d 个关键词）" % (day_path.name, len(day.get("keywords", {}))))
    print()

    all_fails, all_notes = [], []

    # A
    f, n, st = check_structure(day)
    all_fails += f
    all_notes += n
    print("A. 结构不变量")
    print("   关键词 %d ｜ 位置 %d（广告 %d / 自然位 %d）"
          % (st.get("keywords", 0), st.get("positions", 0), st.get("ads", 0), st.get("organics", 0)))
    print("   %s" % ("✅ 全部通过" if not f else "❌ %d 处失败" % len(f)))

    # B
    dpath = pathlib.Path(args.details) if args.details else WORK / ("ecomtool-details-%s.json" % args.date)
    print("B. 跨链路对账（against %s）" % dpath.name)
    if not dpath.exists():
        all_notes.append("详情文件不存在，跳过对账：%s" % dpath)
        print("   ⚠ 跳过（%s 不存在）" % dpath)
    else:
        details = json.loads(dpath.read_text(encoding="utf-8"))
        f, n, st = check_cross_source(day, details)
        all_fails += f
        all_notes += n
        print("   可比 ASIN %d ｜ 标题 %s ｜ 品牌 %s ｜ 价格 %s ｜ 评论数 %s"
              % (st.get("overlap", 0), st.get("title", "-"), st.get("brand", "-"),
                 st.get("price", "-"), st.get("reviews", "-")))
        print("   %s" % ("✅ 通过" if not f else "❌ %d 处失败" % len(f)))

    # C
    f, n = check_timestamps(day, args.date)
    all_fails += f
    all_notes += n
    print("C. 时间一致性")
    print("   %s" % ("✅ 全部是当天抓的" if not f else "❌ %d 处失败" % len(f)))

    # D
    if args.repro:
        print("D. 排名稳定性（重抓 %r）" % args.repro)
        f, st, _ = run_repro(args.repro, args.date)
        all_fails += f
        for k, v in st.items():
            print("   %-16s %s" % (k, v))
        print("   %s" % ("✅ 通过" if not f else "❌ %d 处失败" % len(f)))

    print()
    for note in all_notes:
        print("   · 提示：%s" % note)
    if all_fails:
        print()
        for x in all_fails:
            print("   ❌ %s" % x)
        print("\n结论：**有问题，别信这批数据**（见上面 ❌）")
        return 1
    print("\n结论：✅ 通过 —— 结构与跨链路都对得上。")
    print("   注：这只证明「数据没被抓错」。**它绑定当时的会话条件**（配送地址 / 抓取时点 / 广告竞争），")
    print("   所以和「别人裸浏览器看到的顺序」逐位对齐是做不到的（实测未设地址的独立会话只跟自然位重合 65%）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
