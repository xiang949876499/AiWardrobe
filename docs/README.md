# 文档索引

## AiWardrobe 当前规范

- [MVP 产品规范](MVP_SPEC.md)
- [上传模式规范](upload-modes-spec.md)
- [项目运行与使用说明](../README.md)
- [系统架构](architecture.md)：API/worker/数据库/私有资产的职责与数据流。
- [v2 API guide](api-v2.md)：任务、候选、游标分页、反馈、穿着和私有图片访问。
- [部署与数据迁移](deployment-and-migrations.md)：生产 schema 升级、旧图片迁移和 worker 配置。

## AiWardrobe 优化与历史设计

优化实现保留在 `codex/aiwardrobe-optimization` 分支。2026-10-01 审核发现的 4 项 P1 和 4 项 P2 已修复并通过本地复验；PostgreSQL、真实供应商和完整无障碍验收仍待对应环境验证，状态以修复报告及实施计划为准。

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

## 独立产品研究

- [AI 婚礼 MV 产品 Spec](superpowers/specs/2026-10-01-ai-wedding-mv-design.md)：新人上传照片和爱情故事，生成90秒婚礼MV；包含官方资料、模型选型、制作流程、预算、架构及验收。仅为研究与设计，尚未实现，与衣橱业务独立。
