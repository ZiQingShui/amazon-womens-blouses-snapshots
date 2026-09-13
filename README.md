# Amazon 女式衬衫新品榜快照

公开看板按类目保存美国站新品榜 Top 100，支持切换类目、按日期查看及与指定历史快照对比。热销榜入口仅在类目配置了独立热销榜数据源后启用，避免把新品榜数据误当成热销榜。

- `2368365011`：Women's Blouses & Button-Down Shirts
- `2368383011`：Women's Button-Down Shirts

## 数据结构

- `data/daily/YYYY/MM/YYYY-MM-DD.json`：不可变的每日快照
- `data/manifest.json`：日期索引与字段覆盖率
- `data/latest.json`：最近一期完整快照
- `data/status.json`：最近一次采集/发布状态
- `data/categories.json`：看板类目注册表
- `data/categories/<节点>/...`：新增类目的独立快照、索引与状态

`dist` 与 `docs` 始终写入相同数据；`docs` 供 GitHub Pages 发布。

## 手动添加类目

公开看板侧栏的 `＋ 新增类目` 可由管理员登录后直接新增节点。当前内置目录包含 Amazon 美国站一级类目和女装重点路径；未收录节点可手动输入。新类目会持久保存并显示为“等待首次采集”，普通访客没有修改权限。每日任务会遍历所有已登记类目，不再只抓默认节点。

也可以在本机运行以下命令，依次输入 Amazon 类目节点和英文类目名称：

```powershell
python tools/add_category.py
```

新类目会立即写入 `dist` 与 `docs` 的统一类目清单，并显示为“等待首次采集”。之后按下面的方式为该节点发布第一份 Top 100 快照即可。发布器会读取本地清单；如果节点由网页新增，则会自动读取公开看板的最新类目清单，不需要再修改代码。

## 发布新快照

```powershell
python tools/publish_snapshot.py `
  --input path/to/enriched-products.json `
  --date 2026-09-12 `
  --captured-at 2026-09-12T08:30:00+08:00
```

发布新增类目时指定节点：

```powershell
python tools/publish_snapshot.py `
  --input path/to/button-down-shirts.json `
  --date 2026-09-12 `
  --node 2368383011
```

发布器只接受名次 1–100 完整、ASIN 唯一、标题和图片齐全，并且商品链接与图片链接来自受信任 HTTPS 域名的数据。已存在的同日快照禁止覆盖；未通过校验时不会覆盖 `latest.json`，只会在 `status.json` 记录失败原因。字段完整率不会把“未显示/无法获取”等占位文字当成有效数据；多个促销会去重后全部保留。

## 验证

```powershell
python -m unittest discover -s tests -v
```
