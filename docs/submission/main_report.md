# EmoPlay 柠柠多模态连续性系统说明

> 状态：六章图片、T2/T5 浏览器 WAV、人工接受记录、严格审计和真实 Chrome 路径均已完成；`outputs/manifest.json` 当前为 `release_ready=true`。源码交付状态以 GitHub `main` 与最新 CI 为准。

## 1. 整体思路

程序先把 persona、user、chat、state、triggers 读成统一领域对象，再以触发时刻的 UTC 为硬截止线生成 `SceneSpec`。同一时刻按消息序号处理，触发消息可见，之后的消息进入 `future_locked_message_ids`，不会进入提示词。SceneSpec 包含人物锚点、当时状态、最近可见消息、承诺、冲突、证据来源和 QA 清单；离线命令可一次重算 T1–T6。

生成时走可观察的 `observe → decide → act → evaluate → stop` Harness。每次先创建 run 和“生成前”checkpoint，再调用文本提示词编排与图像/语音 provider，记录模型、参数、seed 支持情况、耗时、计费状态、错误和 idempotency key；媒体按内容 hash 落盘并登记 asset，最后写 rule decision、“生成后”checkpoint 和 run 终态。任何超时/5xx 导致计费状态未知时停止，不跨 provider 重发；429/部署不存在且明确零计费时才可 Azure → DeepSeek/本地降级。

网页只绑定 `127.0.0.1`，提供 Story 与 God 两种视图。Story 默认读取当前 checkpoint 的 cursor 并锁住未来，解锁下一章时自动保存位置；God 展示全量证据、provider 状态、候选和审计摘要。玩家界面不提供书签、回溯或分支切换；生成、位置和人工审阅写入同一存储协议。SQLite 不可用时可用单写者 FileStorage 或内存后端。

## 2. 如何保证“一直是她”

视觉身份锚点来自纯文字：圆脸、单眼皮、笑时卧蚕、本人左眉尾小痣、本人左耳单只银色月亮耳钉、体型与穿衣习惯。发型属于时间状态：T1 之后必须是锁骨发和不齐刘海，不能继续沿用人设中的及胸长发。童年 T3 继承稳定脸部特征，但按二年级年龄、红色背带裤、黄葛树和旺财左耳缺口重建，不能把成年脸直接缩小。

声音只采用文字设计和系统预置 `macos-say:Tingting`，没有真人录音或克隆。T2/T5 使用同一 preset 与 rate；临时情绪作为 scene delta，不改 speaker identity。本地 preset 不能可靠实现轻微四川口音及唱 K 后沙哑，这一限制仍保留；当前接受音频已无损转封装为浏览器可播放 WAV，后接 TTS 时仍沿用同一 `voice_profile` 和精确脚本 gate。

## 3. 能不能发：门禁与返工

自动硬门：无未来信息；地点/动作/服装与状态一致；承诺可追溯；T3 必须兑现 P1；T6 纸面文字精确；语音脚本逐字；文件路径、大小、SHA-256 一致；placeholder、prompt-only、fixture 永远不能 final。安全/计费/网络状态不明确即 fail closed。

人工硬门：同一人物/音色、左右标记、年龄连续、画面自然度、声音情绪与可发送感。只有人工输入理由、再次核验 hash，并产生 `decided_by=human, action=accept` 后，asset ID 才进入 checkpoint 的 `accepted_assets`。不通过则保留原候选、写 reject reason，再从同一 SceneSpec 修改单一变量重做；历史调用和费用不删除。

## 4. 时间、费用与调优证据

`outputs/metrics.json` 由 provider、manifest 和浏览器日志自动计算。本地证据中七次预置语音的中位 provider 耗时为 1,179 ms，`billing_state=zero`；T2/T5 最终时长分别为 7.570 s 与 5.132 s。真实 Chrome 已证明两条 WAV 均能开始播放。

前后对比只改变语速：rate=180 时 T5 为 7.593 s；rate=220 时为 5.132 s，缩短 2.461 s（32.4%）。rate=220 已接受并转为 WAV。两次模型、脚本、音色不变，hash、耗时和零成本可从日志复算。

首次 image-2 定妆调用在 31.256 s 时 transport timeout：未收到 usage、未产生图片，`billing_state=unknown`、cost=null，因此按门禁没有盲目重试。传输路径修正后，日志记录 7 次成功 `gpt-image-2` 调用，中位延迟 176.138 s、总 tokens 95,464、估算成本约 USD 1.724952。公式与原始调用级数字见 `docs/TUNING.md` 和 `logs/provider_calls.jsonl`；估算值不替代 Azure 账单。
