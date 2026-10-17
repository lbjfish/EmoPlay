# 变更日志

| 日期 | 批次 | 变更 | 原因 | 验证 |
|---|---|---|---|---|
| 2026-10-15 | baseline | 初始化仓库、安全规则、打包元数据和追踪记录。 | 以明确职责开始多 Agent 实现。 | Git 状态与文件检查。 |
| 2026-10-15 | batch-1 | 调度 3 个单一职责 Agent，均在开始前触发同一 Provider 限流。 | 已批准的执行方案要求并行实现。 | 未产生 Agent 文件或提交；B001 记录失败与重试策略。 |
| 2026-10-15 | batch-1-root | 增加严格 TOML 配置加载、静态能力发现、CLI 自检、密钥脱敏和 7 项离线测试。 | 为受限的委派任务提供共享集成基础。 | `PYTHONPATH=src python3 -m unittest discover -s tests -v`；离线能力命令。 |
| 2026-10-15 | batch-1-core-recovery | 主 Agent 在多次限流后实现材料加载、UTC 截止、证据/冲突/承诺编译、确定性 SceneSpec、CLI spec 导出和可移植示例材料。 | Core Agent 路径持续不可用，需要在本地继续推进。 | 22 项定向测试、Ruff、mypy、6 文件 CLI smoke。 |
| 2026-10-15 | batch-2 | Terra Agent 完成 Provider 路由与 Web Story/Director；受限的 Core 审计由 `gpt-5.5/xhigh` 完成。 | 分散模型容量并保持主 Agent 集成职责。 | Provider 18 项、Web 6 项、Core 16 项定向测试及完整 102 项回归。 |
| 2026-10-15 | integration-2 | 增加发布审计，提交离线 T1-T6 spec，解决全局 mypy 和密钥扫描问题。 | 将组件成果提升为发布级证据。 | 直接 pytest 102 项、全局 Ruff/mypy、发布审计 0 blocker。 |
| 2026-10-16 | application-integration | 增加生成服务、CLI `generate`/`serve`、Provider call 与资产持久化、run 终态、认证 Web 生成及离线/重启 E2E。 | 将组件合同组合为可执行产品流程。 | Ruff/mypy、120 项测试、发布审计 0 findings。 |
| 2026-10-16 | security-hardening | 修复可复现的 Provider、配置、Storage、trace、Web 和归档边界问题。 | 独立审计发现付费/外部发布 blocker。 | 对抗性单元测试与跨层 E2E；未调用 Live Provider。 |
| 2026-10-16 | review-session | 增加 `ApplicationSession`、checkpoint 位置、人工 accept/reject、诊断、视觉参考流程和参考图 edit。 | 完成 Story/Director 生命周期，而不是停留在候选生成。 | 会话重启、过期 head、审核/哈希、参考图 edit 和 `serve` 黑盒测试。 |
| 2026-10-16 | local-voice-reports | 增加 macOS 预置语音、4.273 秒参考音色、T5 调优/拒绝候选、持久 call 日志/指标、单页报告和页数/哈希门禁。 | 满足非克隆语音路径，并让时间/数字可复算。 | AIFF 时长/哈希证据、PDF 渲染检查、132 项测试、结构审计 0 findings。 |
| 2026-10-16 | strict-final-gate | 将结构开发审计与默认严格最终门禁分离。 | Manifest 仍有 blocker 时必须防止错误绿色。 | 开发模式结构通过但 `release_ready=false`；严格模式返回 4 个 blocker。 |
| 2026-10-16 | official-pricing-and-responses | 增加官方 Azure 价格快照/公式及 `gpt-5.6-sol` Azure v1 Responses 支持。 | 避免虚构图片成本，并对齐 Live 文本 API 形态。 | 官方页面证据、Responses 合同测试及 legacy chat 回归通过。 |
| 2026-10-16 | azure-reference-attempt-1 | 执行一次经批准的 `gpt-image-2` 身份参考调用，凭据经隐藏 stdin 注入。 | 开始真实视觉交付且不持久化密钥。 | 31.256 秒后超时；保存失败 run/call/trace；无图片；计费未知；未自动重试。 |
| 2026-10-16 | image2-proven-path | 检查已验证的 image2 生成器并对齐 Azure v1 图片传输。 | 第二次付费调用前用可复现传输证据替代假设。 | Bearer、IPv4、HTTP1.1、900 秒、图片数组合同测试通过；Live retry 仍受门禁。 |
| 2026-10-16 | parallel-production-hardening | 并行执行视觉准备、干净打包和独立审计；集成匿名 fd curl 密钥、仅参考图 edit anchor、生产提示包和单提交 ZIP 构建器。 | 将并行成果整合为可独立复核的一致发布路径。 | 149 项测试、Ruff、mypy、真实 loopback curl、开发审计 0 findings；严格审计只剩最终媒体/人工 blocker。 |
| 2026-10-16 | runtime-prompt-and-v2-design | 强化运行证据投影，增加已验证的 REFERENCE/T1/T3 生产约束，并记录完成度、E2E、依赖、前端架构和原创互动剧情美术方向。 | 将提示准备接入真实生成链，并在实现前冻结 V2 前端合同。 | 153 项测试、Ruff、mypy、开发审计 0 findings；可替换 T1 SceneSpec 和未来污染回归通过。 |
| 2026-10-16 | v2-backend-closure | 增加统一写并发、可行动审核错误、跨 Store 发布 manifest、已有语音参考导入、依赖顺序快照恢复和严格离线 smoke。 | 在替换 V2 React 展示层前关闭后端/发布缺口。 | 176 项测试、Ruff、mypy、带 smoke 的开发审计 0 findings；严格审计仅剩真实最终媒体/人工 blocker。 |
| 2026-10-17 | product-polish-closure | 修复进度与选择混淆、玩家文案、真实非文本媒体、导演台布局、六章图片、T2/T5 音频、WAV 浏览器兼容、章节居中和真实 Chrome 验收。 | 手工产品评审发现功能断言未覆盖的缺口。 | Chrome T1→T6→T2、桌面/移动截图、真实 WAV 播放、前后端测试及严格 manifest 0 blocker。 |
| 2026-10-17 | clean-install-media | 从 `outputs/manifest.json` 加载 fail-closed 的只读发布目录，并与玩家 Storage 合并。 | 已追踪包必须在没有忽略开发状态时仍显示最终媒体。 | 空 Storage 会话以 HTTP 200 提供已接受 T1 媒体；危险路径和篡改字节失败；manifest 资产不可被可变审核控件修改。 |
| 2026-10-17 | originality-and-model-portability | 移除外部恋爱游戏引用，以原创故事身份重命名设计记录，并修正跨账号/系统的 Hybrid 能力报告。 | 公开交付必须原创，Git clone 也不应暗示自动获得 Azure、DeepSeek 或 macOS 模型环境。 | 品牌扫描、无凭据 Hybrid 黑盒、Provider 合同、全量回归和严格审计。 |
| 2026-10-17 | documentation-cleanup | 增加中文文档地图，从公开树移除内部 Agent 对话，刷新审核状态，并将 Provider 配置移出玩家说明。 | 公开文档需要单一离线游戏路径和客观工程语言。 | 链接扫描、重命名配置测试、前后端测试、严格审计和 GitHub check run 实时验证。 |
| 2026-10-17 | local-state-initialization | 增加安全 CLI 初始化和 FileStorage reset，记录使用方式，并把本地演示初始化到 T1。 | 重启恢复与干净演示初始化必须是两个显式操作。 | 218 项 Python 测试、严格审计、marker/lock 安全门及 Live API 锁状态 `[false,true,true,true,true,true]`。 |
| 2026-10-17 | chinese-documentation | 将 development 与 submission 当前说明统一为中文叙述，并重新生成中文 PDF 元数据、页脚和 manifest。 | 公开仓库文档需要面向中文开发者，同时保留命令、路径、字段名和协议标识的准确性。 | 9 份 Markdown 未翻译正文扫描为 0；两份 PDF 均为 1 页并完成渲染复核；218 项 Python 测试、前端验证和严格审计通过。 |
| 2026-10-17 | readme-operation-video | 使用开源 Pillow 与 FFmpeg 将现有产品截图编排为操作视频，输出 GIF 预览、H.264 MP4 和封面图，并嵌入 README。 | GitHub 首页需要无需启动项目即可理解核心操作的轻量演示。 | 297 帧、1280×720、12 fps；6 个时间点和导演台画面复核；MP4 全帧解码通过；媒体格式/大小回归测试通过。 |
