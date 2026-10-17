# 产品打磨验收 - 2026-10-17

## 范围

本轮把 Story 视为玩家产品，把 Director 视为明确的本地创作者工具；不执行激活、上传、发布或提交。

## 已报告问题与关闭情况

| 问题 | 根因 | 产品修复 | 证据 |
|---|---|---|---|
| 选择章节后锁定状态变化 | 时间线按 `selected_trigger_id` 而不是持久化进度计算锁定。 | 锁定状态改用持久化位置；选择只改变当前查看内容。 | 后端回看测试与 Chrome T1→T6→T2 审计。 |
| Story 出现审计/人工审核语言 | Story 复用了创作者状态和连续性文案。 | Story 改为“照片与声音”和“柠柠的此刻”；技术证据与审核只留在 Director。 | DOM 文案门与截图。 |
| 非文本聊天显示 placeholder 句子 | 对话未获得章节资产。 | Trigger 消息解析已接受图片/音频/视频并渲染原生控件；只有主媒体确实缺失时才显示脚本转写。 | T1/T3/T4/T6 行内图片；T2/T5 原生音频。 |
| Director 图片/JSON 布局重叠 | 全局资产被用作当前主图，Director 又受限于狭窄第三列。 | 当前 Scene 独占主图；Director 横跨整行；JSON 放入受约束 disclosure；媒体卡限制预览范围。 | 1440px 与 390px 几何检查。 |
| 部分章节没有图片 | 早期发布合同只要求 T1/T3/T5 媒体。 | T1-T6 每章均有已接受图片；T2/T5 还要求已接受音频。 | `outputs/manifest.json` 精确媒体矩阵。 |

## 产品复核发现的其它缺陷

- AIFF 虽显示播放器但 Chrome 无法解码；最终音频改为 PCM WAV，并在真实 Chrome 中验证播放。
- 桌面主内容存在过多空白；Story Grid 改为填充居中的 1440px Canvas。
- 移动端章节栏可能隐藏后期当前章节；选择与缩放时会自动居中。
- 未来消息数曾被展示成剩余章节数；界面现从章节状态计算。
- Director 曾把 JSON 当作媒体卡；JSON 现只保留在连续性数据抽屉。
- Story 曾同时暴露 rejected/candidate 与 final 媒体；玩家界面现只选择已接受最终资产。
- 干净 ZIP 曾只有媒体字节而没有被忽略的审核 Storage；运行时现从已追踪发布 manifest 加载经过验证的只读媒体目录。

## 最终媒体矩阵

| 章节 | 背景/主图 | Trigger 行内媒体 |
|---|---|---|
| T1 | PNG | PNG |
| T2 | PNG | PCM WAV，7.569751 秒 |
| T3 | PNG | PNG |
| T4 | PNG | PNG |
| T5 | PNG | PCM WAV，5.131655 秒 |
| T6 | PNG，纸条精确包含 `阿杰 10.16` | PNG |

## 真实浏览器验收

证据保存在 `work/qa/browser-product-audit-v9/`：

- 6 张桌面章节截图；
- T1/T2/T5 媒体聚焦截图；
- 1 张 390×844 移动端 T6 截图；
- 1 张 Director 截图；
- `report.json`：章节锁数组、媒体解码/播放、布局约束、网络、HTTP 与 console 结果。

验收条件：

- 初始锁状态为 `[false, true, true, true, true, true]`；
- 每次“下一章”只解锁 1 章；
- 到达 T6 后回看 T2，6 章仍全部解锁；
- 6 张主图都具有非零自然尺寸；
- T2/T5 音频时长有限、无媒体错误且播放时间能推进；
- 桌面和移动端无横向溢出；
- 移动端章节栏能看见当前 T6；
- Director 与展开的 JSON 均处于 viewport 内；
- 无外部浏览器请求、HTTP 错误或 fatal console error。

干净安装验收还会创建空 FileStorage playthrough，并证明 T1 仍可通过本地 HTTP 200 获得 manifest 中已接受图片；危险目录路径或哈希篡改字节均 fail closed。
