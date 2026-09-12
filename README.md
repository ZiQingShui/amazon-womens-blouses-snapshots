# Amazon 女式衬衫新品榜快照

公开看板每天保存美国站 `Women's Blouses & Button-Down Shirts` 新品榜 Top 100，支持按日期查看及与前一期对比。

## 数据结构

- `data/daily/YYYY/MM/YYYY-MM-DD.json`：不可变的每日快照
- `data/manifest.json`：日期索引与字段覆盖率
- `data/latest.json`：最近一期完整快照
- `data/status.json`：最近一次采集/发布状态

`dist` 与 `docs` 始终写入相同数据；`docs` 供 GitHub Pages 发布。

## 发布新快照

```powershell
python tools/publish_snapshot.py `
  --input path/to/enriched-products.json `
  --date 2026-09-12 `
  --captured-at 2026-09-12T08:30:00+08:00
```

发布器只接受名次 1–100 完整、ASIN 唯一、标题和图片齐全，并且商品链接与图片链接来自受信任 HTTPS 域名的数据。已存在的同日快照禁止覆盖；未通过校验时不会覆盖 `latest.json`，只会在 `status.json` 记录失败原因。

## 验证

```powershell
python -m unittest discover -s tests -v
```
