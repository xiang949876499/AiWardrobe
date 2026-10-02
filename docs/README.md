# 文档索引

## AiWardrobe 当前规范

- [MVP 产品规范](MVP_SPEC.md)
- [上传模式规范](upload-modes-spec.md)
- [项目运行与使用说明](../README.md)
- [系统架构](architecture.md)：API/worker/数据库/私有资产的职责与数据流。
- [v2 API guide](api-v2.md)：任务、候选、游标分页、反馈、穿着和私有图片访问。
- [部署与数据迁移](deployment-and-migrations.md)：生产 schema 升级、旧图片迁移和 worker 配置。

## AiWardrobe 优化与历史设计

衣橱优化与婚礼工作区已整合至本地 `main`；当前合并后审查、修复和验证状态以[main 整合审查](reviews/2026-10-02-main-integration-code-review.md)为准。2026-10-01 优化审核和 215/58 项测试是历史阶段证据；本地隔离 PostgreSQL 的双迁移与并发启动已另行验证。真实供应商、实际部署与完整无障碍验收仍需对应环境。

- [2026-10-02 本地验收补齐](reviews/2026-10-02-local-acceptance-gap-fixes.md)：完整173项前端、四项全局80%覆盖率门禁、此前跳过的实际媒体测试及独立复审。

- [2026-10-01 优化建议](optimization-proposal-2026-10-01.md)：参考项目与现有代码证据、路线比较和建议优先级。
- [2026-10-01 优化 Spec](superpowers/specs/2026-10-01-aiwardrobe-optimization-spec.md)：目标行为、数据模型、v2 接口、任务恢复、迁移与分阶段验收。
- [2026-10-01 优化实施计划](superpowers/plans/2026-10-01-aiwardrobe-optimization.md)：实现范围、测试结果及未执行的环境验证。
- [2026-10-01 代码审核与验收](reviews/2026-10-01-optimization-code-review.md)：修复前缺陷位置、隔离复现和 Spec 验收差距的历史证据。
- [2026-10-01 修复复验报告](reviews/2026-10-01-optimization-fix-verification.md)：八项修复、回归、真实浏览器冒烟与生产环境待验证项。
- [2026-06-22 全维度优化设计](superpowers/specs/2026-06-22-aiwardrobe-optimization-design.md)
- [购买分析设计](superpowers/specs/2026-06-16-purchase-analysis-design.md)
- [购物推荐设计](superpowers/specs/2026-06-16-shopping-recommendations-design.md)
- [购物推荐实施计划](superpowers/plans/2026-06-17-shopping-recommendations.md)
- [前端重设计计划](superpowers/plans/2026-05-28-frontend-soft-luxury-redesign.md)

## 婚礼 MV 产品与工作区

- [AI 婚礼 MV 产品 Spec](superpowers/specs/2026-10-01-ai-wedding-mv-design.md)：新人上传照片和爱情故事，生成90秒婚礼MV；包含模型选型、制作流程、预算、架构及验收。
- [AI 婚礼 MV 实施计划](superpowers/plans/2026-10-01-ai-wedding-mv-implementation.md)：早期演示阶段的历史实施记录。
- [AI 婚礼 MV 运行与接口说明](wedding-mv.md)：当前能力边界、启动方式、配置、用户流程、路由和验证命令；真实适配代码已实现，默认配置禁用外部模型。
- [AI 婚礼 MV 完成情况核查](wedding-mv-completion-audit-2026-10-01.md)：P0 修复矩阵、实测证据与真人商业验收边界。

- [婚礼 MV 缺口修复计划](superpowers/plans/2026-10-01-wedding-mv-gap-fixes.md)：自动创建、资源恢复、界面和验收工具的工程补齐。
- [十对新人验收清单示例](examples/wedding-acceptance-manifest.json)：默认离线，真实内测所需人工与费用证据由运营者准备。
- [验收工具操作指南](examples/wedding-acceptance-README.md)：阶段、参数、断点恢复、官方费用与人工证据格式。
