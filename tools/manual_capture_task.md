# 手动抓取任务执行说明

这个任务只由看板的“立即抓取”按钮触发。不要创建、恢复或启动任何定时抓取。

1. 按请求中的 Amazon 美国站类目节点和榜单类型调用 Ecomtool MCP，真实采集 Top 100；不得用旧数据或其他商品补位。
2. 逐项补齐产品图、ASIN、品牌、标题、价格、促销、上架时间、BSR 大类与全部可见小类排名。缺失信息必须如实标记。
3. 运行现有校验；只有名次 1–100 完整、ASIN 唯一、标题和图片有效时才发布。
4. 手动任务可用 `--replace-current-day` 修复或刷新当天快照，但不能覆盖其他日期。
5. 成功部署后运行：

   `python published-dashboard/tools/update_capture_request.py <请求ID> completed "已完成 Top 100 采集并更新看板"`

6. 登录、验证码、地区限制、Ecomtool 超时、缺失名次、图片无效或发布失败时运行：

   `python published-dashboard/tools/update_capture_request.py <请求ID> failed "具体失败原因"`

只在实际完成、遇到阻断或数据异常时通知用户。
