#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""按关键词抓 Amazon 前台搜索结果，**第一页所有位置都记，并标明广告位 / 自然位**。

数据来源：Ecomtool MCP 的 `amazon_get_search_result`（服务端抓取，没有登录态/个性化推荐，
等同于无痕搜索；比本地开无痕浏览器稳，也没有验证码问题）。
返回的「页面排名」是**广告位与自然位混排**的，这里统一存成一份 `pageTop`：
  · `pageTop` —— **第一页全部位置**（实测 60~63 个），买家翻页看到的顺序，含广告
  · 每条带 `isAd`（是否广告）与 `organicRank`（自然位第几名；**广告位没有这个字段**）
前端三种口径都从这一份派生：买家视角=全部 / 只看自然位=过滤 isAd / 只看广告位=过滤 !isAd。

⚠ 2026-09-23 改：原来只记「页面前 10 + 自然位前 5」，用户反馈「才 11 个位置，需要抓第一页的所有」。
  第一页广告实测就占 1~6 位，只取 10 个根本看不到自然位的全貌。

用法：
  python tools/fetch_keyword_search.py --date 2026-09-22 --all               # 跑完词表里的全部关键词
  python tools/fetch_keyword_search.py --date 2026-09-22 --limit 5           # 先试 5 个词
  python tools/fetch_keyword_search.py --date 2026-09-22 --only "womens blouses"
断点续跑：已抓过的关键词会跳过（除非加 --force），中断了直接重跑即可。
"""
from __future__ import annotations

import argparse
import html
import json
import pathlib
import re
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORK = ROOT / "work"
MCP_URL = "http://127.0.0.1/mcp/index.php"
POLL_INTERVAL = 4
POLL_TIMEOUT = 240
MAX_PAGE = 1              # 只抓第一页（第一页实测 60~63 个位置，全部记录）


def rpc_call(method, params, timeout=90):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode("utf-8")
    req = urllib.request.Request(
        MCP_URL, data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def submit_search(keyword, site="US"):
    """提交搜索抓取任务，返回结果链接。"""
    result = rpc_call("tools/call", {
        "name": "amazon_get_search_result",
        "arguments": {"keyword": keyword, "maxPage": MAX_PAGE, "site": site},
    })
    text = ""
    for block in result.get("result", {}).get("content", []):
        if block.get("type") == "text":
            text += block.get("text", "")
    m = re.search(r"http://127\.0\.0\.1/downloads/[^\s\n\)]+", text)
    if not m:
        raise RuntimeError("未取到结果链接：%s" % text[:300])
    return m.group(0)


def poll_result(url):
    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            if len(raw) > 2000 and "<table" in raw:
                return raw
        except Exception:
            pass
        time.sleep(POLL_INTERVAL)
    raise TimeoutError("轮询超时：%s" % url)


def cell_text(raw_cell):
    """单元格取文本；如果里面是 <img>，返回它的 src（图片ID 那列就是这样）。

    ⚠ 图片ID 列的真实内容是 `<img class="product-img" src="https://m.media-amazon.com/images/I/711jV3wmm8L.SR100,100.jpg">`，
    直接 `re.sub(r"<[^>]*>","")` 会把它清成空串（踩过：40 条全丢图片ID）。
    """
    m = re.search(r'<img[^>]+src="([^"]+)"', raw_cell, re.I)
    if m:
        return html.unescape(m.group(1))
    return html.unescape(re.sub(r"<[^>]*>", "", raw_cell)).strip()


def image_id(url_or_id):
    """从图片 URL 里取出图片 ID（711jV3wmm8L），前端自己按档位拼 URL。"""
    m = re.search(r"/images/I/([A-Za-z0-9+_-]+)", str(url_or_id or ""))
    return m.group(1) if m else str(url_or_id or "").strip()


def parse_table(raw):
    head = re.search(r"<thead[^>]*>(.*?)</thead>", raw, re.S | re.I)
    if not head:
        return [], []
    ths = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", head.group(1), re.S | re.I)
    headers = [html.unescape(re.sub(r"<[^>]*>", "", c)).strip() for c in ths]
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", raw, re.S | re.I):
        tds = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.S | re.I)
        if len(tds) != len(headers):
            continue
        rec = dict(zip(headers, [cell_text(c) for c in tds]))
        # ⚠ tbody 第一行是**重复的表头**（ASIN 那格写着 "ASIN"），必须跳过，
        # 否则它会被当成一条数据混进排名里（踩过：搜索结果数取到它，恒为 0）
        if not re.match(r"^B[0-9A-Z]{9}$", str(rec.get("ASIN", "")).strip()):
            continue
        rows.append(rec)
    return headers, rows


def to_int(v):
    try:
        return int(re.sub(r"[^\d]", "", str(v)) or 0)
    except Exception:
        return 0


def fetch_one(keyword, site="US"):
    """抓一个关键词，返回该词的记录（含页面排名前 N 与自然位前 N）。"""
    link = submit_search(keyword, site)
    headers, rows = parse_table(poll_result(link))
    if not rows:
        raise RuntimeError("表格解析为空")

    def pick(r):
        return {
            "pageRank": to_int(r.get("页面排名")),
            "asin": r.get("ASIN", ""),
            "title": r.get("标题", ""),
            "brand": r.get("品牌", "").replace("<br>", "").strip(),
            "price": r.get("价格", ""),
            "rating": r.get("评分", ""),
            "reviews": to_int(r.get("评论数")),
            "subSales": to_int(r.get("页面子体销量")),
            "isAd": "广告" in str(r.get("是否广告", "")),
            "imageId": image_id(r.get("图片ID", "")),
            "type": r.get("类型", ""),
        }

    items = [pick(r) for r in rows if r.get("ASIN")]
    items.sort(key=lambda x: x["pageRank"] or 10 ** 6)
    # 广告位与自然位分开编号：广告位只有页面位置，自然位另有「自然位第几」
    organic = [x for x in items if not x["isAd"]]
    for i, x in enumerate(organic, 1):
        x["organicRank"] = i          # 广告位没有这个字段（前端据此判断「广告」角标）
    result = {
        "keyword": keyword,
        "fetchedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": link,
        "resultCount": to_int(rows[0].get("搜索结果数")) if rows else 0,
        "perPage": to_int(rows[0].get("每页产品数")) if rows else 0,
        "totalPositions": len(items),
        "adCount": len(items) - len(organic),
        # 第一页**全部**位置（含广告），带 isAd / organicRank —— 前端三种口径都从这份派生
        "pageTop": items,
    }
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--keywords", default=str(ROOT / "config" / "keywords-core.json"),
                    help="关键词清单（默认 config/keywords-core.json —— 人工指定、参与 Git 同步；"
                         "work/keywords-core.json 那份是早期自动挑选的，已弃用）")
    ap.add_argument("--out", help="默认 work/keyword-search-{date}.json")
    ap.add_argument("--site", default="US")
    ap.add_argument("--limit", type=int, default=0, help="只抓前 N 个（用于试点）")
    ap.add_argument("--all", action="store_true", help="跑完清单里的全部关键词")
    ap.add_argument("--only", action="append", default=[], help="只抓指定关键词（可重复）")
    ap.add_argument("--force", action="store_true", help="已抓过的也重抓")
    args = ap.parse_args()

    doc = json.loads(pathlib.Path(args.keywords).read_text(encoding="utf-8"))
    plan = doc["keywords"]
    if args.only:
        want = set(args.only)
        plan = [k for k in plan if k["keyword"] in want]
    elif args.limit and not args.all:
        plan = plan[: args.limit]

    out = pathlib.Path(args.out) if args.out else WORK / ("keyword-search-%s.json" % args.date)
    existing = {}
    if out.exists() and not args.force:
        try:
            existing = json.loads(out.read_text(encoding="utf-8")).get("keywords", {})
        except Exception:
            existing = {}

    print("计划抓取 %d 个关键词（已完成 %d 个）" % (len(plan), len(existing)))
    done, failed, t0 = dict(existing), [], time.time()
    for i, item in enumerate(plan, 1):
        kw = item["keyword"]
        if kw in done and not args.force:
            # ⚠ 缓存必须**辨日期**：只看"关键词在不在"，会把昨天的结果直接当今天的发布
            stamp = str((done[kw] or {}).get("fetchedAt") or "")[:10]
            if stamp == args.date:
                continue
            print("  [%d/%d] %-42s 缓存是 %s 的数据，重抓" % (i, len(plan), kw[:42], stamp or "未知日期"))
            done.pop(kw, None)
        try:
            rec = fetch_one(kw, args.site)
        except Exception as e:
            failed.append({"keyword": kw, "error": str(e)[:200]})
            print("  [%d/%d] %-42s ✗ %s" % (i, len(plan), kw[:42], str(e)[:80]))
            continue
        rec["monthlyVolume"] = item.get("monthlyVolume")
        rec["abaRank"] = item.get("abaRank")
        done[kw] = rec
        first_organic = next((x for x in rec["pageTop"] if not x["isAd"]), None)
        print("  [%d/%d] %-42s ✓ 第一页 %d 个位置（广告 %d / 自然位 %d）/ 自然位第 1 = %s"
              % (i, len(plan), kw[:42], rec["totalPositions"], rec["adCount"],
                 rec["totalPositions"] - rec["adCount"], first_organic["asin"] if first_organic else "-"))
        # 每抓一个就落盘，中断不丢进度
        out.write_text(json.dumps({
            "schemaVersion": 1, "date": args.date, "site": args.site,
            "source": "Ecomtool amazon_get_search_result（服务端抓取，等同无痕）",
            "rankRule": "pageTop = 第一页**全部**位置（含广告），每条带 isAd / organicRank",
            "keywords": done,
        }, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n完成 %d / %d，失败 %d，用时 %.0f 秒（平均 %.1f 秒/词）"
          % (len(done), len(plan), len(failed), time.time() - t0,
             (time.time() - t0) / max(1, len(done) - len(existing))))
    if failed:
        print("失败清单：")
        for f in failed:
            print("  %s → %s" % (f["keyword"], f["error"]))
    print("已写出", out)
    # ⚠ 有失败或缺口必须以非零退出码暴露（原来无条件 return 0，14 个词全失败也算成功）
    leftover = [p["keyword"] for p in plan if p["keyword"] not in done]
    if failed or leftover:
        print("✗ 失败 %d 个、缺 %d 个，本次数据不完整（已写的 %s 请勿直接发布）"
              % (len(failed), len(leftover), out.name), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
