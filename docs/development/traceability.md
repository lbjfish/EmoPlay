# 需求追踪矩阵

| 需求 | 实现 | 自动化证据 | 人工证据 | 状态 |
|---|---|---|---|---|
| 加载可替换材料 | `loader.py`、规范领域对象 | Core loader/mutation 测试 | 示例 manifest 复核 | 已实现 |
| 强制 UTC 截止 | `timeline.py` | 同时刻、未来泄漏、T1-T3 测试 | 证据与 future lock 复核 | 已实现 |
| 离线构建 T1-T6 spec | `spec_builder.py`、CLI `spec` | 6 份稳定 spec 与 CLI smoke | T1 冲突复核 | 已实现 |
| 自动章节进度与不可变审计历史 | `StoryPositionService`、Storage 后端与 `PlaythroughManager` | Storage、重启和真实 Chrome 章节推进测试 | 开发记录复核 | 已实现 |
| 记录 call、Bug、错误和决定 | 有界 Harness、JSONL trace、日志与 run 终态 | Harness/config/E2E trace replay | 日志复核 | 已实现 |
| 无 SQLite、无模型运行 | 离线 Provider 链与 FileStorage/MemoryStorage | 离线生成和重启 E2E | Capability JSON 复核 | 已实现 |
| Story 与 Director 视图 | `nining.web` 本地服务、认证控件、静态 UI | Web 测试与真实本地 HTTP E2E；全部写操作要求当前 checkpoint | Web 并发复核 | 已实现 |
| Azure 文本/图片 adapter | Azure→DeepSeek→deterministic 路由与 `gpt-image-2` adapter | Provider 合同、fail-closed timeout trace、7 次成功图片日志 | 6 章图片与身份参考已复核 | 已实调实现 |
| 生成单一 Trigger 并持久化 | `GenerationService`、CLI `generate`/`generate-background`、Web Generate | 离线、CLI、Provider、持久化和干净目录 E2E | 最终 Scene 媒体决定已记录 | 已实现 |
| 媒体 QA 与最终制品 | 审核服务、发布目录、事实推导 manifest、WAV 浏览器转换 | 哈希/篡改/审核/目录/播放测试 | 6 章图片与 T2/T5 WAV 已接受 | 完成 |
| 发布审计与干净环境 E2E | 严格审计、离线应用 smoke、GitHub-ready 源码树、可选 ZIP 构建器 | 完整 Python/前端测试、真实 Chrome 审计、严格审计 0 blocker | 远端 `main` 与 CI 独立验证 | 完成 |
| 人工接受与持久位置 | `ApplicationSession`、`StoryPositionService`、`ReviewService` | 重启、位置、哈希、accept/reject、T1→T6→T2 测试 | 当前最终决定存在 | 已实现 |
| 干净本地初始化 | `emoplay init`、受保护 FileStorage reset | Reset、lock、未知目录项 CLI 测试和 Live T1 锁状态 | 干净演示仅解锁 T1 | 已实现 |
| 主报告与调优文档 | README、`docs/TUNING.md`、Markdown/PDF 源 | 日志数字测试与发布审计 | 当前结果已汇总 | 已实现 |
