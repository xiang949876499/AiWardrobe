# 部署与数据迁移

这份说明覆盖衣橱与婚礼工作区的版本化 schema、私有图片迁移和两个后台 worker。生产 API 不会自动修改数据库；数据库、对象存储、API 和 worker 的配置必须保持一致。

## 首次部署

生产环境使用 PostgreSQL。配置 `ENVIRONMENT=production`、有效的 `DATABASE_URL`、至少 32 字节的随机 `JWT_SECRET`、私有对象存储凭证和实际启用的 AI/provider 凭证。`AI_DEMO_MODE` 只用于演示；生产识别失败会报告错误，不会伪造演示结果。

从可信密码管理器生成唯一的随机密钥（至少32字节），保存在部署密钥系统或未跟踪的 `.env` 中，设置 `JWT_SECRET`；不要复制公开字符串。`.env.example` 将该字段留空。Compose 使用必填插值，缺失或空值会在配置解析阶段报错，并将同一个值传给迁移、API和两个worker。生产校验同时拒绝旧 Compose 默认值与旧示例占位符，验证异常不显示输入值。开发/测试的显式行为保持独立。曾使用公开默认值的部署必须配置新密钥并重启所有消费者，旧 JWT 会话随之失效；本说明不会自动生成或修改用户密钥。

Compose 还要求显式 `POSTGRES_PASSWORD`、`S3_ACCESS_KEY_ID` 和 `S3_SECRET_ACCESS_KEY`。两个密码分别使用唯一、至少32字节的随机值；`.env.example` 留空且没有公开回退。`POSTGRES_PASSWORD` 是原始密码，Settings 通过 SQLAlchemy URL 构造器编码写入连接串，支持 `@:/?#%` 等字符；不要手工把原文拼接到 URL。Compose 固定内部数据库主机 `postgres`、账号/库名 `aiwardrobe`，同一原始密码传给 PostgreSQL 与迁移/API/两个worker。MinIO server、应用和 bucket 初始化使用同一对显式 S3 凭证。`validate-config` 在启动 PostgreSQL/MinIO 前验证这些设置；不会输出解析后的凭证。

默认 PostgreSQL、S3 API 和 MinIO 控制台只在 Compose 内网提供服务。本地管理需要显式运行 `docker compose -f docker-compose.yml -f docker-compose.local.yml up --build`，附加文件仅将5432/9100/9101绑定到127.0.0.1，仍要求唯一凭证。不要把解析后的 `docker compose config` 输出写入公开日志。已有 PostgreSQL volume 的实际账号密码不会因修改初始化 `POSTGRES_PASSWORD` 自动轮换：需先备份并由操作者按现有部署完成数据库账号密码变更，再同步所有消费者。旧MinIO公开管理凭证也需由操作者轮换；本次仓库变更不操作现有服务、volume或凭证。

先运行 schema migrations，再启动 API 和 worker：

```powershell
cd backend
python -m app.migrations upgrade
python -m app.migrations current
python -m app.migrations verify
```

生产应用启动时要求数据库版本与当前代码一致。独立 worker 使用同一份数据库、存储和 provider 配置：

衣橱版本表 `schema_migrations` 当前为 **13**，婚礼版本表 `wedding_schema_versions` 当前为 **2**。一个 `upgrade` 命令在同一连接、同一事务及迁移锁中升级两套历史；PostgreSQL 使用 advisory lock，SQLite 使用 `BEGIN IMMEDIATE`。重复和并发升级均按锁顺序执行，失败回滚两套变更。`current` 保留衣橱版本查询接口，`verify` 验证两套版本及全部必需表/列。生产启动包括 `TESTING=true` 情况只验证，不执行 DDL。衣橱 version 13 创建候选保存/关联的 `CandidateOwnershipOperation` 幂等回执表；婚礼旧无时区列的来源配置见[婚礼指南](wedding-mv.md)。

```powershell
cd backend
python -m app.task_worker
# 在独立进程中启动婚礼任务消费者
python -m app.wedding_worker --poll-seconds 2
```

Docker Compose >=2.24 定义 `migrate`、`backend`、`worker` 和 `wedding-worker`。API 与两个 worker 都等待迁移成功，共用 `.env`、数据库和私有存储配置；两类素材分别存放在非公开 bucket。婚礼音乐目录只读挂载。分别监控两个 worker；停止期间任务留在数据库，重启后按租约恢复。

## 从旧版升级

升级前备份 PostgreSQL 和原图片存储。按以下顺序操作，并使用现网相同的 `DATABASE_URL`、`STORAGE_DRIVER`、bucket/目录和访问凭证：

1. 暂停旧版写入，或安排维护窗口。
2. 部署包含迁移工具的新代码，先执行 `python -m app.migrations upgrade`，再用 `python -m app.migrations current` 确认 schema 已到当前版本。
3. 执行图片迁移 dry-run：

   ```powershell
   python -m app.asset_migration
   ```

   检查 `scanned`、`skipped` 和 `failed`。dry-run 只读取和校验图片，不修改数据库或对象。

4. 确认源存储可读且失败列表为空后，执行迁移：

   ```powershell
   python -m app.asset_migration --apply
   ```

5. 再次执行 dry-run。只有 `scanned: 0` 且 `failed: []` 时，才启动生产 API；API 启动时也会拒绝仍引用旧公开图片键的记录。
6. 确认 API 和 worker 健康、任务可处理后再恢复流量。原备份和源对象保留到观察期结束。

迁移工具逐条创建私有原图/缩略图资产并更新 Garment、PurchaseCandidate 和 UploadSession 引用。失败记录会带记录类型、ID 和错误码；修复源对象或格式问题后重跑即可。图片文件、数据库和当前引用应一起纳入备份与恢复演练。

## 运行边界

- 开发环境允许 SQLite，schema migrations 会在应用启动时自动运行；生产环境只允许 PostgreSQL，并要求提前完成迁移。
- 私人图片从鉴权资产路由或短期签名 URL 提供。不要把旧 bucket/MinIO 公共读取策略重新打开，也不要将密钥放进仓库。
- v2 和旧版兼容创建入口共用持久任务、配额与 worker；默认购买页面使用 v2。旧同步入口最多等待 20 秒，未完成返回 `503 analysis_pending` 和任务引用。`GET /v2/tasks` 可查询账号自己的任务。
- Worker 可能在远端服务已受理、但本地未确认的情况下中断。此类任务进入 `awaiting_resolution`，需要先核验供应商状态；不要直接重复可能产生费用的提交。
- 迁移脚本提供前向升级，不提供自动降级。回退应用时仍需使用理解当前 schema、资产引用和任务状态的兼容版本。

## Windows 本地运行

`start.bat` 默认启动独立 API、衣橱 worker、婚礼 worker 和 Vite，使用同一份数据库、私有存储及 provider 配置。进程隐藏运行，日志和带进程创建时间的身份记录保存在根目录 `.local-services/`。可分别使用 `start.bat backend`、`start.bat worker`、`start.bat wedding-worker`、`start.bat frontend`；单独启动 API 时也必须启动相应 worker 才能消费任务。

运行 `start.bat stop-local` 停止已记录的本地服务。停止前会验证 PID 和进程创建时间；身份变化或旧版记录缺少身份时拒绝停止，避免 PID 被复用后结束其他进程。重复启动已记录服务会报错，先检查日志并停止旧服务。`start.bat stop` 仍用于 Docker Compose。

API/worker 启动前会检查导入和数据库初始化；任一子进程启动失败会提示对应日志并清理该次启动的进程。已有 Python 环境也可在 `backend/` 使用 `python -m app.local_launcher local` 和 `python -m app.local_launcher stop-local`。

## 供应商恢复排查

RunningHub/ComfyUI 已受理 ID 会持久保存。worker 重启后只读查询，间隔至少 30 秒；自动恢复最长 15 分钟。到期后用户可使用 `POST /v2/tasks/{id}/reconcile` 请求一次只读核验（返回 202），重复请求受 30 秒限频约束。没有可查询 ID 的 unknown 操作仍需供应商/运维证据，不能直接重复提交。

本地取消立即生效，但未核验终态的远端任务仍占供应商预留；只读核验完成后才释放。资产回收同样保留在途任务输入和拆分后待标签的私有资产。不要通过清空任务/资产表来解除占用；受限运维释放需审计证据。

本次本地验证与生产环境尚待执行项见[修复复验报告](reviews/2026-10-01-optimization-fix-verification.md)。
