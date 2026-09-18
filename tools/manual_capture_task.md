# 手动抓取任务执行说明

这个任务只由看板的“立即抓取”按钮触发。不要创建、恢复或启动任何定时抓取。

1. 先验证排名数据源。只有 Amazon 官方榜单，或明确返回该类目、榜单类型和名次的 Ecomtool MCP 结果，才可作为 1–100 名次依据。卖家精灵市场样本、Top400 和普通搜索结果不能补充缺失名次。
2. 按请求中的 Amazon 美国站类目节点和榜单类型调用 Ecomtool MCP，真实采集 Top 100；不得用旧数据或其他商品补位。若官方页面不足 100 名且 Ecomtool MCP 没有带名次的类目榜单接口，应立即按“缺失名次”失败。
3. 对每个 ASIN 至少执行一次 Ecomtool MCP 详情采集，逐项补齐产品图、品牌、标题、价格、促销、上架时间、BSR 大类与全部可见小类排名。缺失信息必须如实标记，并保留 `detailStatus` 与 `detailAttempts`。市场调研的“上架日期”不能冒充 Amazon 详情页的 Date First Available。
4. 运行现有校验，并用 `python published-dashboard/tools/check_snapshot_images.py <合并JSON> --min-height 300` 检查全部 100 张图片真实可访问且不是放大的 100px 缩略图。只有名次 1–100 完整、ASIN 唯一、标题和图片有效、100 个商品都完成详情采集，且详情来源为 Ecomtool MCP 时才发布。图片与导出 ID 不同时保留差异标记，以当前 Ecomtool 商品图为准。
5. 手动任务可用 `--replace-current-day` 修复或刷新当天快照，但不能覆盖其他日期。
6. 成功部署后运行：

   `python published-dashboard/tools/update_capture_request.py <请求ID> completed "已完成 Top 100 采集并更新看板"`

7. 登录、验证码、地区限制、Ecomtool 超时、缺失名次、图片无效或发布失败时运行：

   `python published-dashboard/tools/update_capture_request.py <请求ID> failed "具体失败原因"`

只在实际完成、遇到阻断或数据异常时通知用户。
