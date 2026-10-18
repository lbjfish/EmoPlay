# Casa Lume 全球多模态销售 Agent 方案摘要

> 完整技术设计、Supervisor JSON 契约、测试与实施路径见
> [`docs/design/casa_lume_langgraph_sales_agent.md`](../design/casa_lume_langgraph_sales_agent.md)。

方案采用 LangGraph 承载 **Supervisor Loop + 渐进式领域 Worker**。API Gateway 只负责鉴权、限流、文件安全和幂等；Supervisor 负责理解用户目标、选择领域、安排并行或串行、审核每次结果并决定是否结束。Supervisor 只看到 Catalog、Commerce、Asset、Room、Media、Language、Privacy 七类领域入口，具体 API 仅对所属 Domain Worker 可见。

```text
客户文本 / 语音 / 图片
        ↓
输入标准化：ASR、语种识别、附件安全处理
        ↓
Supervisor：目标、已有事实、缺失能力
        ↓
按需启动一个或多个领域 Worker
        ↓
Worker 返回事实、媒体产物或 needed_capabilities
        ↓
Supervisor 审核并写入全局状态
        ↓
不满足目标 → 补调领域 / 恢复 Worker / 澄清 / 降级
满足目标   → 事实、隐私、几何、媒体门禁
        ↓
必要的人审 → 按客户语言输出文本或 TTS 语音 → 幂等发送
```

跨领域依赖不预先写死。Tool Contract 只描述本领域接口的必填参数、可选参数语义、输出、TTL、错误和权限。以客厅适配为例，在图片授权已经通过的前提下，Room Worker 可先完成空间几何；当用户目标是判断 Nivra 能否放下时，它会返回缺少 `ProductDimensions`。Supervisor 再进入 Catalog 领域，Catalog Worker 自行选择商品 API，返回官方尺寸后恢复 Room Worker。所有领域结果都回到 Supervisor，由同一个决策循环判断继续还是结束。

四个请求的处理：

- **S1 绿色款**：Catalog 证实当前只有 CG-02 和 OW-01，不能生成“在售绿色实物”。客户明确接受后才制作带显著“概念模拟、非在售颜色”标记的改色图。
- **S2 客厅试摆**：Privacy、Catalog、Asset 第一波并行，确认图片用途、取得 235 × 95 × 84cm 官方尺寸和官方三维素材；授权通过后 Room 才读取住宅图，随后 Media 完成适配评估和空间合成。260cm 墙面与 235cm 沙发总余量为 25cm，但仍需披露插座、窗帘、踢脚线、门洞和搬运路径。具备官方 3D 资产时生成可拖拽、旋转和量尺的 Three.js 场景，否则降级为带真实 footprint 的近似模拟。
- **S3 葡语语音**：Commerce 获取带 TTL 的价格、10 期免息、20/10 截止、7–10 个工作日和免运费事实；Language 生成 pt-BR 文本与 TTS。输出音频再次 ASR，并对金额、币种、期数、日期和配送数字逐字段精确比对。
- **S4 买家实拍**：Asset Worker 从 DAM/UGC 查询经授权素材；返回空结果时明确回答目前没有，不把生成权限交给该 Worker，也不生成假晒单。替代为官方场景图、客户空间模拟或真实晒单回访登记。

Supervisor 与 Worker 之间统一使用 JSON 契约，包括 `InputEnvelope`、`SupervisorDecision`、`DomainTask`、`DomainResult`、`CapabilityRequest`、`FactEnvelope`、`SupervisorReview`、`SupervisorState`、`FinalResponsePlan` 和 `ApprovalRequest`。LangGraph 保存状态和 checkpoint；结束条件由 Supervisor 根据目标满足度判断，并受最大轮数、时间、费用和重复决策保护。

与柠柠剧情生成相比，本题不只要求人物或时间连续，更强调实时商品事实、价格时效、住宅隐私、媒体来源和交易副作用。EmoPlay 可复用的是状态、证据、checkpoint、审计与降级原则；Casa Lume 在此基础上增加跨领域 Supervisor、实时企业 API、多语言语音和交互式空间试摆。
