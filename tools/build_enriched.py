"""把「榜单名次 + Ecomtool 详情」合并成发布器要求的 enriched JSON。

输入：
- work/ecomtool-{date}-parsed.json  （榜单名次，含 ASIN/图片ID/标题/评论数/评分）
- work/ecomtool-details[-{date}].json （Ecomtool 详情，含品牌/价格/BSR/促销）

输出：work/enriched-{ranking}-{node}.json  （每份 100 条，符合 publish_snapshot.py 契约）

用法：
  python tools/build_enriched.py --date 2026-09-18
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
WORK = ROOT / "work"

IMAGE_BASE = "https://m.media-amazon.com/images/I/"
PRODUCT_BASE = "https://www.amazon.com/dp/"


def parse_bsr(rank_info: str) -> tuple[int | None, int | None, str | None, str | None]:
    """从「销量排名信息」解析主/子类目 BSR。

    格式示例：#80 in Women's Button-Down Shirts">#5,649 in Clothing, Shoes & Jewelry#80 in ...
    返回 (main_bsr, sub_bsr, main_category, sub_category)
    main = Clothing, Shoes & Jewelry（大类）
    sub = 具体类目（Button-Down Shirts / Blouses...）
    """
    if not rank_info:
        return None, None, None, None
    matches = re.findall(r"#([\d,]+)\s+in\s+([^#\"<>]+)", rank_info)
    main_bsr = sub_bsr = None
    main_cat = sub_cat = None
    for rank_str, cat in matches:
        cat = cat.strip()
        rank = int(rank_str.replace(",", ""))
        if "Clothing, Shoes & Jewelry" in cat or "Clothing" in cat and cat != "":
            # 大类（Clothing, Shoes & Jewelry）作为 main
            if main_bsr is None or "Shoes & Jewelry" in cat:
                main_bsr = rank
                main_cat = cat
        else:
            # 更具体的类目作为 sub
            if sub_bsr is None or rank < (sub_bsr or 10**9):
                sub_bsr = rank
                sub_cat = cat
    # 如果没有明显的 Clothing 大类，取排名最大的作为 main
    if main_bsr is None and sub_bsr is not None:
        # 找数值最大的排名作为 main
        all_matches = [(int(r.replace(",", "")), c.strip()) for r, c in matches]
        if all_matches:
            main_bsr, main_cat = max(all_matches, key=lambda x: x[0])
    return main_bsr, sub_bsr, main_cat, sub_cat


def parse_price(value: str) -> str:
    """价格转成 $XX.XX 格式。"""
    value = str(value or "").strip()
    if not value or value == "0":
        return "未显示/无法获取"
    try:
        num = float(value)
        return f"${num:.2f}"
    except ValueError:
        return value


def parse_promotions(detail: dict) -> list[str]:
    """从 Coupon 和促销折扣解析促销列表。"""
    promos = []
    coupon = str(detail.get("Coupon", "")).strip()
    discount = str(detail.get("促销折扣", "")).strip()
    if coupon and coupon != "0":
        promos.append(f"Coupon ${coupon}")
    if discount and discount != "0":
        promos.append(f"{discount} off")
    return promos


def build_item(rank_row: dict, detail: dict, snapshot_date: str) -> dict:
    asin = rank_row["asin"]
    image_id = rank_row.get("imageId", "")
    main_bsr, sub_bsr, main_cat, sub_cat = parse_bsr(detail.get("销量排名信息", ""))

    # 价格：优先最终价格，其次 Buybox 价格
    final_price = str(detail.get("最终价格", "")).strip()
    buybox_price = str(detail.get("Buybox价格", "")).strip()
    price_raw = final_price if final_price and final_price != "0" else buybox_price

    item = {
        "rank": rank_row["rank"],
        "asin": asin,
        "title": rank_row.get("title", detail.get("标题", "")),
        "image": f"{IMAGE_BASE}{image_id}._AC_SL1000_.jpg" if image_id else "",
        "url": f"{PRODUCT_BASE}{asin}",
        "brand": str(detail.get("品牌", "")).strip(),
        "price": parse_price(price_raw),
        "currency": "USD",
        "reviewCount": _to_int(rank_row.get("reviews")),
        "rating": _to_float(rank_row.get("rating")),
    }
    # 可选字段
    if main_bsr is not None:
        item["mainBsr"] = main_bsr
    if sub_bsr is not None:
        item["subBsr"] = sub_bsr
    if main_cat:
        item["mainCategory"] = main_cat
    if sub_cat:
        item["subCategory"] = sub_cat
        item["subRanks"] = [{"category": sub_cat, "rank": sub_bsr}]

    promotions = parse_promotions(detail)
    item["promotions"] = promotions
    if promotions:
        item["promotion"] = " + ".join(promotions)
        item["promotionStatus"] = "detected"
    else:
        # Ecomtool 明确返回 Coupon=0 且无折扣 → 确认无促销，而非未知
        item["promotion"] = "暂无促销"
        item["promotionStatus"] = "none"

    # 详情采集标记
    item["detailSource"] = "Ecomtool MCP 商品详情"
    item["detailStatus"] = "complete"
    item["detailAttempts"] = 1

    # 上架日期：详情表里没有，尝试留空（发布器不设下限）
    item["listingDate"] = "未显示/无法获取"

    # 图片来源标记
    item["imageSource"] = "Ecomtool MCP 商品详情；同一图片 ID 的高清版本"
    item["priceSource"] = "Ecomtool MCP 商品详情"

    return item


def _to_int(value) -> int:
    try:
        return int(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0


def _to_float(value):
    """评分转数字；无效值返回 None（与历史快照的数字类型契约对齐）。"""
    try:
        num = float(str(value).replace(",", "").strip())
        return num if 0 <= num <= 5 else None
    except (ValueError, TypeError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="快照日期，如 2026-09-18")
    args = parser.parse_args()
    snapshot_date = args.date
    parsed_path = WORK / f"ecomtool-{snapshot_date}-parsed.json"
    details_path = WORK / f"ecomtool-details-{snapshot_date}.json"
    if not parsed_path.exists():
        raise SystemExit(f"缺少榜单解析文件：{parsed_path}")
    if not details_path.exists():
        raise SystemExit(f"缺少详情文件：{details_path}")
    parsed = json.loads(parsed_path.read_text(encoding="utf-8"))
    details = json.loads(details_path.read_text(encoding="utf-8"))

    produced = []

    for ranking in ["new-releases", "bestsellers"]:
        for node, rank_rows in parsed[ranking]["by_node"].items():
            items = []
            missing_detail = []
            for row in rank_rows:
                asin = row["asin"]
                detail = details.get(asin)
                if detail is None:
                    missing_detail.append(asin)
                    continue
                items.append(build_item(row, detail, snapshot_date))

            items.sort(key=lambda x: x["rank"])
            out_path = WORK / f"enriched-{ranking}-{node}.json"
            out_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
            produced.append((ranking, node, len(items), len(missing_detail)))
            print(f"{ranking} / {node}: {len(items)} 条商品, 缺详情 {len(missing_detail)}")
            if missing_detail:
                print(f"  缺详情 ASIN: {missing_detail}")

    print("\n生成完成：")
    for ranking, node, count, missing in produced:
        print(f"  work/enriched-{ranking}-{node}.json ({count} 条)")

    # 汇总校验：每个文件应恰好 100 条
    print("\n=== 完整性校验 ===")
    ok = True
    for ranking, node, count, missing in produced:
        status = "✓" if count == 100 and missing == 0 else "✗"
        print(f"  {status} {ranking}/{node}: {count}/100")
        if count != 100 or missing:
            ok = False
    print("全部通过" if ok else "存在缺口！")


if __name__ == "__main__":
    main()
