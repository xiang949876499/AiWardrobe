# 婚礼 MV 工作区：运行与接口说明

婚礼 MV 是 AiWardrobe 中独立的产品工作区，入口名为“婚礼 MV”。它与原有衣橱数据和登录令牌分开保存素材与参与者会话。产品方案、模型比较和预算假设见[产品 Spec](superpowers/specs/2026-10-01-ai-wedding-mv-design.md)，实现顺序见[实施计划](superpowers/plans/2026-10-01-ai-wedding-mv-implementation.md)。

## 当前能力边界

- 支持创建项目、单次邀请 B 方、双方授权、按本人上传照片、手动填写爱情故事和事实、编辑并确认分镜、逐镜审核/拒绝候选、单镜头重做、接受照片回退、查看任务预算和状态、双方审核同一预览版本及下载成片。
- 新人故事和分镜目前由用户手动填写。服务端定义了故事、静帧、人物视频和场景视频 provider 接口，但尚未启用任何真实供应商适配器；填入 API key 不会接通模型。
- `WEDDING_MEDIA_MODE=demo` 只产生带“DEMO ONLY / NO AI MOTION”标记的占位画面。它不会生成新人形象或 AI 动态视频，不调用外部模型，也不模拟费用。FFmpeg 可用时会生成静音演示 MP4。
- `WEDDING_MEDIA_MODE=disabled` 是默认值。真实 provider 不可用时，任务会返回明确错误，不会假装生成成功。非演示成片还要求 FFmpeg 和持有适用授权的音乐文件及授权编号。
- 原始产品 P0 的真实模型接入、自动脚本/静帧、音乐库选择、版本回退等能力尚未完成；支付和对外收费属于 P1，竖版剪辑及 AI 原创配乐属于后续范围。逐项状态见[完成情况核查](wedding-mv-completion-audit-2026-10-01.md)。

## 本地运行

Windows 根目录的一键启动脚本会启动 API、前端和婚礼 worker；本地模式启用确定性 demo provider。要生成 MP4 预览，宿主机还需安装 FFmpeg 并将其加入 `PATH`，或设置 `WEDDING_FFMPEG_PATH`。

~~~powershell
.\start.bat
~~~

也可以单独启动 worker；它必须使用与 API 相同的数据库和存储配置：

~~~powershell
cd backend
python -m app.wedding_worker --poll-seconds 2
~~~

Docker Compose 会一并启动 `backend`、`wedding-worker`、PostgreSQL、MinIO 和前端：

~~~powershell
docker compose up --build
~~~

Compose 默认 `WEDDING_MEDIA_MODE=disabled`。本地演示时，在 `.env` 中将其设为 `demo`；演示成片带水印且静音。容器镜像包含 FFmpeg 和 Noto CJK 字体。Docker 镜像构建在当前执行环境未验证，因为这里没有 Docker CLI。

## 环境变量

婚礼相关变量列在仓库根目录的 [`.env.example`](../.env.example)。常用设置：

| 变量 | 默认值 / 用法 |
| --- | --- |
| `WEDDING_MEDIA_MODE` | `disabled`；本地水印演示用 `demo` |
| `WEDDING_ACCOUNT_BUDGET_LIMIT_FEN` | `15000`，账户级累计硬上限 ¥150 |
| `WEDDING_WORKER_STALE_SECONDS` | `1800`，用于恢复卡住的任务 |
| `WEDDING_S3_BUCKET` | `aiwardrobe-wedding-private`，必须与衣橱 `S3_BUCKET` 不同，且不可设置匿名读取策略 |
| `WEDDING_FFMPEG_PATH` | 留空则从 `PATH` 查找；Docker 镜像已安装 FFmpeg |
| `WEDDING_MUSIC_FILE` / `WEDDING_MUSIC_LICENSE_ID` | 非演示渲染必须同时提供已获授权的文件及授权编号 |
| `WEDDING_*_PROVIDER` / `WEDDING_*_API_KEY` / `WEDDING_*_MODEL` | 为未来适配器预留。当前无实时适配器；设置凭证不会触发外部调用 |

S3 模式下，衣橱图片桶可能被现有 MinIO 配置公开读取，因此婚礼素材使用独立 bucket。Compose 会创建私有 bucket，不对它设置匿名读取策略。部署到其他 S3 服务时，先创建该私有 bucket，并仅授予 API/worker 所需的读写删除权限。配置校验会拒绝婚礼与衣橱共用同一个 S3 bucket。

## 用户流程

1. A 方从“婚礼 MV”创建项目并确认本人肖像处理范围。
2. A 方创建单次邀请；B 方打开链接、勾选授权后加入。参与者令牌只保存在该浏览器的 `sessionStorage`，不会覆盖 AiWardrobe 的 `localStorage` 登录令牌。
3. 双方分别上传本人照片。只有双方同意后，服务端才接受照片素材。
4. A 方填写故事、事实及可编辑分镜模板（默认 18 镜头、总长 90 秒），确认后提交关键帧和生成任务。
5. 双方只审核与自己相关的候选和镜头；A 方可重做单个镜头或接受明确标记的照片回退。双方审核同一预览素材后，A 方才能确认交付并下载。

任何一方都可以撤回授权。撤回后，服务端取消未完成的生成和渲染、作废已批准成片，并拒绝继续读取项目素材或导出；再次授权后，旧成片仍保持作废状态。

目前上传只支持 JPEG、PNG 和 WebP。系统按已持久化的 `video/*` 资产统计动态镜头；占位 PNG 不会计入动态镜头。

## 主要 API

所有项目 API（接受邀请的 token 兑换接口除外）需要 `Authorization: Bearer <token>`。B 方 token 只作用于对应项目和本人数据。

| 用途 | 路由 |
| --- | --- |
| 服务能力 | `GET /wedding-capabilities` |
| 项目列表/创建/读取/修改/删除 | `GET/POST /wedding-projects`；`GET/PATCH/DELETE /wedding-projects/{id}` |
| 双方授权和邀请 | `POST /wedding-projects/{id}/self-consent`；`POST /wedding-projects/{id}/consent-invites`；`POST /consent-invites/{token}/confirm`；`POST /wedding-projects/{id}/consent/revoke` |
| 照片、故事和分镜 | `POST /wedding-projects/{id}/assets`；`GET /wedding-projects/{id}/storyboard`；`POST/PUT /wedding-projects/{id}/storyboard`；`POST /wedding-projects/{id}/storyboard/approve`；`POST /wedding-projects/{id}/frames/approve` |
| 镜头审核与重做 | `GET /wedding-projects/{id}/candidates`；`POST /participant-reviews/{shot_id}`；`POST /wedding-projects/{id}/shots/{shot_id}/regenerate`；`POST /wedding-projects/{id}/shots/{shot_id}/select-candidate`；`POST /wedding-projects/{id}/shots/{shot_id}/accept-fallback` |
| 生成和预算 | `GET /wedding-projects/{id}/quote`；`POST /wedding-projects/{id}/generation-jobs`；`GET /wedding-projects/{id}/generation-jobs`；`GET /wedding-generation-jobs/{job_id}`；`POST /wedding-projects/{id}/generation-jobs/{job_id}/cancel` |
| 成片与交付 | `POST /wedding-projects/{id}/render`；`GET /wedding-projects/{id}/renders`；`GET /wedding-projects/{id}/renders/{render_id}`；`POST /wedding-projects/{id}/render-reviews`；`POST /wedding-projects/{id}/approve`；`POST /wedding-projects/{id}/export` |
| 私有素材 | `GET /wedding-assets/{asset_id}`；`DELETE /wedding-projects/{id}/assets/{asset_id}`；`GET /wedding-projects/{id}/assets` |

服务端会检查项目所有者/参与者、素材归属、授权状态、版本和预算。项目中不保存可公开访问的对象 URL；浏览器通过受授权检查的素材读取接口获取私有内容。

## 验证

~~~powershell
cd backend
python -m pytest
cd ../frontend
npm test
npm run build
~~~

本地 worker 与 API 使用 SQLite 时必须指向同一个数据库文件。要验证真正的行级并发锁，还需在 PostgreSQL 环境单独运行并发集成测试；当前测试环境只有 SQLite。
