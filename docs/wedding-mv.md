# 婚礼 MV 工作区：运行与接口说明

婚礼 MV 是 AiWardrobe 的独立工作区。模型与产品方案见[产品 Spec](superpowers/specs/2026-10-01-ai-wedding-mv-design.md)，实现证据与商业验收边界见[完成核查](wedding-mv-completion-audit-2026-10-01.md)。

## 能力与模型

已实现：双方授权和私有素材、真实供应商适配器、异步故事与静帧任务、人物/场景视频任务、断点查询、预算和本地账单对账，以及照片质量/主参考图、逐项目音乐许可、不可变时间线和历史版本恢复、真实 FFmpeg 音画渲染与资源生命周期。界面提供草稿恢复、本人审核、素材准备、任务进度与历史版本操作。具体实测和外部验收状态以完成核查为准。

| 用途 | 固定模型 | 供应商 |
| --- | --- | --- |
| 故事整理与18镜头分镜 | doubao-seed-2-0-pro-260215 | 火山方舟 |
| 静态分镜 | doubao-seedream-4-5-251128 | 火山方舟 |
| 新人人物视频 | wan2.7-r2v-2026-06-12 | 阿里云百炼，北京 |
| 场景视频 | wan2.6-i2v-flash，audio=false | 阿里云百炼，北京 |

默认 `WEDDING_MEDIA_MODE=disabled`。`demo` 使用合成占位画面，始终带 DEMO ONLY / NO AI MOTION 标记，不调用外部模型、不代表人物一致性。`live` 需要有效凭证、双方对供应商的授权、明确的故事 token 价格和可用音乐许可。未配置或未知结果会明确停住，不会自动切换供应商或假装成功。

## 运行

生产使用 PostgreSQL；开发和测试可使用 SQLite。婚礼并发预算和资源配额验收使用 PostgreSQL，以验证实际行锁；API 与 worker 必须共享同一数据库、存储和环境变量。

```powershell
# 根目录：复制.env.example并配置 PostgreSQL/私有存储；不要提交真实凭证
cd backend
python -m pip install -e ".[dev]"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8031
# 另一个终端，使用完全相同的环境变量
python -m app.wedding_worker --poll-seconds 2
# 前端终端
cd frontend
npm ci
npm run dev
```

前端默认 `http://localhost:5174`；后端 `http://127.0.0.1:8031`。Docker Compose 使用前端5173、后端8000、PostgreSQL5432、MinIO控制台9101；容器带 FFmpeg 与中文字体，运行命令 `docker compose up --build`。此次没有 Docker CLI，未验证实际镜像构建。

`start.bat` 启动隐藏的 API、衣橱 worker、婚礼 worker 和 Vite 四个进程，共享环境文件中的配置；未配置时使用 SQLite 与本地存储，婚礼模式默认 disabled。日志和进程身份记录保存在 `.local-services/`，用 `start.bat stop-local` 安全停止。`start.bat wedding-worker` 可单独启动婚礼消费者。正式制作使用 PostgreSQL 与显式 provider 配置。

## 安全与成本

- B 的受限参与者会话保存在 sessionStorage，作用于一个项目和本人；衣橱账号令牌独立。双方本人确认身份，A 无权替 B 确认。
- 所有素材通过产品授权网关读取，逐次核查项目归属、当前授权、到期和成片状态；不给浏览器对象存储原始/签名 URL。婚礼 S3 bucket 与衣橱公开桶分开，禁止匿名读取。
- 撤销、删除和内容更改即时停止产品访问；远程对象清理可以重试。已经下载的文件或供应商已取走的素材不能远程收回。
- 每项目15000分，预留2000分用于交付；账号累计额度默认15000分。独立账号账本在删除项目后仍保留最小财务元数据，删除再建不能重置额度。
- 供应商 usage 与本地价格计算的是估算，官方账单才是财务金额。已受理 task_id 在重启后继续GET查询；UNKNOWN_SUBMISSION 持有预占、等待核对，禁止换新key自动重发收费POST。
- 对账保留 task/request/model/price/input-hash/consent元数据，不保留肖像、故事正文、提示词或供应商私有URL。

## 异步创建

所有项目接口需要 Bearer token；故事/静帧创建由所有者发起，B不能发起付费任务。

| 操作 | 请求 |
| --- | --- |
| 故事创建 | POST /wedding-projects/{id}/story-jobs，{version,story_text} |
| 静帧创建 | POST /wedding-projects/{id}/frame-jobs，{version,photos:[{shot_id,asset_id}]} |
| 创建进度 | GET /wedding-projects/{id}/creation-jobs |
| 取消创建 | POST /wedding-projects/{id}/creation-jobs/{job_id}/cancel |
| 分镜读取/保存 | GET /wedding-projects/{id}/storyboard，PUT同路由 |
| 人工分镜/静帧批准 | POST .../storyboard/approve，POST .../frames/approve |
| 视频生成/进度 | POST/GET .../generation-jobs |

创建 POST 必须提供非空 `Idempotency-Key`（最多128字符）；同key同输入回放，同key不同输入409。故事结果只引用输入原文的事实，返回18×5秒，事实仍需人工确认。静帧使用相关人物各自选定的主图，场景不传新人参考；照片镜头明确分配原图。

创建进度状态 queued/processing/completed/needs_action/cancelled；包含 completed_operations/total_operations、demo、error_code。排队后人工故事修改会得到 DRAFT_CHANGED，不覆盖用户草稿。更改静帧会得到 FRAMES_CHANGED；处理中不能批准静帧。未知收费提交没有安全的通用“再试一次”，须先查询/对账。

## 内部结构与故障处理

| 模块 / 持久数据 | 职责 |
| --- | --- |
| `wedding_providers.py`、`wedding_media.py` | 固定供应商协议、实际限时 HTTP 调用、媒体验证及显式 demo；官方模型访问权仍取决于账号 |
| `wedding_creation.py` / `WeddingCreationJob` | 故事、静帧持久队列；租约、草稿指纹与版本门禁 |
| `wedding_worker.py`、`wedding_video_operations.py` / `GenerationJob`、`WeddingBillingReceipt` | 视频提交前记录操作、回调 task_id、已知任务查询恢复、未知提交隔离 |
| `wedding_accounting.py` / `WeddingAccountUsage`、`WeddingBillingReceipt`、`BudgetLedger` | 账号与项目预占、结算及官方账单调整；项目删除不销毁最小财务记录 |
| `wedding_authoring.py`、`wedding_photo_quality.py` | 本人照片条件、主图变更的局部失效、音乐条款、不可变渲染快照及历史恢复 |
| `wedding_render_worker.py`、`wedding_render_media.py` / `RenderVersion` | 带 token 的租约、输入快照检查、确定性时间线、实际编码与解码检测 |
| `wedding_retention.py`、`wedding_cleanup.py` / `WeddingObjectCleanup` | 保留期、配额、上传暂存所有权、失败和到期对象的重试清理 |
| `routers/wedding.py`、`wedding_service.py` | owner/participant 权限、逐次授权媒体网关与本人审核 |

所有付费操作先在数据库事务中检查并预占预算，再关闭事务执行供应商或存储 I/O，结果持久化前重新检查授权、内容和租约。父项目先于子任务/渲染加锁，账号结算使用独立账本锁，避免删除、撤销与迟到结果之间的竞争。

worker 重启会恢复已知供应商任务和过期渲染租约。`PROVIDER_OUTCOME_UNKNOWN` 或丢失提交响应需要运营者用官方任务/账单核对，不能通过换 key 重新提交解决。`RENDER_HISTORY_INVALID` 表示旧版本没有完整快照，须新建符合当前门禁的渲染。`FINAL_DOWNLOAD_LIMIT` / `PREVIEW_BANDWIDTH_LIMIT` 是项目累计配额；切换历史版本不会解除限制。

清理对象只在上传者已结束且删除成功后释放存储占用。数据库或对象存储异常时，访问先被阻断，后台保留清理记录继续重试；无法证明上传者结束的记录保守保留，需运营核查。更新音乐文件前保留已交付授权对应的旧音频字节，否则历史重渲染会因哈希不一致失败。

## 正式账单对账

在 backend 目录执行：

```powershell
python -m app.wedding_accounting C:/absolute/path/bills.json
```

输入最多10000行JSON数组，如 `[{"task_id":"provider-task-or-request-id","cost_fen":31}]`，金额是非负整数分。相同金额重复导入无操作，不同金额重复导入报告conflict；未匹配、歧义、未结算待核对均明确报告。已结算估算按账单差额调整，即使差额揭示超预算。命令不触发供应商调用、不恢复已删除项目访问。

## 照片与主参考图

照片上传会实际解码，运行 OpenCV 人脸/眼睛检测及清晰度检查。正式制作要求每人至少3张本人可用原图、明确选定1张主参考图，并人工确认可用照片上的质量警告。检测只检查素材条件，人物身份仍由本人确认。损坏照片和多人合影不能作为个人主图；演示模式的无人脸合成图片带 `demo_bypass`，不能代表真实照片门禁通过。

照片检测在调用线程使用 CPU 路径，显式关闭该线程的 OpenCL 优先项；这避免了本次 Windows 后台线程人脸检测后的原生退出挂起。线程与异步请求的实际检测、正常退出、无人脸拒绝及损坏图片拒绝均有回归覆盖。

- `GET .../photo-readiness`：所有者读取双方准备情况，B只读取本人明细。
- `POST .../photos/{asset_id}/analyze-quality`：重新分析本人原图；`POST .../photos/{asset_id}/confirm-quality`，提交 `{confirmed:true}` 确认警告。
- `PUT .../participants/{A|B}/primary-photo`，提交 `{version,asset_id}`：每人选择本人主图。

更换主图保持同一故事内容版本，只清除依赖该人物的帧、候选和审核，并作废相关成片；其他镜头的实际素材ID和本人审核原样保留。界面应在发起新的模型任务前显示需重做镜头和费用提示。选择主图本身不会触发付费调用。

## 音乐、字幕与历史

`WEDDING_MUSIC_CATALOG_FILE` 指向运营者维护的 UTF-8 JSON 数组。每项包括 `track_id,title,path,license_id,license_version,valid_from,valid_until,perpetual_embedded_delivery:true,allowed_uses:["customer_embedded_delivery","wedding_playback"]`。路径是服务器文件，授权必须真实覆盖客户嵌入交付和婚礼播放。仅设置旧的音乐文件和许可编号不能授权正式渲染。

`GET /wedding-music` 只返回安全目录信息，`PUT .../music` 提交 `{track_id}` 为项目选择音乐。新选择要求目录有效期和文件有效；每个渲染记录锁定授权条款及音频哈希，历史成片的有效永久嵌入授权不会因目录下架消失。历史渲染重做前仍要校验音频字节没有变化。

`POST .../render` 支持 `{version,subtitles:{shot_id:"第一行\n第二行"},music_track_id?,source_render_id?}`。每行最多18个中文等宽单位，最多2行；不改变镜头顺序和90秒时长，也不重新调用生成模型。时间线、候选、授权和字幕是不可变快照。

`POST .../renders/{render_id}/restore` 提交 `{version}` 创建新的渲染版本。恢复仍要求素材、双方授权、原图来源和本人审核有效；不修改源版本。内容改版后的旧故事和旧镜头不能伪装成新版本，返回明确的 `RENDER_SOURCE_VERSION_STALE` / `TIMELINE_SHOT_VERSION_STALE`。下载可以用 `POST .../export?render_version_id={id}` 选择已批准的历史成片；未指定时必须是最新渲染已批准。

## 渲染、资源与保留期

渲染与资源规则：
- 固定18段/90秒，照片保留完整边界的慢运镜，中文字幕烧录，720p和1080p H.264/yuv420p/30fps，输入视频原音全部去除。正式配乐AAC48k双声道，解码后检测-16±1LUFS、truepeak≤-1dBTP；每个接收视频ffprobe及完整解码验真。
- 渲染默认120秒租约、最多3次尝试，持续心跳、过期恢复和token隔离，错误原因/次数可读。输入512MiB、输出每份256MiB、scratch768MiB、最长900秒、默认2线程；不可变历史缺失的旧记录报RENDER_HISTORY_INVALID。
- 保留期以实际owner批准交付后30天为固定截止；未交付项目按最近有效活动30天。状态轮询不延期，已交付读取/编辑不延长交付截止，已过期数据不复活。ProjectResponse公开retention_expires_at，AssetResponse公开expires_at。
- 2GiB包括素材、暂存、失效/过期但未删除的物理字节。先记录独立暂存所有权再写对象，失败可重试清理；只有确认writer结束后又成功删除才能释放占用，未知writer保守保留且需要运营核查。
- 项目累计10次实际final GET、其余私有媒体累计默认20GiB，跨版本/恢复共用。完整字节预扣，断连或读取失败不返还；export POST不计下载。HTTP429 FINAL_DOWNLOAD_LIMIT/PREVIEW_BANDWIDTH_LIMIT，存储不足409 PROJECT_STORAGE_LIMIT，过期媒体404。
- Optional harness backend/scripts/check_wedding_pg_s3.py，必须WEDDING_RUN_PG_S3=1及显式loopback设置文件；创建独立随机schema/privatebucket并finally清理。实际PG18.6+Moto5.2.3并发/锁序/撤回/删除/expiry重试通过；不代表真实MinIO/云部署策略已验证。

## 部署配置补充

故事模型必须配置两个明确的正数 `WEDDING_STORY_INPUT_FEN_PER_MILLION_TOKENS` / `WEDDING_STORY_OUTPUT_FEN_PER_MILLION_TOKENS`，以当前账号价格为准；静帧和视频价格同样只是本地预算估算，官方账单用于对账。生产启用前核实地域、模型访问权限和 CDN 域名。供应商协议资料见[万相 R2V](https://help.aliyun.com/en/model-studio/wan-video-to-video-api-reference)与[万相 I2V](https://help.aliyun.com/en/model-studio/legacy-image-to-video-api-reference/)，具体模型名与项目 Spec 保持一致。

API 与 worker 必须共同读取同一份运行环境文件、数据库和私有音乐目录。Compose 的音乐目录应以只读方式挂载；容器音乐目录 JSON 和每条音乐的 `path` 都使用容器内路径，不能直接填写 Windows 主机路径。Windows 本地使用 Microsoft YaHei；Linux 镜像提供 Noto CJK 字体。FFmpeg/ffprobe 路径也应按当前操作系统设置。

现有 PostgreSQL 的历史无时区列需要保持原先写入时的绝对时刻。若迁移前后数据库会话时区改变，运营者应配置 `WEDDING_LEGACY_TIMESTAMP_TIMEZONE` 为原写入时区，并在备份后显式执行 `python -m app.migrations upgrade`；不要把已有北京时间值直接当 UTC。迁移只对历史无时区列执行转换，采用 PostgreSQL 绑定表达式验证时区，支持命名时区和合法固定偏移。已经带时区的列不重新解释，也不校验未使用的覆盖值。实际 PostgreSQL 的北京时间、UTC 切换、固定偏移和重复迁移已验证；来源混杂或未知的历史时间仍需人工厘清。

Compose 至少使用2.24版，API和两个worker共享可选 `.env` 文件。额外故事价格、音乐目录和资源限制在文件中配置；显式 `environment` 字段优先于 `env_file`。容器默认用 Noto Sans CJK SC；Windows本地默认用Microsoft YaHei，可在需要时按运行环境覆盖。Docker路径配置示例：音乐主机目录 `WEDDING_MUSIC_DIRECTORY=./runtime/wedding-music`，容器目录 `WEDDING_MUSIC_CATALOG_FILE=/wedding-music/catalog.json`，曲目 `path=/wedding-music/your-licensed-track.wav`。共享只读目录不附带任何商业音乐授权。对应规则见[Compose环境变量文档](https://docs.docker.com/compose/how-tos/environment-variables/set-environment-variables/)。

## 显式生产迁移与恢复

在同一份数据库配置下，先于 API 和两个 worker 执行 `cd backend; python -m app.migrations upgrade`。衣橱版本表 `schema_migrations` 保持版本13，婚礼版本表 `wedding_schema_versions` 保持版本2，两者在同一事务内升级。PostgreSQL 在同一连接持有 advisory lock，SQLite 使用写事务序列化；重复执行安全。`python -m app.migrations verify` 只检查版本及必需表/列。生产启动只验证，不创建或修改表；未升级时拒绝启动。Compose 的 `migrate` 服务成功退出后才启动 API/worker。开发环境可自动初始化；生产即使设置 `TESTING=true` 也只验证。

取消、撤回或失效后的已知供应商任务继续由 worker 使用已保存任务 ID 做只读查询，直至得到终态，再结算一次；不会重新 POST、下载产物或恢复产品访问。未提交的排队任务释放预留并结束凭证。未知提交保留预留，不允许用新 key、浏览器或版本绕过。故事/静帧未知任务必须由运营者取得供应商终态证据后，用现有账务对账命令导入包含 `operation_key`、`terminal_state`（`succeeded`/`failed`/`not_submitted`）、`cost_fen`、`evidence_id` 的记录；`not_submitted` 只允许零费用。该能力没有浏览器接口，且不能替代外部查证。视频每个镜头输入修订最多三次尝试，已排队、未知和可能收费的尝试都计入，删候选不重置次数。

## 草稿、角色与制作界面

所有者编辑原始故事、事实或分镜后，1秒防抖自动保存，界面显示保存中、已保存或失败。浏览器本地恢复按账号、角色、项目与内容版本隔离；失败时可以复制草稿。轮询和晚到响应不能覆盖未保存编辑。批准或编排故事前先完成有效草稿保存，已有保存和并发调用共享一个完整保存队列。资料尚不完整时可提交原始故事让 AI 整理，不伪造事实或强行保存非法草稿。任务提交后真实的新编辑仍会触发 DRAFT_CHANGED，避免旧结果覆盖新故事。

所有者分镜响应中的 `draft_fingerprint` 用于可选的原始 `If-Match` 请求头，直接发送字段值，不添加 ETag 引号。草稿 POST、PUT 与批准在项目锁内比较；409 `DRAFT_CONFLICT` 需要加载或比对服务器版本，不自动覆盖。初次创建使用 JSON null 的指纹 `74234e98afe7498fb5daf1f36ac2d78acc339464f950703b8c019892f982b90b`。B 响应不提供所有者草稿指纹。

邀请链接使用 fragment 保存令牌。客户端先 `POST /consent-invites/preview`，JSON `{token}`，显示实际 `project_id`、人物、`provider_list`、`scope`、`purposes`、`terms_version`、`deletion`、到期时间及不可变 `disclosure_hash`；不返回私人故事或素材。本人确认使用 `POST /consent-invites/confirm`，JSON `{token, accepted, display_name, terms_version, disclosure_hash}`。旧路径确认仍要求相同 hash。新 API 请求不把 token 放入 URL；分享后妥善处理浏览器历史中的旧 query 链接。

会话丢失或 24 小时到期后，所有者点击“生成重新进入邀请”，调用 `POST /wedding-projects/{id}/access-invites`（无请求体）；B 本人预览并兑换七天内有效的一次性邀请。兑换旋转会话，旧令牌和重放失效；不重授授权、不修改原同意条款或精确素材审核。预览过期返回 `CONSENT_INVITE_EXPIRED`，重放返回 `CONSENT_INVITE_ALREADY_USED`，披露 hash 不匹配拒绝确认。

已确认的 B 会话不会代替衣橱账号令牌；同时持有两个会话时，通过“切换 B 方本人”或“切换项目所有者”明确操作身份。B 只看本人素材与本人涉及的镜头，不能代所有者保存故事或发起收费任务。项目、账号或角色切换立即隐藏上一个作用域的私有画面，晚到 blob 会被释放。

故事、静帧、视频任务显示实际进度与错误；未知提交禁用新的收费尝试，已知终止后的重试会提示可能新增费用。真实 MP4 候选使用视频播放器；DEMO 图片保留明确的占位标识。照片镜头的关键帧选择器支持双方有效原图，重新批准会从所选本人原图确定 A/B 范围并选中该原片；必须由该人物审核后才能渲染。换照片只清除该照片镜头的旧审核，并作废依赖它的成片。

已批准故事通过“修订事实与镜头”进入编辑，修改事实或镜头场景/动作后点击“创建修订版本”。界面提示受影响镜头需要重新审核和报价、再次模型调用会产生费用。`POST /wedding-projects/{id}/storyboard/revisions` 接收当前 `version`、完整 `story_text`、`facts` 和 `shots`（与草稿提交相同），`If-Match` 发送当前 `draft_fingerprint`。响应是下一版本草稿；旧批准版本保持不可变。新镜头的 `source_shot_id` 记录未变输入的直接来源，复用原始素材/候选 ID、原到期时间、审核和尝试次数；修改的镜头清除派生产物和审核。继续批准、生成静帧和视频只处理缺失/变更镜头。仅改展示标题不产生内容版本。新版本会清空旧的自动保存和媒体响应作用域。

场景镜头不能选带 A/B 人物来源的原图或人物生成帧。实际更换非照片关键帧会清除该镜头的候选、审核、选择及依赖成片，旧候选不能重选；原图与其他镜头保持。收费任务在派发前再次检查有效期、关键帧来源和每位人物的精确审核。LIVE 手工和自动路径都要求 18×5 秒、8 人物/4 场景/6 照片，且按实际固定供应商 MP4 验证至少 6 人物、10 AI 动态镜头及最多 2 个明确接受的原图回退；TESTING 不绕过这些检查。

历史列表明确选择渲染 ID；播放、本人审核、所有者批准、字幕编辑、恢复与导出均针对这个版本。切换历史版本清除未提交的字幕或音乐修改。作废、过期或缺失有效快照的版本保留状态信息，不能播放或导出。

## 验证与验收边界

前端使用 Node `>=24.14.0 <25` 和锁文件安装 `npm ci`。后端测试依赖包含独立的 HTTPX2 TestClient 支持，供应商客户端仍使用 HTTPX0.28.1。此次实测平台为 Windows/Python3.12/Node24.14；Docker、其他 Python 版本和现场播放设备应分别验证。

```powershell
cd backend
python -m pytest
# 可选真实媒体测试：先配置 WEDDING_FFMPEG_PATH / WEDDING_FFPROBE_PATH
# 具体 opt-in 变量见 tests/test_wedding_render_media.py
cd ../frontend
npm test
npm run build
npm audit
```

完整的合成产品链已通过公开产品 API 制作90秒720p/1080p MP4，并验证解码、音乐、字幕、私有导出和历史。浏览器检查使用真实 Vite/API，包括保存、两标签页 CAS 冲突、角色隔离、历史播放下载和照片重新分配。合成图片与内部测试音只建立工程证据；人物一致性、当前账号费用、真实音乐交付授权与婚礼设备效果没有由这些检查建立。

## 十对新人验收工具

`backend/scripts/wedding_acceptance.py` 默认只离线检查材料清单，不发送付费请求。先准备十个不同项目的成年新人、双方授权、实际可用原图/主图、批准分镜与静帧、真实音乐许可和固定模型；令牌只通过清单指定的环境变量读取，不写入清单、报告或断点文件。

```powershell
cd backend
python scripts/wedding_acceptance.py --manifest ../docs/examples/wedding-acceptance-manifest.json
python scripts/wedding_acceptance.py --help
```

[清单示例](examples/wedding-acceptance-manifest.json)与[人工及费用证据示例](examples/wedding-acceptance-evidence.json)是待填写的结构示例，不能作为通过数据。工具分为只读预检、生成、渲染、恢复和报告阶段；生成与渲染必须显式传入 `--execute-paid`，本次开发没有执行该开关。每阶段仍通过产品的授权/报价/预算/持久任务接口，不直接调用供应商。人工选择、本人身份审核和成片批准必须在产品中完成，工具不替新人确认。

提交前原子保存断点；提交响应丢失时停在未知状态，不改 key 重发。运营者核实已知 ID 后，工具还须验证实际项目、版本及候选目标范围，证据不足继续停住。费用验收使用官方总可变成本及音乐/制作/存储证据，usage 估算不能算预算通过。身份、事实正确性、评分和播放意愿缺失时保持 INCOMPLETE。正式门槛以原始 Spec11.2 为准，延迟 P50/P95 是目标而不是本次已测 SLA；替代供应商对比仍需人工研究。

完整命令、证据字段和恢复限制见[验收工具操作指南](examples/wedding-acceptance-README.md)。私有清单、断点和证据存放在已忽略的 `acceptance/` 目录或仓库外。退出码0只表示该阶段处理完成，结果仍可能是 INCOMPLETE 或 FAIL，必须读取 JSON 的 `status`；示例不会被算作真人验收通过。
