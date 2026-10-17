# 决策日志

| 编号 | 日期 | 决策 | 依据 | 影响 |
|---|---|---|---|---|
| D001 | 2026-10-15 | 核心采用离线优先架构，Azure adapter 为可选能力。 | 已批准的架构与模型路由要求。 | Core 测试不能依赖凭据。 |
| D002 | 2026-10-15 | 文件是可移植事实，SQLite 只作为可选状态/索引后端。 | Storage 设计与无 SQLite 要求。 | FileStorage 与 SQLiteStorage 共享同一合同。 |
| D003 | 2026-10-15 | 底层历史只追加，不删除 call、成本、资产或决定。 | 审计和崩溃恢复需要保留事实。 | Event 与 checkpoint 作为内部机制保留，不代表玩家功能。 |
| D004 | 2026-10-15 | ReAct 只记录可观察 trace 字段，不记录私有推理。 | 可追踪性目标。 | Harness 记录 observation、decision、action、result、correction。 |
| D005 | 2026-10-15 | 文本 Provider 顺序为 Azure `gpt-5.6-sol` → DeepSeek → deterministic。 | 已批准路由要求：Azure 主用，DeepSeek 次用。 | Key 只存在环境变量；仅在允许的 availability/rate-limit 失败后降级，不修改全局 Codex 配置。 |
| D006 | 2026-10-15 | 主任务使用 `gpt-5.6-sol`；委派任务使用 Terra/max 或 Luna；5.6 限流职责转移到 `gpt-5.5/xhigh`。 | 已批准的任务模型分配策略。 | 按职责路由模型，所有权和证据要求不变。 |
| D007 | 2026-10-16 | 只有已知零计费失败才能跨 Provider 降级。 | Timeout、传输中断、5xx 和畸形成功可能处于未知计费。 | 未知计费进入 reconciliation 并停止；Azure 429/部署不存在且零计费时可降级到 DeepSeek/本地。 |
| D008 | 2026-10-16 | 本地 Web 只允许 loopback；所有写操作还要求每次启动生成的 capability 与唯一 operation ID。 | Loopback 本身不能阻止跨源探测或重复点击。 | 缺失/错误 capability 被拒绝；已完成 operation ID 不可重放；写操作串行。 |
| D009 | 2026-10-16 | Azure `gpt-5.6-sol` 使用 Azure OpenAI v1 Responses 形态，同时保留 legacy chat completions。 | Live 配置为 GPT-5.6 reasoning 模型，可能暴露 `/openai/v1/responses`。 | 请求把 max completion 映射为 max output，默认省略不支持的 seed，并解析结构化 output text；两种 API 均有测试。 |
| D010 | 2026-10-16 | 只有 Live 响应提供必要 token 字段时才估算 Azure 成本。 | 官方目录价按 token 计费，目录价格不等于发票。 | 本地零费用 call 记录 0；Azure 文本/图片记录 USD 估算微单位与依据；缺少 token 明细则为 null。 |
| D011 | 2026-10-16 | Azure v1 图片调用使用 Bearer，以及 opt-in 的 curl IPv4/HTTP1.1 与 900 秒总超时。 | 已验证 image2 生成器确立该传输合同；urllib/30 秒路径超时。 | Endpoint/header 通过匿名 fd 传递，不进入 curl argv、日志或仓库；body 走 stdin；禁止自动重试；legacy deployment 保留 `api-key`。 |
| D012 | 2026-10-16 | 后续 Scene edit 只允许使用 role 为 `reference` 的人工接受图片。 | 先接受 T1 不能把成年场景误作为 T3 童年身份 anchor。 | 已接受 Scene candidate 仍可成为最终媒体，但不能污染身份连续性输入。 |
| D013 | 2026-10-16 | 最终 ZIP 使用干净的仅追踪快照、一个新提交，以及源目录外的输出路径。 | 干净预检后不得因打包而污染或递归包含源仓库。 | 脏树、仓库内输出、审计 findings、错误 ZIP 形态或 ZIP 审计失败都会在原子发布前停止。 |
| D014 | 2026-10-16 | 运行 prompt 只投影已验证且截止时刻之前的 SceneSpec 证据；样例专用生产约束必须在全部事实存在时才添加。 | T1/T3 高质量 prompt 不能泄漏未来事实，也不能污染可替换材料。 | 移除 future-locked 和 source/network 元数据；REFERENCE/T1/T3 证据不完整时 fail closed 到通用 prompt。 |
| D015 | 2026-10-16 | 所有浏览器写操作要求当前 expected checkpoint；资产接受还要求当前 expected SHA-256。 | 章节推进、生成或审核不能写入已变化的 playthrough head。 | HTTP 写锁覆盖前置条件和写入；过期请求返回 409；已成功 operation ID 禁止重放。 |
| D016 | 2026-10-16 | 最终媒体和拒绝结果从兼容 Storage root 中已验证的最新人工决定推导。 | 语音与 Azure 图片生产使用独立 FileStorage，单 Store 发布视图不完整。 | 材料指纹和记录一致时允许多 Store/Branch；冲突、重复 final、重复拒绝字节、篡改和材料不一致会阻止发布。 |
| D017 | 2026-10-16 | 严格最终审计除静态检查外，还运行真实隔离离线应用 smoke。 | 结构干净的树仍可能包含损坏的 CLI、服务、写操作或重启路径。 | 最终审计在临时无密钥环境启动真实会话/服务；失败时加入 `offline_app_smoke`。 |
| D018 | 2026-10-17 | 玩家运行只采用离线成品；Azure/DeepSeek 属于创作者素材工具，不是游戏模式。 | 六章剧情和聊天均为编写内容，不存在模型驱动动态 IM。 | README 只展示离线玩家启动；Provider 配置独立放入创作者文档，避免误解为附加玩法。 |
| D019 | 2026-10-17 | 重启恢复指定存档；干净初始化必须通过显式 CLI 操作。 | 自动删除会破坏存档，静默复用又会干扰干净演示检查。 | `emoplay init` 创建新存档；`--reset` 只接受已识别且未被占用的 FileStorage，绝不修改 `outputs/` 最终媒体。 |
| D020 | 2026-10-17 | 玩家界面只保留自动章节推进；导演台只保留证据、指标和媒体审核。 | 书签、手动 checkpoint、回溯、分支切换和功能选择增加认知负担，但不改变这部确定性六章剧情。 | 删除对应前端状态与控件、缩减 dashboard 数据，并从 Web 写接口移除六类无效操作；底层不可变审计历史继续服务恢复与发布验证。 |
