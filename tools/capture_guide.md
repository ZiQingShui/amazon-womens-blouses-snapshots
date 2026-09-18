# 半自动采集 · Ecomtool 抓数清单

这是看板数据采集的第一步：用 Ecomtool MCP 抓取 Amazon 美国站某个类目的榜单 Top 100 与商品详情，产出标准 JSON，交给 `publish_snapshot.py` 发布。

## 目标类目

- 默认新品榜节点：`2368365011`（Women's Blouses & Button-Down Shirts）
- 新增类目可用 `--node` 指定（如 `2368383011`）

## 采集要求（与发布器校验口径一致）

1. **榜单名次**：必须是 Amazon 官方榜单（或 Ecomtool 返回带名次的类目榜单），真实 1–100 名，不得用旧数据、卖家精灵市场样本、Top400 或普通搜索结果补位。不足 100 名且无带名次榜单接口 → 立即按「缺失名次」失败。
2. **逐项详情**：对每个 ASIN 至少执行一次 Ecomtool 详情采集，补齐：图片、品牌、标题、价格、促销、上架时间、BSR 大类 + 全部可见小类排名。
3. **来源如实标注**：缺失信息标「未显示/无法获取」而非编造；市场调研的「上架日期」不能冒充 Amazon 详情页 Date First Available。

## 产出 JSON 格式

一个数组，每项一个商品，字段如下（关键字段必填）：

```json
[
  {
    "rank": 1,
    "asin": "B0XXXXXXX",
    "title": "商品标题",
    "image": "https://m.media-amazon.com/images/...",
    "url": "https://www.amazon.com/dp/B0XXXXXXX",
    "brand": "品牌名",
    "price": "$24.99",
    "currency": "USD",
    "promotions": [],
    "promotionStatus": "none",
    "listingDate": "2026-07-06",
    "mainCategory": "Clothing, Shoes & Jewelry",
    "mainBsr": 363,
    "subCategory": "Women's Button-Down Shirts",
    "subBsr": 4,
    "subRanks": [{"category": "Women's Button-Down Shirts", "rank": 4}],
    "detailSource": "Ecomtool MCP 商品详情 + 市场调研",
    "detailStatus": "complete",
    "detailAttempts": 1
  }
]
```

字段说明：
- `rank`/`asin`/`title`/`image`：发布器强制，必须 100% 齐全
- `detailStatus`（complete/partial）+ `detailAttempts`（≥1）：必须标注，否则判为「未采集」
- `detailSource`：必须含「Ecomtool MCP」，否则判来源不合法
- `price`/`brand`/`mainBsr`/`subBsr`：字段覆盖率下限（brand/price ≥95%、BSR ≥90%）
- `image` 必须是受信任 HTTPS 域名（Amazon CDN 或本项目 GitHub Pages 托管图）

## 保存位置

存为 `work/enriched-<日期>.json`（`work/` 目录需自行创建）。然后执行发布：

```powershell
python tools/publish_snapshot.py `
  --input work/enriched-2026-09-17.json `
  --date 2026-09-17 `
  --captured-at 2026-09-17T08:30:00+08:00 `
  [--node 2368365011]
```

发布器会自动校验、落盘 docs+dist、重建 Worker bundle。校验不过会写 `status.json` 记录原因，不会覆盖 `latest.json`。
