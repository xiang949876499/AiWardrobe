# 图片上传模式规范

## 目标

衣物图片支持单件识别和整套穿搭拆分。用户提交后先得到可恢复任务；识别结果进入待确认状态，经检查或修正后才成为可信衣橱内容。

## 模式

- **单件上传**：每张图片独立提交为 `garment_single` 任务，识别一件衣物。
- **自动拆分**：穿搭/多件图片作为 `garment_split` 任务，使用已配置的视觉拆分 provider 生成多个待确认结果。
- 浏览器入口统一做适用的 JPEG 压缩；后端仍会验证 MIME、格式、字节大小、像素数并生成私有缩略图。PNG 透明度在服务端预处理中保留。
- 多文件上传限制同时处理数量为 3；单项失败可单独重试。请求携带稳定 `Idempotency-Key`，任务状态在刷新后可恢复。

## 类别

`top`、`bottom`、`outerwear`、`dress`、`shoes`、`bag`、`accessory`。`bag` 与 `accessory` 分开；围巾、腰带、首饰和帽子归为 `accessory`。

## API

- 新客户端使用 `POST /v2/tasks/garment-upload`，multipart 字段 `file`、`mode=single|split` 和 `Idempotency-Key`；返回 `202` 与任务资源。
- 使用 `GET /v2/tasks/{task_id}` 查询阶段、阶段耗时、状态、结果引用及安全错误码。
- 用户可使用现有衣物编辑/确认接口检查结果；未确认的分析结果不得当成已核验衣物用于个人决策。
- 旧版 `/uploads/plain-garment`、`/uploads/garment-photo` 和 `/garments/batch-upload` 保留兼容；新功能应接入持久任务 API。

## 状态与失败处理

任务状态为 queued、running、succeeded、failed 或 cancelled；处理中展示阶段，结束后展示可用的操作/成本遥测和安全重试条件。provider 真实错误不能改写成 Demo 标签。远端提交 `unknown` 时保留任务并等待核验，不得盲目重复可能收费的调用。

请求限制和错误码以 [v2 API guide](api-v2.md) 为准；生产 worker 配置和历史资产迁移见[部署与数据迁移](deployment-and-migrations.md)。
