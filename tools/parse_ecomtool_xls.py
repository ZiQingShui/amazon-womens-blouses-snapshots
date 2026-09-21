#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""解析 Ecomtool 导出的榜单 .xls（实为带 #result_table 的 HTML 表格）→ 结构化 JSON。

用法：
  # 自动扫 Downloads 里文件名含该日期的两份（bestsellers + new-releases）
  python tools/parse_ecomtool_xls.py --date 2026-09-21

  # 显式指定文件
  python tools/parse_ecomtool_xls.py --date 2026-09-21 --files "a.xls" "b.xls"

输出：work/ecomtool-{date}-parsed.json，结构为
  {"_meta": {"snapshotDate", "source", "files"},
   "bestsellers":  {"by_node": {"2368365011": [...], "2368383011": [...]}},
   "new-releases": {"by_node": {…}}}

每行字段：rank(int) / asin / imageId / title / price / reviews / rating /
         category / node / ranking('new-releases'|'bestsellers') / capturedAt(ISO8601+08:00)

要点（踩过的）：
  · 文件名里的节点顺序**不可靠**（出现过 `2368383011_2368365011` 与反向两种），
    节点一律取行内「节点」列（并用首个 td 的 id="node_XXX_result" 交叉校验）。
  · 文件名里的日期是 `2026-9-21`（无前导零），与 ISO 的 `2026-09-21` 不同，两种都要认。
  · 标题含 `&amp;` 之类实体，要 html.unescape。
"""
import argparse
import glob
import html
import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
NODES = ("2368365011", "2368383011")
RANKINGS = ("bestsellers", "new-releases")

HEADER = ["ASIN", "图片ID", "标题", "价格", "评论数", "评分", "类目", "节点", "排名", "抓取时间"]


def detect_ranking(filename):
    """从文件名识别榜单类型。"""
    low = filename.lower()
    for rk in RANKINGS:
        if rk in low:
            return rk
    return None


def to_iso(stamp):
    """'2026-9-21 08:58:56' → '2026-09-21T08:58:56+08:00'"""
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})[ T](\d{1,2}):(\d{2}):(\d{2})", stamp.strip())
    if not m:
        return stamp.strip()
    y, mo, d, h, mi, s = m.groups()
    return "%04d-%02d-%02dT%02d:%02d:%02d+08:00" % (int(y), int(mo), int(d), int(h), int(mi), int(s))


def parse_file(path):
    """解析一份导出文件 → (ranking, {node: [rows]})"""
    text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
    ranking = detect_ranking(os.path.basename(path))
    if not ranking:
        raise SystemExit("无法从文件名识别榜单类型（需含 bestsellers / new-releases）：%s" % path)

    body = re.search(r'<table[^>]*id="result_table".*?</table>', text, re.S)
    if not body:
        raise SystemExit("没找到 #result_table：%s" % path)

    by_node = {n: [] for n in NODES}
    total = 0
    for tr in re.findall(r"<tr>(.*?)</tr>", body.group(0), re.S):
        tds = re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)
        if len(tds) != len(HEADER):
            continue
        vals = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in tds]
        if vals[0] == "ASIN":          # 表头
            continue
        asin, image_id, title, price, reviews, rating, category, node, rank, captured = vals

        # 交叉校验：首列 td 的 id 里也带节点
        id_hit = re.search(r'id="node_(\d+)_result"', tr)
        if id_hit and id_hit.group(1) != node:
            print("  ⚠ 行内节点列(%s) 与 td id(%s) 不一致，以节点列为准：%s" % (node, id_hit.group(1), asin))

        if node not in by_node:
            raise SystemExit("出现未知节点 %s（只认 %s）" % (node, "/".join(NODES)))
        try:
            rank_int = int(rank)
        except ValueError:
            continue
        by_node[node].append({
            "rank": rank_int,
            "asin": asin,
            "imageId": image_id,
            "title": title,
            "price": price,
            "reviews": reviews,
            "rating": rating,
            "category": category,
            "node": node,
            "ranking": ranking,
            "capturedAt": to_iso(captured),
        })
        total += 1

    for n in NODES:
        by_node[n].sort(key=lambda r: r["rank"])
    print("  %-14s %s → %s" % (ranking, os.path.basename(path)[:38] + "…",
                               " / ".join("%s:%d" % (n, len(by_node[n])) for n in NODES)))
    return ranking, by_node, total


def discover(date_iso, downloads):
    """扫目录里文件名含该日期的文件（日期两种写法都认）。"""
    y, m, d = date_iso.split("-")
    tags = ["%s-%s-%s" % (y, int(m), int(d)), date_iso]     # 2026-9-21 与 2026-09-21
    found = []
    for f in glob.glob(os.path.join(downloads, "*.xls")):
        name = os.path.basename(f)
        if any(t in name for t in tags):
            found.append(f)
    return sorted(found)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="快照日期，如 2026-09-21")
    ap.add_argument("--files", nargs="*", help="显式指定 .xls；不传则自动扫 --dir")
    ap.add_argument("--dir", default=os.path.expanduser("~/Downloads"), help="自动发现目录")
    ap.add_argument("--out", help="输出路径，默认 work/ecomtool-{date}-parsed.json")
    args = ap.parse_args()

    files = args.files or discover(args.date, args.dir)
    if not files:
        raise SystemExit("没找到 %s 的导出文件，请用 --files 指定" % args.date)

    print("解析 %d 个文件：" % len(files))
    result = {"_meta": {"snapshotDate": args.date, "source": "Ecomtool 榜单导出",
                        "files": [os.path.basename(f) for f in files]}}
    for f in files:
        ranking, by_node, _ = parse_file(f)
        if ranking in result:
            for n in NODES:                      # 同榜单多文件时合并
                result[ranking]["by_node"][n].extend(by_node[n])
        else:
            result[ranking] = {"by_node": by_node}

    # 校验：两个榜单 × 两个节点都要齐，且各 100 条
    problems = []
    for rk in RANKINGS:
        if rk not in result:
            problems.append("缺榜单 %s" % rk)
            continue
        for n in NODES:
            rows = result[rk]["by_node"].get(n, [])
            if len(rows) != 100:
                problems.append("%s / %s 有 %d 条（应为 100）" % (rk, n, len(rows)))
            ranks = [r["rank"] for r in rows]
            if ranks and ranks != list(range(1, len(ranks) + 1)):
                problems.append("%s / %s 名次不连续" % (rk, n))
            asins = [r["asin"] for r in rows]
            if len(set(asins)) != len(asins):
                problems.append("%s / %s 有重复 ASIN" % (rk, n))

    out = pathlib.Path(args.out) if args.out else ROOT / "work" / ("ecomtool-%s-parsed.json" % args.date)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    print()
    print("已写出 %s" % out)
    print("  bestsellers  %s" % " / ".join("%s:%d" % (n, len(result["bestsellers"]["by_node"][n])) for n in NODES))
    print("  new-releases %s" % " / ".join("%s:%d" % (n, len(result["new-releases"]["by_node"][n])) for n in NODES))
    if problems:
        print()
        for p in problems:
            print("  ⚠ %s" % p)
        return 1
    print("  校验通过（两榜单 × 两节点各 100 条、名次连续、ASIN 唯一）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
