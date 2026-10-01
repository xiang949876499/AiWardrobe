# AiWardrobe 优化审核问题修复复验

日期：2026-10-01。分支：`codex/aiwardrobe-optimization`。范围：[原审核](2026-10-01-optimization-code-review.md) R1～R8 及修复中发现的跨模块问题。保留原工作区修改；未提交、推送或调用真实付费服务。

**结论：原八项问题已修复，本地回归与核心浏览器流程通过。** 这不等于全部生产验收通过；PostgreSQL、真实供应商和完整无障碍证据仍需在对应环境取得。

## 修复与失败用例

每项先保留复现失败，再修改业务逻辑。以下测试路径相对于仓库根目录；失败数量属于对应分项运行，不应相加为覆盖率。

| 问题 | 修复后的行为 | RED→GREEN / 回归位置 |
|---|---|---|
| R1：nullable 结果崩溃 | 总分、维度、风险与建议价缺失时显示依据不足；默认购买入口提交持久任务 | 初始 6 个前端失败用例涵盖 R1/R6/恢复；`frontend/src/App.test.tsx` 用四种后端生成的正常、空衣橱、预算冲突、未知价格契约 |
| R2：旧入口绕过任务/配额 | 旧购买/上传/推荐创建共用 worker、配额和幂等；同步等待 ≤20 秒，超时返回 503 与任务引用 | `backend/tests/test_legacy_task_adapters.py`：重放、跨 UTC 日期、长期 unresolved、单/批上传、推荐与限额；前端默认购买要求 v2 |
| R3：无效重试状态 | 原子失败状态转换，禁止 active/cancelled/unknown 重试；合法重试清理租约与取消标记，回执稳定重放 | R3/R5 初始 6 个失败；`backend/tests/test_task_recovery_review.py`、`test_v2_tasks.py` |
| R4：所有权不一致 | `source_candidate_id` 唯一规范关系；保存/关联/删除共用事务和锁顺序，已关联返回原衣物，旧数据需核对，删除保留墓碑 | 初始 8 failed / 1 passed，另有 3 个并发/旧关联失败；`backend/tests/test_candidate_ownership_review.py`、`test_candidate_reanalysis_concurrency.py`、`test_schema_migrations.py` |
| R5：已受理供应商无法恢复 | 接收 ID 后立即持久化；RunningHub/ComfyUI 重启后只读查询，30 秒限频/15 分钟窗口，人工 reconcile 返回 202；不重提 | `backend/tests/test_task_recovery_review.py`：split/tag、超时/重启、截止窗口、远端失败、取消清理、租约失效；`test_asset_gc.py` 保留恢复输入 |
| R6：待确认被当成可信衣物 | 前后端统一 ready + confirmed + 确认时间；待确认可发现，手动/目标/关联选择器只用可信衣物 | `frontend/src/App.test.tsx` 使用真实 ready/pending_review/null 确认时间，并验证显式确认；真实浏览器上传到确认通过 |
| R7：模板组合和虚假覆盖 | 从可信衣橱生成 ≤3 组真实候选+上衣/下装/鞋，验证归属/排除/季节，允许零组；分维度证据不足保持 null | `backend/tests/test_purchase_combinations_review.py`：资源/季节/排除/零组、包不足、规则版本和授权图片；400×400×400 反例及末尾有效资源验证搜索不会无效穷举或截断 |
| R8：本地没有 worker | 默认隐藏启动 API/worker/frontend，共享配置；预检查、失败提示、日志与进程身份保护停止入口 | `backend/tests/test_local_launcher.py`、`test_local_stop.py`；真实独立进程上传任务完成到待确认，实际停止三个服务通过 |

独立复审还发现并修复：保存与重新分析加锁顺序、恢复时旧任务/分页/账号迟到响应覆盖当前结果、组合笛卡尔积性能。补充回归发现并修复：取消后的迟到标签仍可能入库、恢复期间中间资产被回收、同场景多组搭配的重复 React key。历史快照保留规则版本与资源 ID，当前授权读取再生成短期图片 URL。

后端契约夹具可复现：从仓库根运行 `python backend/scripts/generate_purchase_contract_fixtures.py`。它调用确定性购买规则并经实际 `PurchaseAnalysisDetail` 校验，不访问数据库或 provider；输出供前端行为测试使用。

## 最终验证

| 检查 | 结果 |
|---|---|
| 后端 `python -m pytest -q` | **215 passed，170.86 秒** |
| 前端 `npm test -- --run` | **5 files / 58 passed，21.36 秒** |
| 前端 `npm run build` | **通过**，TypeScript + Vite |
| 后端 `python -m compileall -q app` | **通过** |
| `git diff --check` | **通过** |
| 浏览器核心流程 | **通过**，见下文 |

浏览器使用实际 Chromium、Vite、FastAPI 和独立 worker，隔离 SQLite/私有目录，显式 Demo。注册→空衣橱商品图片分析（null 结果正常显示）→刷新恢复最新任务→衣物上传→待确认→用户确认→候选保存→补齐下装/鞋→新候选与三件确认衣物的真实组合→删除关联衣物保留 removed 墓碑均通过。浏览器提交走 v2，页面及 console error 为零。

购买页在 375px 和 640px（1280px 的 200% 等效重排）分别测得文档宽度 375/640，无横向溢出；移动导航聚焦后 Enter 激活衣橱通过。截图与本地脚本保存在忽略的 `.superpowers/sdd/repair-2026-10-01/`，不作为产品资源。冒烟服务已用带进程身份校验的停止入口关闭。

## 验收边界

- PostgreSQL 服务/Docker 不可用：新库、旧库升级、并发配额/锁行为尚无 PostgreSQL 执行证据；SQLite 独立会话并发不能替代它。
- RunningHub/ComfyUI 恢复使用契约模拟。真实供应商成功、超时、查询与计费回报未验证；未产生付费调用。
- 375px 和等效重排覆盖本次购买页面；读屏、实际浏览器 200% 缩放及所有页面完整视觉验收未完成。
- 未测量 80% 测试覆盖率、Spec 的 P95/20 并发/2000 件负载指标，不能从绿色测试推断达标。

当前可认定原审核问题本地关闭。M1 的生产数据库/供应商证据与 M3 的完整无障碍退出条件仍未全部满足，见[实施计划](../superpowers/plans/2026-10-01-aiwardrobe-optimization.md)。部署、兼容错误码与恢复操作已同步到[API guide](../api-v2.md)、[架构](../architecture.md)和[部署说明](../deployment-and-migrations.md)。
