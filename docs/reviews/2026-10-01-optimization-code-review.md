# AiWardrobe 优化分支代码审核与验收

> 历史审核：以下缺陷、代码行号与结论描述修复前工作区。R1～R8 后续已修复并通过本地复验，当前证据及生产环境待验证项见[修复复验报告](2026-10-01-optimization-fix-verification.md)。原始失败证据保留用于追溯。

**日期**：2026-10-01

**结论**：不合格，发布阻断。生产就绪评估 **40/100（Blocked）**；分数用于排列修复优先级，不代表数学测量。

**范围**：`codex/aiwardrobe-optimization` 工作区中的优化实现，包括未提交修改和新增文件；基线 `f2deaec`。

**标准**：[优化 Spec](../superpowers/specs/2026-10-01-aiwardrobe-optimization-spec.md)、[实施计划](../superpowers/plans/2026-10-01-aiwardrobe-optimization.md)及项目约定。

代码已包含持久任务、私有资产、决策历史、反馈、穿着和分页等基础能力，但默认购买流程会崩溃，旧入口绕过任务与配额，可查询供应商的恢复链路未实现，本地启动缺少 worker。不能认定“按照计划实现所有逻辑”已经验收完成。此前计划中的部分完成勾选过早，本次已按复现结果修正。

## 1. 验证证据

| 检查 | 本次结果 | 能证明的范围 |
|---|---|---|
| 后端 `python -m pytest -q` | **132 passed，142.16 秒** | 已有 SQLite/模拟 provider 测试通过 |
| 前端 `npm test -- --run` | **5 个文件、42 passed** | 已有 jsdom 行为测试通过 |
| 前端 `npm run build` | **通过** | TypeScript 与 Vite 生产构建通过 |
| 后端真实结构 → 前端诊断测试 | 捕获 `Cannot read properties of null (reading 'ideal')` | 成功响应足以使默认购买结果页崩溃 |
| 隔离数据库/模拟供应商复现 | 见 R2、R3、R4、R5、R7 | 验证绕过配额、无效重试、关联不一致、恢复失败和虚构组合 |

诊断测试以“捕获到缺陷”为断言，因此诊断通过不代表产品通过。复现使用临时 SQLite、临时图片和模拟 provider，没有调用真实付费服务；临时前端测试与响应夹具已清理，没有修改应用逻辑。

绿色测试不足以替代 Spec 验收：现有前端夹具把后端已允许为空的字段写成非空；`pendingGarment` 夹具实际使用 `review_status=confirmed`；供应商已受理后超时、人工关联后保存/删除等跨模块路径缺少测试。

## 2. 必须修复的问题

### R1 · P1：默认购买结果页不兼容后端 nullable 契约

**位置**：[App.tsx](../../frontend/src/App.tsx#L1558)、[types.ts](../../frontend/src/types.ts#L136)、[purchase_analysis.py](../../backend/app/purchase_analysis.py#L337)。

后端正常分析固定返回 `suggested_price=null`；衣橱不足或触发硬约束时，`score_breakdown`、`idle_risk_detail` 等字段也为空。前端仍把它们声明为必有值，并直接读取 `.ideal`、`.duplicate_risk` 和 `.level`。

**复现**：从后端图片分析函数取得有已确认衣橱的成功响应，将该 JSON 返回给默认商品 URL 分析页面。页面进入 React 错误边界，报错：

```text
Cannot read properties of null (reading 'ideal')
```

这条路径不依赖空衣橱，正常成功响应即可触发。空衣橱还会在更早的评分字段读取处失败。

**修复要求**：同步前端类型与实际 Pydantic 契约；为空时展示“依据不足”或隐藏对应值；使用后端返回结构覆盖正常、空衣橱、超预算、未知价格四条结果路径。

**验收影响**：AC-08、AC-19，默认购买主流程。

### R2 · P1：默认/旧分析入口绕过持久任务、幂等和供应商配额

**位置**：[purchase.py](../../backend/app/routers/purchase.py#L150)、[api.ts](../../frontend/src/api.ts#L415)、[App.tsx](../../frontend/src/App.tsx#L1471)。旧衣物上传入口也有同类直接调用。

默认购买页面仍调用 `/purchase/analyze` 和 `/purchase/analyze-image`，可恢复任务是用户额外展开的入口。旧路由直接请求 `AiService`，没有创建 `AnalysisTask`/`ProviderOperation`，没有接入供应商提交限额与容量预留，也没有 Spec §6.1 要求的有界同步适配。

**复现**：设置每小时供应商提交限额为 1，模拟真实 AI 服务，连续两次调用旧图片分析路径：

```json
{"configured_submission_limit":1,"provider_calls":2,"new_tasks":0,"provider_operations":0}
```

重复或并发请求可以避开新配额；请求中断后没有持久任务用于追踪和恢复。此处观察到的是绕过限额及缺少记录，未使用真实收费服务测试费用。

**修复要求**：把旧创建接口改成同一任务服务的兼容适配层，限制同步等待并返回任务引用；默认前端使用该可靠链路；验证旧路径重放、超时和配额行为。

**验收影响**：AC-03、AC-04、AC-07、AC-20、Spec §6.1。

### R3 · P2：重试接口接受取消/运行中任务，产生无效状态

**位置**：[tasks.py](../../backend/app/routers/tasks.py#L205)、[task_worker.py](../../backend/app/task_worker.py#L122)。

重试接口只拒绝成功和远端提交不确定状态，没有限制为可安全重试的失败任务。重新排队时保留 `cancel_requested` 和原租约，且缺少受锁保护的状态转换。

**复现**：

```json
{"case":"cancelled retry","returned_status":"queued","cancel_requested":true,"worker_claim":null}
{"case":"running retry","returned_status":"queued","original_lease_preserved":"active-worker"}
```

取消任务会显示排队但永远不被 worker 领取；运行中任务会在原 worker 仍持有租约时重新进入可领取队列。第二种情况存在执行重叠风险，本次未声称已复现重复收费。

**修复要求**：用行锁或原子条件更新约束状态，仅允许经过提交安全核验的失败任务重试；取消/运行中状态返回冲突；明确定义并清理合法重试的租约和取消状态。

**验收影响**：AC-03、AC-04、任务状态机。

### R4 · P2：人工关联已有衣物与保存/删除使用不同关联依据

**位置**：[decision.py](../../backend/app/routers/decision.py#L334)、[decision.py 保存](../../backend/app/routers/decision.py#L254)、[garments.py 删除](../../backend/app/routers/garments.py#L190)。

人工核对接口只写 `candidate.owned_garment_id`，不建立 `garment.source_candidate_id`，也未锁定衣物并检查它是否已绑定其他候选。保存及删除却只依赖后者。`legacy_unlinked` 也没有按契约阻止直接保存。

**复现**：先将旧候选关联到一件已确认衣物，再保存同候选，返回另一件新衣物，出现重复入库。另一隔离场景中，删除人工关联的衣物后：

```json
{"garment_exists":false,"ownership_state":"linked","dangling_owned_garment_id":"已删除衣物的ID"}
```

候选仍显示已关联，删除墓碑没有建立。前端禁用重复保存按钮不能保证 API 和旧客户端的数据一致性。

**修复要求**：选定唯一关联来源并一致读写，锁定双方、约束唯一绑定；已关联保存返回原衣物；旧候选要求先核对；删除同样维护墓碑。

**验收影响**：AC-11、Spec §6 候选所有权规则及旧数据迁移约束。

### R5 · P1：可查询供应商的已受理任务无法恢复或核验

**位置**：[task_worker.py](../../backend/app/task_worker.py#L939)、[runninghub.py](../../backend/app/runninghub.py#L197)、[tasks.py](../../backend/app/routers/tasks.py#L269)。ComfyUI 使用相同的封装方式。

RunningHub 返回的 `task_id`、ComfyUI 返回的 `prompt_id` 只留在客户端函数的局部变量。`ProviderOperation.provider_task_id` 没有写入路径。worker 把“上传 → 提交 → 轮询 → 下载”当作一次调用，发生超时后无法从已受理的远端任务继续；`reconcile` 对所有 unknown 操作直接返回不支持，没有按供应商查询能力分支。

**复现**：模拟 RunningHub 已返回 `accepted-remote-id`，随后输出轮询超时：

```json
{"remote_submits":1,"remote_task_id":"accepted-remote-id","stored_provider_task_id":null,"submission_status":"unknown","task_status":"running","task_stage":"awaiting_resolution","reconcile_result":"provider_reconciliation_unsupported"}
```

此场景远端 ID 已知且可以查询，本地仍丢失恢复依据；任务停在待核验，远端容量预留保持占用。有审计的运维释放预留不会补回结果恢复能力。

**修复要求**：拆开远端提交和轮询，收到 ID 后立即持久化；支持 worker 重启后的只读轮询和限频核验；只有确实不可查询的供应商才能返回不支持，不能盲目重新提交。

**验收影响**：AC-03、AC-04、Spec §5.1～§5.2。

### R6 · P2：前端把待确认识别结果当作已入库衣物

**位置**：[App.tsx 衣橱](../../frontend/src/App.tsx#L918)、[App.tsx 搭配](../../frontend/src/App.tsx#L2011)、[task_worker.py](../../backend/app/task_worker.py#L488)。

worker 返回 `status=ready, review_status=pending_review, user_confirmed_at=null`，这是合法的“任务完成、标签待审核”组合。前端仍以 `status=pending_review` 查待确认项，以 `status=ready` 标记已入库并加入手动搭配和目标单品列表。待确认提示因而遗漏，用户会选择后端不允许使用的衣物；后端确认条件会拒绝这些搭配请求。

**证据**：前后端字段与条件直接不一致；现有名为 `pendingGarment` 的前端夹具使用 `review_status=confirmed`，没有覆盖真实状态。本项以源码路径确认，未作完整浏览器视觉验收。

**修复要求**：区分处理完成与用户确认；前端可信衣物条件与后端一致，待确认列表使用 `review_status`，保留可发现的确认入口。

**验收影响**：G1、AC-09、AC-19。

### R7 · P2：购买搭配仍是占位模板，没有使用真实衣橱组合

**位置**：[purchase_analysis.py](../../backend/app/purchase_analysis.py#L498)、[覆盖度](../../backend/app/purchase_analysis.py#L101)。

`_outfit_ideas` 不接收衣橱数据，始终输出一个只有候选品类、空图片、没有资源 ID 的模板。无法满足候选 ID、用户确认衣物 ID、上衣/下装/鞋、季节和排除项校验。覆盖度也只检查衣橱非空，而非对应维度的证据是否足够。

**复现**：候选为上衣，用户仅有一个已确认包，结果仍是 `coverage.status=sufficient`，搭配潜力等维度全部标记可用；`outfit_ideas[0].items` 只有一个 `category=top, image_url=""` 的无 ID 项。

**修复要求**：从可信衣橱构造并校验真实组合，无有效组合允许零组；将证据不足标在相应维度，避免把模板当作个人组合。

**验收影响**：AC-09、Spec §7.3。

### R8 · P1：默认本地启动没有 worker，新上传永远排队

**位置**：[App.tsx 上传](../../frontend/src/App.tsx#L1225)、[start.bat](../../start.bat#L123)、[main.py](../../backend/app/main.py)。

前端上传已全部提交 v2 持久任务。`start.bat` 本地模式仅启动 API 和 Vite；API lifespan 不启动 worker。因此按 README 一键启动后上传，任务会排队而没有消费者，轮询不能完成。Docker 已配置 worker，不能证明本地默认启动路径可用。

**证据**：检查启动脚本、API lifespan 和上传调用路径，确认没有 worker 启动点；本次没有执行会安装依赖并打开交互终端的完整启动脚本。

**修复要求**：本地启动加入与 API 共享数据库、存储和 provider 配置的独立 worker，并有启动失败提示；覆盖默认启动后上传至待确认的端到端冒烟。

**验收影响**：AC-03、AC-19、M1 worker 集成与 README 快速开始。

## 3. 合格性判断

| 阶段 | 判断 | 主要原因 |
|---|---|---|
| M1：真实链路可靠 | **未通过** | R2/R3/R5/R8；PostgreSQL 验收未执行 |
| M2：购买决策闭环 | **未通过** | R1/R4/R7，可信衣物前端使用条件 R6 |
| M3：恢复和效率 | **未通过** | 默认入口契约和任务链路不完整；完整无障碍/视觉验收未执行 |
| 本地现有测试/构建 | **通过** | 132 后端测试、42 前端测试、生产构建 |
| 生产发布 | **阻断** | P1 问题影响核心流程、配额和恢复 |

本次没有取得 PostgreSQL 新库/旧库迁移及并发执行证据、真实供应商恢复/计费证据、全链路浏览器验收、读屏、375px 和 200% 缩放证据；也没有测量是否达到 80% 测试覆盖率。不能把这些项标记为已通过。本审核未调用外部代码扫描器，也未改变第三方资源。

## 4. 修复与复验顺序

1. 修复 R1，并建立后端响应到默认前端结果页的契约回归，先恢复可用的购买主流程。
2. 修复 R2/R8，使所有创建入口和默认启动实际连接同一持久任务链路。
3. 修复 R3/R5，验证远端已受理后超时、重启、取消和安全重试；只读核验不产生额外提交。
4. 修复 R4/R6/R7，验证人工关联、重复保存、删除墓碑、待确认衣物和真实组合约束。
5. 增补 PostgreSQL 集成与并发、真实 provider 只读恢复、本地启动端到端、键盘及移动/缩放验收，再运行全量测试和构建。

每项修复均须先保留失败用例再验证修复，不应通过改变 Spec 或用非空假响应掩盖缺陷。
