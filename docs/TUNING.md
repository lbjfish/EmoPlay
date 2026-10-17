# EmoPlay 调优与评估记录

本文记录产品调优过程中试过什么、哪里失败、为什么调整，以及如何从仓库日志复算所有数字。它只使用已提交的脱敏日志，不依赖聊天记录、API Key 或人工口述。

## 1. 数据源

| 文件 | 用途 |
|---|---|
| `logs/provider_calls.jsonl` | 每次 provider/model、用途、状态、延迟、usage、计费状态与估算成本 |
| `logs/runs/*.jsonl` | 每次生成的 observe/decide/act/evaluate/stop 轨迹 |
| `logs/product_coverage_baseline.json` | 修复前提交 `ef5e304…` 的章节媒体事实快照 |
| `logs/product_browser_audit_before.json` | AIFF 修复前真实 Chrome 媒体状态 |
| `logs/product_browser_audit_after.json` | WAV 修复后真实 Chrome 播放、布局与章节回看状态 |
| `outputs/manifest.json` | 当前最终媒体、SHA-256、人工决定与 MIME 类型 |
| `outputs/metrics.json` | 由上述日志自动计算的汇总结果 |

重新计算：

```bash
PYTHONPATH=src .venv/bin/python scripts/summarize_metrics.py .
jq '.product_coverage_comparison, .browser_audio_comparison, .gpt_image_2' outputs/metrics.json
```

对应回归测试：

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_summarize_metrics.py -q
```

## 2. 指标定义

- **章节背景覆盖率**：T1–T6 中拥有最终 `image` 的章节数 ÷ 6。
- **主触发媒体覆盖率**：图片章 T1/T3/T4/T6 有最终图片，语音章 T2/T5 有最终音频的章节数 ÷ 6。
- **浏览器可播放语音率**：T2/T5 中最终 MIME 为浏览器目标格式，且真实 Chrome 解码和播放推进成功的数量 ÷ 2。
- **生成延迟**：`provider_calls.jsonl` 中成功 `gpt-image-2` 调用的 `latency_ms`。
- **估算成本**：成功调用日志里的 `cost_amount_micros` 求和；这是项目价格公式的估算，不是 Azure 最终账单。
- **语速调优**：同一 T5 脚本使用 Tingting 不同 rate 时的音频时长变化。

## 3. 关键尝试与结果

### 3.1 首次远端图像调用：失败后停止

最早的视觉参考调用走旧的短超时路径，31,256 ms 后发生 `transport_timeout`，`billing_state=unknown`。因为无法判断是否计费，系统没有自动重试，也没有把 placeholder 当成最终图。该失败仍保留在 provider call 与 reconciliation 日志中。

后续先用已有成功会话核对 Azure v1 Bearer、IPv4/HTTP1.1、响应结构和 900 秒总超时，再把每个新场景限制为一次授权调用。

### 3.2 图像资产补齐

最终共有 7 次成功 `gpt-image-2` 调用：1 张虚构身份参考和 6 张章节图。日志汇总：

| 指标 | 数值 |
|---|---:|
| 成功调用数 | 7 |
| 中位延迟 | 176,138 ms（176.138 s） |
| 平均延迟 | 197,051 ms（197.051 s） |
| 最短 / 最长 | 111,622 / 341,980 ms |
| 总 tokens | 95,464 |
| 估算总成本 | 1,724,952 micros，即约 USD 1.724952 |

每次图片都先经过人物一致性、场景事实、手部/文字和网页裁切检查，再绑定 SHA-256 接受。T6 额外放大检查纸条必须准确为 `阿杰 10.16`。

### 3.3 T5 语速与音色调优

| 候选 | 时长 | 结论 |
|---|---:|---|
| Tingting rate 180 | 7,593 ms | 偏慢、拖沓，拒绝 |
| Tingting rate 220 | 5,132 ms | 节奏合适，接受后转封装为 WAV |
| Tingting rate 320 | 2,624 ms | 过快，拒绝 |
| Sinji rate 220 | 5,132 ms | 音色/地域感不符合角色，拒绝 |

从 rate 180 到 220，时长减少 2,461 ms，即 **32.4%**。最终声音不是克隆真人，而是 macOS 本地预置音色；T2/T5 都不产生网络 TTS 成本。

### 3.4 “有播放器”不等于“能播放”

第一轮页面显示了 `<audio>` 控件，但真实 Chrome 对两个 AIFF 都返回：

- `readyState=0`
- `networkState=3`
- `MediaError.code=4`（`MEDIA_ERR_SRC_NOT_SUPPORTED`）
- 可播放数 `0/2`

将同一 PCM 内容无损转封装为 WAV 并重新绑定哈希后：

- T2 时长 7.569751 s，播放推进到 0.387082 s；
- T5 时长 5.131655 s，播放推进到 0.420761 s；
- 两条均 `audioError=null`；
- 可播放数从 `0/2` 提升到 `2/2`。

AIFF 被保留为审计证据并标记拒绝，Story 只选择最终 WAV。

### 3.5 章节状态与布局

修复前，章节锁以“当前查看章”为边界：完成 T6 后回点 T2，会错误地重新锁住 T3–T6。修复后以持久进度为边界，真实 Chrome 完成 T1→T6 后回到 T2，锁数组仍为：

```json
[false, false, false, false, false, false]
```

布局验收使用 1440×1000 桌面和 390×844 手机视口，检查 hero、Director、展开 JSON、当前章节导航和页面宽度。最终结果：

- 水平溢出：0；
- 外部浏览器请求：0；
- HTTP 错误：0；
- fatal console error：0；
- 手机 T6 当前章可见：通过；
- Director/JSON 边界：通过。

## 4. 至少一组前后对比

以下数据由 `scripts/summarize_metrics.py` 从基线快照、当前 manifest 和浏览器日志计算：

| 指标 | 修复前 | 修复后 | 变化 |
|---|---:|---:|---:|
| 章节背景覆盖率 | 2/6，33.3% | 6/6，100.0% | +66.7 个百分点 |
| 主触发媒体覆盖率 | 3/6，50.0% | 6/6，100.0% | +50.0 个百分点 |
| 浏览器可播放语音率 | 0/2，0.0% | 2/2，100.0% | +100.0 个百分点 |
| T5 rate 180→220 时长 | 7,593 ms | 5,132 ms | -2,461 ms，-32.4% |

基线事实可直接用 Git 历史复核：

```bash
git show ef5e304198d3c2f30ec8e92dcb543b3242a1e9dd:outputs/manifest.json \
  | jq '.final_media | keys'
```

## 5. 评估边界

- 图片是否“像同一个虚构角色”、构图是否自然仍属于人工视觉判断；自动化只验证路径、尺寸、哈希、加载和布局。
- 成本为日志中的估算值，不能替代 Azure 发票。
- 浏览器结论来自本机真实 Chrome；最终 WAV 同时使用标准 RIFF/PCM 格式降低跨浏览器风险。
- 模型与密钥不进入日志；日志只保存 provider/model 名、哈希、用量和安全错误。
