# EmoPlay 中文文档目录

玩家文档入口为仓库根目录的 [README](../README.md)，默认使用离线配置启动游戏。
本目录其余内容服务于开发、验收和素材复现。

## 当前文档

| 路径 | 用途 | 普通玩家需要阅读吗 |
|---|---|---:|
| `TUNING.md` | 调优过程、失败尝试、前后数字对比及计算来源 | 可选 |
| `creator/ASSET_GENERATION.md` | 创作者重新生成图片/语音候选的 Provider 管线；不是动态聊天功能 | 否 |
| `design/` | 原创视觉、交互和无障碍设计方向 | 否 |
| `design/casa_lume_langgraph_sales_agent.md` | Casa Lume LangGraph 多 Agent 销售系统的完整技术设计与落地路径 | 否 |
| `development/` | Bug、变更、决策、需求追踪和产品 QA | 否 |
| `submission/` | 最终说明、销售 Agent 技术方案摘要和人工审核记录；严格发布审计要求保留 | 否 |
| `assets/` | README 使用的产品截图、GIF 预览和 MP4 操作指引 | 否 |

## `output` 与 `outputs`

这两个目录位于仓库根目录，不在 `docs/` 内：

- `output/pdf/`：主报告 PDF 与哈希清单。销售 Agent 技术方案只保留 Markdown，不生成 PDF；网页游戏不读取，但严格发布审计会检查；
  删除后游戏仍可启动，CI 的 release audit 会失败。
- `outputs/`：游戏素材与生成事实，包括六章 SceneSpec、最终图片/WAV、manifest、指标、
  候选和提示词。网页通过 `outputs/manifest.json` 加载人工接受的媒体；不能整体删除。

纯游戏精简发行版必须先修改发布审计和 manifest，再移除 PDF 或历史候选。
`outputs/runs/` 不能直接手工删减，因为 manifest 以路径和 SHA-256 绑定媒体；缺少任何
被引用文件都会使审计失败。
