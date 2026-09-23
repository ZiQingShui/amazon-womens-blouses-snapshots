#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把抓好的关键词排名发布到看板数据目录。

  work/keyword-search-{date}.json  →  docs/data/keyword-search/{date}.json
                                  →  docs/data/keyword-search/manifest.json

按「一天一个文件」存（看板一次 fetch 拿到当天全部关键词，前端简单）；
manifest 里记日期列表，供看板做日期切换。

每个词的记录里只有**一份** `pageTop` = **第一页全部位置**（2026-09-23 用户："需要抓第一页的所有"），
每条带 `isAd`（是否广告）与 `organicRank`（自然位第几，广告位没有这个字段）。
前端三种口径（买家视角 / 只看自然位 / 只看广告位）都从这一份过滤得到。

用法：
  python tools/publish_keyword_search.py --date 2026-09-22
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORK = ROOT / "work"
DOCS = ROOT / "docs" / "data" / "keyword-search"
DIST = ROOT / "dist" / "data" / "keyword-search"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--src", help="默认 work/keyword-search-{date}.json")
    args = ap.parse_args()

    src = pathlib.Path(args.src) if args.src else WORK / ("keyword-search-%s.json" % args.date)
    doc = json.loads(src.read_text(encoding="utf-8"))
    kws = doc.get("keywords", {})
    if not kws:
        print("⚠ %s 里没有关键词数据" % src)
        return 1

    # 精简：只留看板要用的字段（source 只留文件名，省体积）
    # ⚠ **只发布一份 `pageTop` = 第一页全部位置**（2026-09-23 用户："需要抓第一页的所有"）。
    #   广告位与自然位都在这一份里，靠 `isAd` 区分、`organicRank` 给自然位编号，
    #   前端三种口径（买家视角 / 只看自然位 / 只看广告位）全从它派生 —— 不再单独存 organicTop，
    #   避免同一批数据存两份。
    def slim_item(x):
        out = {
            "pageRank": x.get("pageRank"),           # 页面第几位（含广告）
            "asin": x.get("asin"), "title": x.get("title"), "brand": x.get("brand"),
            "price": x.get("price"), "rating": x.get("rating"), "reviews": x.get("reviews"),
            "subSales": x.get("subSales"), "imageId": x.get("imageId"),
            "isAd": bool(x.get("isAd")),             # ← 前端据此打「广告 / 自然位」角标
        }
        if x.get("organicRank"):
            out["organicRank"] = x.get("organicRank")
        return out

    slim = {}
    for kw, rec in kws.items():
        # 老 work 文件（2026-09-23 之前）只有 organicTop，退化兼容一下
        rows = rec.get("pageTop") or rec.get("organicTop") or []
        slim[kw] = {
            "monthlyVolume": rec.get("monthlyVolume"),
            "abaRank": rec.get("abaRank"),
            "resultCount": rec.get("resultCount"),
            "perPage": rec.get("perPage"),
            "totalPositions": rec.get("totalPositions"),
            "adCount": rec.get("adCount"),
            "fetchedAt": rec.get("fetchedAt"),
            "pageTop": [slim_item(x) for x in rows],
        }

    day = {"schemaVersion": 1, "date": args.date, "site": doc.get("site", "US"),
           "rankRule": doc.get("rankRule", ""), "keywords": slim}

    for root in (DOCS, DIST):
        root.mkdir(parents=True, exist_ok=True)
        (root / ("%s.json" % args.date)).write_text(
            json.dumps(day, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # manifest：日期倒序 + 关键词元信息（看板用它渲染左侧词表）
    manifest = {"schemaVersion": 1, "site": "US", "updatedAt": args.date, "dates": [], "keywords": []}
    mf = DOCS / "manifest.json"
    if mf.exists():
        try:
            manifest.update(json.loads(mf.read_text(encoding="utf-8")))
        except Exception:
            pass
    dates = sorted(set(manifest.get("dates", []) + [args.date]), reverse=True)
    manifest["dates"] = dates
    manifest["updatedAt"] = args.date
    # 关键词元信息按当前这次为准（词表可能增减）
    manifest["keywords"] = [
        {"keyword": k, "monthlyVolume": v.get("monthlyVolume"), "abaRank": v.get("abaRank"),
         "organicTop1": (v.get("organicTop") or [{}])[0].get("asin", "")}
        for k, v in sorted(slim.items(), key=lambda kv: -(kv[1].get("monthlyVolume") or 0))
    ]
    blob = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))
    (DOCS / "manifest.json").write_text(blob, encoding="utf-8")
    (DIST / "manifest.json").write_text(blob, encoding="utf-8")

    size = (DOCS / ("%s.json" % args.date)).stat().st_size
    print("发布 %d 个关键词 → docs/data/keyword-search/%s.json（%.0f KB）" % (len(slim), args.date, size / 1024))
    print("manifest 日期：%s" % ", ".join(dates[:7]))
    print("同款样本 2026-09-22 的「同时上榜」思路：关键词里出现的 ASIN 可回查 docs/data/daily（前端做交叉）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
