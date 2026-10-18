# Casa Lume 全球多模态销售 Agent 技术方案

> 适用范围：家具、电器、家居软装等需要“商品事实 + 空间理解 + 多模态生成 + 全球语言服务”的销售场景。
>
> 核心架构：LangGraph 状态图承载 **Supervisor Loop + 渐进式领域 Worker**。Supervisor 只选择领域、安排顺序、审核结果和决定是否结束；具体 API 只对所属领域 Worker 可见。

## 1. 方案摘要

Casa Lume 的四个请求并不是四次独立的图片或语音生成，而是一次持续销售会话中的四个事实决策：绿色款是否真实存在、沙发能否放进客户客厅、价格和配送是否准确、买家实拍是否真实且获得授权。

系统不让一个大模型直接拥有全部 API，也不预先写死所有跨域流程。每次收到消息后，Supervisor 根据用户目标和当前状态选择少量领域入口；领域 Worker 只加载本领域 Tool Contract，自主选择具体 API。若执行过程中发现缺少其他领域能力，Worker 返回结构化的 `needed_capabilities`，由 Supervisor 决定补调、串行等待、并行执行、向客户澄清或降级。

```text
客户文本 / 语音 / 图片
        ↓
API Gateway：鉴权、限流、文件安全、幂等
        ↓
输入标准化：ASR、语言识别、附件解析
        ↓
Supervisor：理解目标、检查已有事实、选择领域
        ↓
按需启动一个或多个 Domain Worker
        ↓
Worker 调用本领域 API，返回事实、产物或缺失能力
        ↓
Supervisor 审核结果并写入全局状态
        ↓
满足目标？──否──→ 补调领域 / 恢复 Worker / 澄清 / 降级
        │
        是
        ↓
确定性事实、隐私、几何与媒体门禁
        ↓
按风险决定是否人工审批
        ↓
按客户语言生成文本或 TTS 语音并可靠发送
```

## 2. 与柠柠剧情生成的本质差异

| 维度 | 柠柠剧情媒体 | Casa Lume 销售 Agent |
|---|---|---|
| 首要目标 | 人物、时间、地点和承诺连续 | 商品事实正确、合规并推动购买决策 |
| 事实来源 | 截止时刻前的人设和聊天记录 | PIM、库存、价格、物流、DAM、授权服务的实时数据 |
| 主要风险 | 人物变脸、声音变化、剧情穿帮 | 虚构 SKU、过期价格、假买家图、住宅隐私、错误尺寸承诺 |
| 生成边界 | 在既定剧情内生成角色媒体 | 生成内容不得冒充库存事实、实拍或精确测量 |
| 状态变化 | 主要按时间线推进 | 每条消息都可能触发实时查询、跨领域依赖和交易副作用 |
| 交互结果 | 照片、语音或剧情说明 | 文本、语音、概念图、空间合成图、可拖拽试摆场景 |
| 语言 | 固定中文角色 | 自动识别客户语言，并以客户语言输出文本和语音 |

共同点是都必须保存时间、事实、产物来源和审核决定，并在每次生成后把已发送内容写回状态，保证后续对话不自相矛盾。

## 3. 架构名词与职责冻结

以下是方案中固定存在的组件。后续流程不再引入其他隐含调度层。

| 组件 | 是什么 | 负责什么 | 明确不负责什么 |
|---|---|---|---|
| API Gateway | 普通服务网关，不是 LLM | 鉴权、租户隔离、限流、请求大小、文件类型、幂等键、渠道回调 | 不理解业务意图，不选择 Agent 或 API |
| Input Processor | 确定性组件加专用模型 | ASR、语言识别、图片安全处理、消息标准化 | 不做销售决策 |
| Supervisor Agent | 全局决策大脑 | 理解目标、选择领域、安排波次、审核结果、处理冲突、决定继续或结束 | 不直接查询具体业务 API，不编造事实 |
| Domain Entry | 暴露给 Supervisor 的少量领域入口 | 说明该领域能接收什么、能产出什么，并进入对应领域子图 | 不列出领域内部全部 API |
| Domain Worker | 按需启动的单领域执行者 | 根据目标和本领域 Tool Contract 选择并调用具体 API | 不直接调用其他领域，不负责最终回复 |
| Tool Contract | 本领域 API 的结构化说明 | 描述输入、可选输入的业务含义、输出、错误、时效、权限和示例 | 不预先写死全局级联关系 |
| Global State | LangGraph 会话状态与 checkpoint | 保存目标、事实、任务、产物、缺失能力、决定、成本和恢复点 | 不保存密钥和无必要的媒体二进制 |
| Deterministic Gates | 普通代码和策略规则 | 校验金额、SKU、TTL、授权、尺寸、来源标签和发送权限 | 不进行开放式创作 |
| Outbox Sender | 可靠发送组件 | 幂等地向 IM、Web 或语音渠道发送最终结果 | 不改写已审核内容 |

Supervisor 固定只看到 `catalog_domain`、`commerce_domain`、`asset_domain`、`room_domain`、`media_domain`、`language_domain`、`privacy_domain` 七类入口。具体的 `catalog.get_product`、`asset.search_authorized_ugc`、`room.analyze_space`、`pricing.get_offer` 只在相应 Worker 内出现。

## 4. Supervisor 可见的领域入口

领域入口是随 Supervisor 部署的静态 Tool Schema，不是可查询的全局工具库，也不需要额外检索服务。

```json
{
  "supervisor_visible_domain_entries": [
    {
      "domain": "catalog",
      "description": "查询商品身份、规格、可售变体与官方素材",
      "accepts": ["product_mention", "sku", "locale"],
      "provides": ["ProductProfile", "ProductDimensions", "VariantAvailability"]
    },
    {
      "domain": "commerce",
      "description": "查询价格、促销、库存、配送范围与时效",
      "accepts": ["sku", "region", "customer_context"],
      "provides": ["OfferSnapshot", "InventorySnapshot", "DeliveryQuote"]
    },
    {
      "domain": "asset",
      "description": "查询官方图片、三维资产与获得营销授权的客户实拍",
      "accepts": ["sku", "asset_type", "consent_scope"],
      "provides": ["OfficialAssetSet", "AuthorizedCustomerAssetSet", "AssetAbsenceEvidence"]
    },
    {
      "domain": "room",
      "description": "理解客户空间并评估摆放条件",
      "accepts": ["room_image", "measurement_hints", "optional_product_dimensions"],
      "provides": ["RoomGeometry", "FitAssessment", "PlacementConstraints"]
    },
    {
      "domain": "media",
      "description": "根据已验证事实和素材生成概念图、空间合成图和可交互场景",
      "accepts": ["official_assets", "room_geometry", "product_dimensions", "generation_brief"],
      "provides": ["ConceptImage", "RoomComposite", "InteractiveRoomScene"]
    },
    {
      "domain": "language",
      "description": "本地化文本并生成或校验语音",
      "accepts": ["locale", "verified_facts", "response_intent"],
      "provides": ["LocalizedText", "VoiceAsset", "VoiceVerification"]
    },
    {
      "domain": "privacy",
      "description": "检查客户媒体授权、用途、保存期限和撤回状态",
      "accepts": ["customer_id", "asset_ref", "requested_purpose"],
      "provides": ["ConsentDecision", "RetentionPolicy"]
    }
  ]
}
```

这份 JSON 只告诉 Supervisor“哪个领域能产出哪类能力”。Supervisor 不需要知道具体 API 名称，也不直接决定领域内部应该调用一个还是三个接口。

## 5. Supervisor JSON 契约

JSON 契约用于限制模型自由文本，支持校验、回放、测试和断点恢复。所有字段使用版本号，未知信息使用 `null` 或显式缺失项，不允许模型补造。

### 5.1 输入信封 `InputEnvelope`

```json
{
  "schema_version": "1.0",
  "run_id": "run_s2_001",
  "conversation_id": "conv_mariana_01",
  "received_at": "2026-10-15T13:09:00Z",
  "channel": "overseas_im",
  "customer": {
    "customer_id": "customer_mariana",
    "detected_locale": "pt-BR",
    "region": "BR-SP"
  },
  "message": {
    "mode": "mixed",
    "original_text": "Essa é a minha sala. A parede tem 2,60 m. Ele cabe?",
    "normalized_text": "Essa é a minha sala. A parede tem 2,60 m. Ele cabe?",
    "attachments": [
      {
        "type": "image",
        "asset_ref": "upload_room_01",
        "mime_type": "image/jpeg"
      }
    ]
  },
  "known_context_refs": ["product_mention:Nivra", "prior_message:S1"]
}
```

### 5.2 Supervisor 决策 `SupervisorDecision`

```json
{
  "schema_version": "1.0",
  "decision_id": "decision_002",
  "goal": "判断 NIV-3L 是否适合客户客厅，并提供可理解的视觉预览",
  "intent": "room_fit_and_visualize",
  "required_outputs": ["FitAssessment", "RoomComposite"],
  "known_capabilities": ["room_image", "wall_width_hint", "sku"],
  "missing_capabilities": ["ConsentDecision", "ProductDimensions", "OfficialAssetSet", "RoomGeometry"],
  "dispatch": [
    {
      "domain": "privacy",
      "objective": "确认客户客厅图片可用于本次空间模拟",
      "wave": 1
    },
    {
      "domain": "catalog",
      "objective": "获取 NIV-3L 的官方尺寸",
      "wave": 1
    },
    {
      "domain": "asset",
      "objective": "获取 NIV-3L 的官方图片与可用三维资产",
      "wave": 1
    }
  ],
  "deferred": [
    {
      "domain": "room",
      "objective": "从客厅图片和 2.60m 墙宽提示中提取空间几何",
      "wait_for": ["ConsentDecision"]
    }
  ],
  "stop": false,
  "stop_reason": null
}
```

`wave` 只表达当前已确认的并行关系；`deferred` 记录已经发现但尚不允许启动的任务。Room 必须等待图片授权，这是确定性隐私门禁。其他后续任务仍由执行结果回到 Supervisor 后重新判断，而不是一次性生成不可改变的完整 DAG。

### 5.3 领域任务 `DomainTask`

```json
{
  "schema_version": "1.0",
  "task_id": "task_room_001",
  "run_id": "run_s2_001",
  "domain": "room",
  "objective": "提取可用于沙发摆放判断的房间几何和障碍约束",
  "input_refs": ["asset:upload_room_01", "fact:wall_width_2_60m", "fact:consent_decision_01"],
  "expected_outputs": ["RoomGeometry", "PlacementConstraints"],
  "constraints": [
    "不得把单目深度估计描述为精确实测",
    "必须保留置信度和未验证项",
    "不得调用 catalog、asset 或 media 领域接口"
  ],
  "budget": {
    "deadline_ms": 8000,
    "max_attempts": 2,
    "max_cost_usd": 0.08
  }
}
```

### 5.4 Worker 结果 `DomainResult`

```json
{
  "schema_version": "1.0",
  "task_id": "task_room_001",
  "domain": "room",
  "status": "completed",
  "produced_capabilities": [
    {
      "type": "RoomGeometry",
      "ref": "fact:room_geometry_01",
      "confidence": 0.86
    },
    {
      "type": "PlacementConstraints",
      "ref": "fact:placement_constraints_01",
      "confidence": 0.82
    }
  ],
  "needed_capabilities": [],
  "evidence_refs": ["asset:upload_room_01", "fact:wall_width_2_60m"],
  "warnings": ["插座和窗帘开启范围需要客户确认"],
  "error": null
}
```

### 5.5 缺失能力请求 `CapabilityRequest`

当 Worker 判断当前目标无法可靠完成时，不跨域调用工具，而是返回以下结构：

```json
{
  "schema_version": "1.0",
  "task_id": "task_room_fit_002",
  "domain": "room",
  "status": "needs_capability",
  "produced_capabilities": [
    {
      "type": "RoomGeometry",
      "ref": "fact:room_geometry_01",
      "confidence": 0.86
    }
  ],
  "needed_capabilities": [
    {
      "type": "ProductDimensions",
      "required_for": "FitAssessment",
      "reason": "用户要求判断是否放得下，缺少商品真实尺寸只能给出泛化建议",
      "required_now": true
    }
  ],
  "warnings": [],
  "error": null
}
```

### 5.6 事实信封 `FactEnvelope`

```json
{
  "schema_version": "1.0",
  "fact_id": "catalog:NIV-3L:dimensions:v17",
  "fact_type": "ProductDimensions",
  "source_system": "PIM",
  "source_version": "v17",
  "fetched_at": "2026-10-15T13:09:01Z",
  "expires_at": null,
  "payload": {
    "width_cm": 235,
    "depth_cm": 95,
    "height_cm": 84,
    "seat_height_cm": 44
  },
  "confidence": 1.0,
  "provenance": "official_catalog",
  "content_hash": "sha256:example"
}
```

价格、库存和促销必须包含 `expires_at`；生成或发送时若已过期，Supervisor 必须重新调度 Commerce Worker。

### 5.7 Supervisor 审核 `SupervisorReview`

```json
{
  "schema_version": "1.0",
  "review_id": "review_003",
  "goal": "判断 NIV-3L 是否适合客户客厅，并提供视觉预览",
  "satisfied_outputs": ["ConsentDecision", "ProductDimensions", "RoomGeometry"],
  "missing_outputs": ["FitAssessment", "RoomComposite"],
  "conflicts": [],
  "rejected_results": [],
  "next_action": "resume_domain",
  "next_tasks": [
    {
      "domain": "room",
      "objective": "使用已验证尺寸和房间几何完成适配评估"
    }
  ],
  "should_finalize": false,
  "reason": "核心输入已经齐备，但用户要求的适配结论和视觉结果尚未生成"
}
```

`next_action` 只能是：`dispatch_domain`、`resume_domain`、`clarify_user`、`safe_fallback`、`request_human_review` 或 `finalize`。

### 5.8 全局状态快照 `SupervisorState`

```json
{
  "schema_version": "1.0",
  "run_id": "run_s2_001",
  "conversation_id": "conv_mariana_01",
  "goal": "判断 NIV-3L 是否适合客户客厅，并提供视觉预览",
  "status": "running",
  "input_ref": "input:run_s2_001",
  "facts": [
    "fact:product_dimensions_01",
    "fact:room_geometry_01",
    "fact:consent_decision_01"
  ],
  "artifacts": [],
  "tasks": [
    {
      "task_id": "task_catalog_001",
      "domain": "catalog",
      "status": "verified"
    },
    {
      "task_id": "task_room_001",
      "domain": "room",
      "status": "verified"
    }
  ],
  "unresolved": ["FitAssessment", "RoomComposite"],
  "decisions": ["decision_002", "review_003"],
  "budget": {
    "elapsed_ms": 4200,
    "cost_usd": 0.06,
    "remaining_cost_usd": 0.44
  },
  "checkpoint_id": "checkpoint_004"
}
```

### 5.9 最终回复计划 `FinalResponsePlan`

```json
{
  "schema_version": "1.0",
  "run_id": "run_s2_001",
  "locale": "pt-BR",
  "modalities": ["text", "image", "interactive_scene"],
  "verified_claims": [
    {
      "claim": "O sofá tem 2,35 m de largura e a parede informada tem 2,60 m.",
      "fact_refs": ["fact:product_dimensions_01", "fact:wall_width_2_60m"]
    }
  ],
  "required_disclosures": [
    "A visualização é uma simulação.",
    "Confirme rodapé, tomada, abertura da cortina e rota de entrega antes da compra."
  ],
  "artifact_refs": ["artifact:room_composite_01", "scene:interactive_room_01"],
  "voice_required": false,
  "human_review_required": true,
  "send_policy": "after_approval"
}
```

### 5.10 人工审批 `ApprovalRequest`

```json
{
  "schema_version": "1.0",
  "approval_id": "approval_001",
  "run_id": "run_s2_001",
  "risk_level": "high",
  "reasons": ["客户住宅图片", "生成式空间合成", "对外发送"],
  "candidate_response_ref": "response_plan:run_s2_001",
  "fact_refs": ["fact:product_dimensions_01", "fact:room_geometry_01"],
  "artifact_refs": ["artifact:room_composite_01"],
  "allowed_actions": ["approve", "reject", "request_revision"],
  "artifact_hash": "sha256:example"
}
```

## 6. 渐进式工具选择

### 6.1 谁决定什么

| 决策 | 负责人 |
|---|---|
| 用户真正想完成什么 | Supervisor |
| 需要进入哪个领域 | Supervisor |
| 同时启动几个领域 | Supervisor |
| 本领域使用哪个具体 API | Domain Worker |
| 某个可选输入在当前目标下是否必须 | Domain Worker |
| 跨域缺失能力如何补齐 | Supervisor |
| 结果是否足够回答客户 | Supervisor |
| 金额、权限、尺寸和发送是否允许 | Deterministic Gates |

### 6.2 为什么不把所有 API 暴露给 Supervisor

当企业存在数百个接口时，全量暴露会增加 token、工具误选和权限面。额外增加全局向量检索又会产生一次检索、排序和召回失败成本。本方案在常规规模下采用两级渐进暴露：

```text
Supervisor 只看到 6～12 个领域入口
                  ↓
选中 catalog_domain
                  ↓
Catalog Worker 只看到本领域 5～20 个具体 API
                  ↓
根据本领域 Tool Contract 选择并执行
```

只有当单个领域内部接口增长到本地上下文无法容纳时，才在该领域内部增加搜索；它仍然不成为全局架构的必需层。

### 6.3 可选依赖如何动态判断

Tool Contract 不写死“调用 A 必须先调用 B”，而是描述字段语义：

```text
room.assess_fit
  必填：room_geometry
  可选：product_dimensions
  语义：若用户要求判断是否放得下、比例是否协调，
        缺少 product_dimensions 只能给低置信度建议。
```

Room Worker 根据用户目标判断：

- 用户只问“这个房间适合什么风格”：可以不需要商品尺寸；
- 用户问“Nivra 能否放下”：商品尺寸在业务上成为当前目标的必要输入；
- 用户只要快速概念图：可以用近似 footprint，但必须降低置信度并明确披露；
- 用户要下单前确认：必须取得官方尺寸和搬运约束，不能使用近似值。

### 6.4 跨领域依赖发现

依赖可能在三个时间点被发现：

1. Supervisor 首次规划时，根据目标和领域描述提前发现；
2. Worker 阅读本地 Tool Contract 时，发现当前目标需要其他能力；
3. API 返回部分结果或错误后，Worker 才知道还缺什么。

```text
Domain Worker 发现缺少能力
        ↓
返回 needed_capabilities，不直接跨域调用
        ↓
Supervisor 检查现有 Global State
        ↓
已有该能力？──是──→ 恢复原 Worker
        │
        否
        ↓
根据领域入口描述选择可提供该能力的领域
        ↓
派发新任务，等待结果写入 Global State
        ↓
恢复原 Worker 或由 Supervisor 重新规划
```

Worker 之间不互相创建 Agent，所有跨域关系都回到 Supervisor，避免递归失控和责任不清。

## 7. Supervisor Loop 与 LangGraph 串联方式

LangGraph 在本方案中负责状态、条件边、子图、暂停恢复和 checkpoint，不负责替 Supervisor 做业务判断。

```text
[接收并标准化输入]
          ↓
[Supervisor Decide]
          ↓
  ┌────需要澄清────→ [向客户提问] → [等待新输入]
  │
  ├────需要领域能力→ [启动/恢复 Domain Worker]
  │                         ↓
  │                   [结果写入 State]
  │                         ↓
  └──────────────── [Supervisor Review]
                            ↓
              结果不够 ────┴──── 结果足够
                 ↓                    ↓
          回到 Decide            [Policy Gates]
                                      ↓
                            风险高？──是→ [人工审批]
                                      │
                                      否/批准
                                      ↓
                              [本地化与发送]
                                      ↓
                                    [结束]
```

结束不是由固定流程步数决定，而是由 Supervisor 对照 `required_outputs` 判断：目标是否满足、事实是否有证据、冲突是否解决、披露是否完整。为防止无限循环，还要设置最大轮数、费用、时间和重复决策检测；超过边界时进入澄清或安全降级。

## 8. Agent 数量与动态并行

长期常驻的核心 Agent 只有一个 Supervisor。Domain Worker 是按任务动态进入的领域子图，不要求每条消息启动全部 Worker。

```text
简单问价：Supervisor + Commerce Worker

问绿色款：Supervisor + Catalog Worker
                    └─客户接受概念预览后再启动 Media Worker

客厅试摆：Supervisor
           ├─Privacy Worker ─┐
           ├─Catalog Worker ─┼─ 第一波并行
           └─Asset Worker ───┘
                              ↓
                         Supervisor 审核授权
                              ↓
                         Room Worker 第二波
                              ↓
                         Media Worker 第三波

葡语语音：Supervisor
           ├─Commerce Worker
           └─Language Worker 在事实就绪后执行
```

同一波次只放没有数据依赖的任务。需要上一步输出的任务必须进入下一波；执行途中发现新依赖时再动态补充，不要求开场就预测全部 Agent 数量。

## 9. 多语言和语音链路

ASR 是语音转文字，TTS 是文本转语音，两者不能混用。

```text
客户语音
   ↓
ASR + Language ID：得到原语言文本、语言、置信度
   ↓
结构化意图与实体：商品、地区、金额诉求、输出模态
   ↓
内部类型化事实：SKU、Decimal 金额、币种、日期、配送天数
   ↓
Supervisor 和领域 Worker 完成任务
   ↓
按客户 locale 生成目标语言文本
   ↓
需要语音？──是→ pt-BR TTS → ASR 回听 → 数字/日期精确比对
   │
   否
   ↓
文本或多模态回复
```

系统可以为运营人员保留中文工作译文，但不能用“先自由翻成中文、再自由翻回去”的方式承载价格和尺寸。金额、币种、日期、SKU 和单位始终保存在类型化字段中，最后按 `pt-BR` 模板注入。

## 10. 四个请求的执行方案

### 10.1 S1：请求绿色 Nivra 图片

客户请求的是绿色在售商品，但当前 Catalog 只有 Cinza Grafite 和 Off-White。

```text
S1 葡语文本
   ↓
Supervisor 识别：商品变体查询 + 图片请求
   ↓
Catalog Worker：解析 Nivra → NIV-3L，查询可售颜色和官方资产
   ↓
返回：绿色变体不存在
   ↓
Supervisor 判断原请求无法按事实直接满足
   ↓
先用葡语说明当前只有 CG-02 / OW-01
   ↓
询问是否接受“非在售绿色概念预览”
   ↓
客户同意？──否→ 推荐真实颜色并结束
   │
   是
   ↓
Media Worker 基于官方图保形改色
   ↓
视觉一致性检查 + 显著标记“概念模拟，非在售颜色”
   ↓
人工审批后发送
```

禁止直接生成绿色沙发并暗示它可以买到。商品事实与视觉想象必须分开保存。

### 10.2 S2：客厅摆放与可交互试摆

已知墙宽 260cm、沙发宽 235cm，静态计算总余量为 25cm，居中约每侧 12.5cm。但“数学上放得下”不等于“安装和使用上合适”。

第一波并行获取：

- Privacy Worker：确认住宅照片仅用于本次模拟，设置最短保留期并移除 EXIF/GPS；
- Catalog Worker：获取官方商品身份与尺寸；
- Asset Worker：获取正侧面官方图和可用 CAD/glTF/USDZ，不读取客户住宅图。

授权通过后，第二波 Room Worker 才能读取住宅图片，识别墙、地板、窗帘、插座、植物、相机和尺度锚点；第三波再执行适配评估和 Media 生成：

```text
ProductDimensions + OfficialAssetSet
                    │
                    ├─────────────┐
                    │             │
RoomGeometry + PlacementConstraints
                    │             │
                    └──────┬──────┘
                           ↓
                    FitAssessment
                           ↓
            能否生成可信交互场景？
                  │                  │
                 能                 不能
                  ↓                  ↓
        Media Worker 构建场景    生成 2D 近似合成图
                  ↓                  ↓
      拖拽、旋转、吸附、标尺     明确“模拟/非精确测量”
                  └────────┬─────────┘
                           ↓
                几何、遮挡、来源和披露门禁
                           ↓
                      人工审批发送
```

前端使用 Three.js / React Three Fiber 展示交互场景，可拖拉拽、旋转、切换官方颜色、显示 footprint 与安全间距。缺少官方 3D 资产时，只使用真实尺寸构建近似体积，不宣称还原了沙发全部结构。

回复必须提醒客户确认踢脚线、插座、窗帘开启、门洞、电梯和搬运转角。空间图是决策辅助，不是安装保证。

### 10.3 S3：价格和配送葡语语音

```text
S3：客户正在驾驶，请求语音
   ↓
Supervisor 识别：Commerce 事实 + pt-BR VoiceAsset
   ↓
Commerce Worker 查询实时价格、促销期限、配送和运费
   ↓
Supervisor 审核 TTL 与地区：São Paulo capital
   ↓
Language Worker 用 pt-BR 模板生成短文本
   ↓
TTS 使用品牌批准的巴西葡语预置音色
   ↓
生成音频重新 ASR
   ↓
金额、币种、期数、日期、配送天数逐字段精确比对
   ↓
一致？──否→ 重生成或转人工
   │
   是
   ↓
发送简短语音，并附文字副本供稍后查看
```

事实锁定为：R$ 4.299,90；10 × R$ 429,99；有效至 20/10；圣保罗市区 7–10 个工作日；免运费。任何数字不一致都不能仅凭“语义接近”放行。

### 10.4 S4：请求买家实拍

```text
S4：客户明确要求真实买家照片
   ↓
Supervisor 进入 Asset 领域
   ↓
Asset Worker 按 SKU 和 marketing consent 查询 DAM/UGC
   ↓
有授权素材？──是→ 核对撤回状态、用途、hash 后发送
   │
   否
   ↓
返回“无经授权买家实拍”的事实证据
   ↓
Supervisor 禁止调用生成模型伪造 UGC
   ↓
提供真实替代：官方场景图 / S2 客户空间模拟 / 登记晒单回访
```

“没有”也是有效结果。不能用生成图、官方样板间或其他 SKU 的图片冒充买家实拍。

## 11. 完整场景推演：S2 动态补依赖

假设住宅图片授权已经存在于 Global State，但 Supervisor 首轮只派发了 Room Worker，尚未取得商品尺寸：

```text
1. Room Worker 调用本领域空间分析接口，得到 RoomGeometry。

2. Room Worker 准备执行适配评估。
   本地 Tool Contract 表明 ProductDimensions 对“能否放下”是业务必要输入。

3. Room Worker 不知道 Catalog 具体接口，也不跨域调用，
   返回 needed_capabilities = [ProductDimensions]。

4. Supervisor 收到后检查全局状态，当前没有 ProductDimensions。

5. Supervisor 根据 catalog_domain 的能力描述，
   派发“为 NIV-3L 取得 ProductDimensions”的任务。

6. Catalog Worker 在本领域选择具体商品 API，
   返回 235 × 95 × 84cm，并附官方来源和版本。

7. Supervisor 审核并写入 Global State，随后恢复 Room Worker。

8. Room Worker 完成 FitAssessment，计算宽度余量，
   同时指出插座、窗帘和搬运路径仍未确认。

9. Supervisor 发现客户还要求“给我看看”，继续派发 Media Worker。

10. Media Worker 返回空间合成图或交互场景，
    Supervisor 再次审核，目标全部满足后才进入发送门禁。
```

这就是 Supervisor Loop：每次领域结果都回到同一个决策点，是否继续由目标满足度决定，不由预设流程图强行决定。

## 12. Tool Contract 设计原则

每个领域维护自己的 Tool Contract，至少包含：

- 工具用途与不适用场景；
- 必填参数、可选参数及其业务影响；
- 输入输出 JSON Schema；
- 来源系统、TTL 和数据权威等级；
- 只读或有副作用；
- 权限、地区和租户限制；
- 超时、重试、幂等与费用；
- 常见错误和安全降级；
- 正例、反例和边界案例。

跨域关联不写成不可改变的硬依赖图，而通过“能力类型 + 参数语义 + 当前用户目标”动态形成。确定性的硬约束仍然必须写死，例如：没有客户授权不得处理住宅图片、没有真实 UGC 不得声称存在买家实拍、价格过期不得发送。

## 13. 数据、事实和媒体来源

| 领域 | 权威数据源 | 关键输出 |
|---|---|---|
| Catalog | PIM / 商品中心 | SKU、尺寸、材质、颜色、官方图、3D 资产 |
| Commerce | ERP、Pricing、Logistics | 库存、价格、促销、配送、运费、TTL |
| Room | 视觉与几何服务 | 平面、深度、尺度、障碍、置信度 |
| Asset | DAM / UGC 资产服务 | 官方图片、三维资产、经授权买家实拍、空结果证据 |
| Media | 图像生成、合成与渲染服务 | 概念图、空间合成图、交互场景 |
| Language | ASR、Language ID、Translation、TTS | 原文、语言、本地化文本、语音、回听结果 |
| Privacy | Consent / CRM / Policy | 授权目的、保留期、撤回状态、数据区域 |

工具返回内容一律作为不受信任数据处理，不能把 API 或 OCR 返回的文本当作新的系统指令。

## 14. 性能、准确率与成本平衡

### 14.1 默认快路径

- 商品名明确、只问单一价格：Supervisor 直接进入 Commerce Worker；
- SKU 已在会话状态中解析，不重复调用 Catalog；
- 简单字段查询优先用确定性 API 适配器，不额外调用领域 LLM；
- 只有模糊目标、多接口选择或局部重规划时才使用 Worker LLM。

### 14.2 并行原则

- Catalog、Privacy、Room 初步理解可并行；
- Media 必须等待官方资产、授权和空间几何；
- Language 可先准备非事实措辞，但金额和配送必须等 Commerce 事实锁定；
- 同一副作用不并行重试，避免重复计费或发送。

### 14.3 模型分层

| 任务 | 建议执行方式 |
|---|---|
| Supervisor 跨域决策、冲突处理和最终审核 | 高能力推理模型，最小必要上下文 |
| 领域内明确字段抽取和分类 | 小模型或确定性代码 |
| 商品、价格、物流、授权 | 企业 API，不由 LLM 生成 |
| ASR、TTS、深度、分割、渲染 | 专用模型或服务 |
| 金额、日期、TTL、hash、尺寸公式 | 确定性代码 |

### 14.4 预算和停止条件

每轮保存 token、模型费、媒体费、API 延迟和重试次数。Supervisor 受最大循环轮数、截止时间、费用上限和重复决策检测约束。超过限制时优先返回已验证的部分结果并说明缺口，而不是无限循环。

## 15. 安全、隐私和可靠发送

- API Gateway 使用 OIDC、租户隔离、速率限制和请求大小限制；
- 客户住宅照片先进行病毒扫描和 EXIF/GPS 清理；
- 图片只用短期签名 URL，按授权保留期自动删除；
- 各领域 Worker 使用最小权限服务账号和本地工具白名单；
- 禁止任意 shell、任意 URL、动态 SQL 和模型生成工具名；
- 价格、库存和活动在最终发送前重新检查 TTL；
- 生成图保存 prompt、模型、seed、源素材 hash、耗时和费用；
- 概念图、空间模拟、官方图和买家实拍使用不同 provenance 标签；
- Outbox 记录目标渠道、内容 hash 和 idempotency key，避免重复发送；
- 高风险媒体、住宅图片、概念颜色和事实冲突进入人工审批。

## 16. 故障与降级策略

| 故障 | 处理方式 |
|---|---|
| 商品名有多个候选 | 向客户澄清，不猜 SKU |
| Catalog 不可用 | 不生成商品事实，返回稍后确认或人工接管 |
| 价格 TTL 过期 | 重新查询；失败则不播报旧价格 |
| Room 深度估计置信度低 | 降级到 2D 近似图，要求补充测量 |
| 缺少官方 3D 资产 | 使用真实尺寸 footprint，明确近似 |
| 图像模型不可用 | 返回文字尺寸判断和官方图，不阻塞基本销售服务 |
| TTS 不可用 | 返回 pt-BR 文本，并说明暂时无法发送语音 |
| ASR 回听数字不一致 | 阻断语音，重生成或转人工 |
| Worker 超时 | 保留已完成事实，只重试该领域 |
| Supervisor 连续重复同一决策 | 触发循环保护，澄清或人工接管 |

## 17. 测试与验收

### 17.1 单元和契约测试

- 所有 Supervisor JSON 契约进行 Schema 校验；
- 每个领域入口只返回声明过的能力类型；
- Tool Contract 覆盖必填、可选、错误、TTL 和权限；
- 金额使用 Decimal，尺寸和日期不经过自由文本计算；
- Worker 不得调用其他领域 API；
- Outbox 对相同 idempotency key 只发送一次。

### 17.2 图级测试

- 单领域快路径是否只启动一个 Worker；
- 两个独立领域能否并行；
- 动态发现依赖后能否补调并恢复原 Worker；
- Worker 部分完成时事实是否保留；
- Supervisor 是否会在目标未满足时继续循环；
- 最大轮数、费用和重复决策保护是否生效；
- checkpoint 恢复后是否避免重复付费和重复发送。

### 17.3 S1–S4 Golden Set

- 不存在的绿色 SKU 不得被描述为在售；
- 概念图必须有显著标签和源素材追踪；
- 235cm 沙发与 260cm 墙面的总宽度余量计算必须为 25cm；
- 空间结论必须包含插座、窗帘和搬运路径未验证项；
- 葡语语音中的金额、期数、日期和配送天数回听完全一致；
- 没有 UGC 时必须拒绝伪造；
- 恶意提示不得越过事实、隐私和发送门禁。

### 17.4 业务指标

- SKU、颜色、价格、配送关键字段错误率：0；
- 未授权住宅图片处理和假 UGC 发送：0；
- 语音数字回听通过率：100%，否则阻断；
- p50 / p95 响应延迟、每轮成本和人工接管率；
- 概念图接受率、试摆交互完成率、销售转化提升；
- 客户主动撤回授权后的删除完成率和耗时。

## 18. 实施路线

### 阶段一：事实型 MVP

- 接入海外 IM、API Gateway 和会话状态；
- 实现 Supervisor、Catalog、Commerce、Language 三个领域；
- 完成 S1、S3、S4 的事实查询、葡语文本和语音；
- 上线 JSON Schema、TTL、幂等、审计和人工接管。

### 阶段二：空间合成

- 接入客户授权、媒体生命周期和 Room Worker；
- 基于官方尺寸完成 2D 空间合成与置信度披露；
- 建立视觉、几何和隐私 Golden Set。

### 阶段三：交互式试摆

- 接入 CAD/glTF/USDZ 和 Three.js / React Three Fiber；
- 支持拖拽、旋转、吸附、标尺、颜色切换和事件埋点；
- 缺资产时自动降级到 footprint 或 2D 预览。

### 阶段四：平台化

- 多品牌、多租户和地区化政策；
- 领域 Tool Contract 版本治理；
- Supervisor 离线评测、灰度发布和成本路由；
- 通过新增领域入口扩展售后、安装、支付和推荐能力。

## 19. 与 EmoPlay 的关系

EmoPlay 当前实现的是离线剧情产品，并未在运行时接入 LangGraph 多 Agent、全球商品 API 或自动语种服务。能够复用的是设计原则和工程基础：

| 可复用原则 | EmoPlay | Casa Lume |
|---|---|---|
| 状态保存 | 剧情进度、checkpoint | 会话事实、任务、领域结果、恢复点 |
| 时间/证据边界 | 只能使用当前章节之前的信息 | 只能使用当前有效且有来源的商品事实 |
| 审计 | 生成与决策日志 | 模型、工具、事实、媒体、审批和发送审计 |
| 降级 | 离线确定性内容 | API、图像或语音不可用时返回安全替代 |
| 人工门禁 | 内容验收流程 | 住宅图片、概念图、事实冲突和发送审批 |

Casa Lume 不是把 EmoPlay 包装成多 Agent，而是把其中已经验证的“状态、证据、回溯、审计、降级”原则扩展为 Supervisor 驱动的跨领域销售平台。

## 20. 最终技术结论

```text
Supervisor 持有全局目标与状态
        ↓
渐进式选择少量领域入口
        ↓
Domain Worker 在本领域选择具体 API
        ↓
缺少跨域能力时结构化返回 Supervisor
        ↓
Supervisor 动态补调、并行、串行、恢复或澄清
        ↓
每轮结果都回到 Supervisor 审核
        ↓
目标满足后经过确定性门禁、必要的人审和可靠发送
```

这套设计既保留 Agent 根据上下文自主规划的能力，又通过领域隔离、类型化事实、JSON 契约、权限边界、checkpoint 和确定性门禁控制准确率、性能、成本与风险。
