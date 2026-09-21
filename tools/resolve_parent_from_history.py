#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""卖家精灵不可用时的备用链路：用「历史记录 + 同款兄弟借调」解析父体/上架日期。

背景：`fetch_market_analysis.py` 依赖卖家精灵账号，账号异常时表现为任务提交成功、
结果链接持续 404、轮询 5 分钟超时。此时改用本脚本：
  · 直接命中：今天要的 ASIN 以前抓过（合并全部 work/market-analysis*.json）
  · 兄弟借调：没抓过的 ASIN，用同变体组的兄弟记录借用（父体/上架日期是同款共享属性）
  · 变体组来自 work/child-asins-*.json（由 fetch_child_asins.py 抓，不依赖卖家精灵）

用法：
  python tools/resolve_parent_from_history.py --date 2026-09-21

输出：work/market-analysis-{date}.json，格式与 fetch_market_analysis.py 一致
      {asin: {"parentAsin", "listingDate", "variantCount", "salesDays", "brand"}}
"""
import argparse
import glob
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORK = ROOT / "work"


def load_history(exclude):
    """合并所有历史 market-analysis*.json（跳过本次的输出文件）。"""
    history, sources = {}, {}
    for f in sorted(glob.glob(str(WORK / "market-analysis*.json"))):
        if pathlib.Path(f).name == exclude:
            continue
        try:
            d = json.loads(pathlib.Path(f).read_text(encoding="utf-8"))
        except Exception as e:
            print("  ⚠ 跳过 %s（%s）" % (pathlib.Path(f).name, e))
            continue
        for k, v in d.items():
            if isinstance(v, dict) and (v.get("parentAsin") or v.get("listingDate")):
                history.setdefault(k, v)
                sources.setdefault(k, pathlib.Path(f).name)
    return history, sources


def load_groups():
    """合并所有 child-asins*.json → {asin: set(同组 asin)}"""
    group_of = {}
    for f in sorted(glob.glob(str(WORK / "child-asins*.json"))):
        try:
            d = json.loads(pathlib.Path(f).read_text(encoding="utf-8"))
        except Exception as e:
            print("  ⚠ 跳过 %s（%s）" % (pathlib.Path(f).name, e))
            continue
        for _key, val in d.items():
            members = val if isinstance(val, list) else (
                val.get("childAsins") or val.get("children") or val.get("asins") or [])
            members = [m for m in members if isinstance(m, str)]
            if not members:
                continue
            grp = set(members)
            for m in grp:
                group_of.setdefault(m, set()).update(grp)
    return group_of


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--asins", help="ASIN 列表，默认 work/{date}_all_asins.json")
    ap.add_argument("--out", help="默认 work/market-analysis-{date}.json")
    args = ap.parse_args()

    asin_file = pathlib.Path(args.asins) if args.asins else WORK / ("%s_all_asins.json" % args.date)
    out_file = pathlib.Path(args.out) if args.out else WORK / ("market-analysis-%s.json" % args.date)
    asins = json.loads(asin_file.read_text(encoding="utf-8"))

    history, sources = load_history(out_file.name)
    groups = load_groups()
    print("历史记录 %d 个 ASIN；变体组索引 %d 个 ASIN" % (len(history), len(groups)))

    resolved, direct, borrowed, missing = {}, [], [], []
    for a in asins:
        if a in history:
            resolved[a] = dict(history[a])
            direct.append(a)
            continue
        sib = groups.get(a)
        if sib:
            donor = next((s for s in sib if s in history), None)
            if donor:
                rec = dict(history[donor])
                rec["_borrowedFrom"] = donor
                resolved[a] = rec
                borrowed.append(a)
                continue
        missing.append(a)

    out_file.write_text(json.dumps(resolved, ensure_ascii=False, indent=1), encoding="utf-8")
    n = len(asins) or 1
    print()
    print("直接命中 %d（%.1f%%）· 兄弟借调 %d（%.1f%%）· 缺失 %d" % (
        len(direct), 100.0 * len(direct) / n, len(borrowed), 100.0 * len(borrowed) / n, len(missing)))
    print("总覆盖率 %.1f%%（%d/%d）" % (100.0 * len(resolved) / n, len(resolved), len(asins)))
    if missing:
        print("缺失清单（前 20）：%s" % ", ".join(missing[:20]))
        (WORK / ("_missing_parent_%s.json" % args.date)).write_text(
            json.dumps(missing, ensure_ascii=False), encoding="utf-8")
        print("  已写入 work/_missing_parent_%s.json" % args.date)
    print("已写出 %s" % out_file)

    # 抽查借调记录，确认字段完整
    if borrowed:
        a = borrowed[0]
        print("借调样例 %s <- %s : %s" % (a, resolved[a].get("_borrowedFrom"),
                                        json.dumps(resolved[a], ensure_ascii=False)[:160]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
