# 创作者素材重生成（非玩家功能）

## 结论

EmoPlay 的玩家剧情、聊天和结局是确定内容，不包含模型驱动的动态 IM、自由对话或实时
剧情生成。普通玩家只需离线启动；配置 Azure / DeepSeek 不会解锁新对话。

本目录记录创作者工具：从固定 SceneSpec 重新编排提示词、生成图片/语音候选、记录调用
与费用，并经过人工审核后才可能更新最终媒体。远程 Provider 的部署、配额、内容策略和
生成结果属于运行环境条件，不随 Git 仓库分发。

“能启动”与“远程模型可用”是两项不同验收：

1. **服务启动验收**：解析配置、打开 FileStorage、加载六章和最终媒体、启动 loopback
   Web 服务；不发送模型请求。
2. **Provider 实调用验收**：使用已配置的服务账号发出最小请求，可能联网、计费或被
   限流；只有真实成功响应才能证明当前部署可用。

## 创作者管线的三种状态

| 状态 | 启动要求 | 页面与既有成品 | 新文本/图片/语音生成 | 网络与费用 |
|---|---|---|---|---|
| 离线模式 | Python 3.11+、现代浏览器 | 完整；读取仓库内已接受且哈希匹配的图片/WAV | deterministic / placeholder / prompt-only，不产生新的远程最终媒体 | 禁止联网，零模型费用 |
| 混合配置但无远程凭据 | 同离线模式；可选 macOS `say` | 完整；与离线模式读取同一最终媒体 | 文本回退 deterministic，图片回退 placeholder；无 `say` 时语音回退 prompt-only | 服务启动不联网，生成动作也不会得到远程结果 |
| 混合配置且凭据有效 | 另需账号端点、密钥、模型/部署名、区域配额 | 完整；远程结果先作为候选，不自动替换最终媒体 | Azure 文本/图片优先，DeepSeek 仅作文本安全降级 | 生成动作可能联网和计费 |

SQLite、Node.js 和 pnpm 都不是运行模式的前提。Python 后端直接托管已经构建好的
React 静态资源；Node/pnpm 仅在修改前端源码时需要。

## 远程 Provider 的可移植性边界

远程模型访问权不随 Git 仓库分发，至少取决于：

- Azure 订阅、租户、AI 资源、区域以及允许的模型目录；
- 实际部署名或 v1 模型名、API 路径和服务端版本；
- 账号配额、速率限制、余额、内容安全策略和组织权限；
- DeepSeek 账号、官方 API 可达性、模型别名和当时的服务状态；
- 模型服务端更新、随机性以及同一名称背后的版本变化；
- 操作系统能力；`/usr/bin/say` 与 `Tingting` 属于 macOS，Windows/Linux 不具备
  相同预置语音环境。

本仓库保证适配器契约、降级规则、密钥不入库以及经过测试的请求格式。远程部署是否提供
`gpt-5.6-sol`、`gpt-image-2` 及其具体输出不属于仓库保证范围。模型或部署标识必须通过
环境变量显式提供，示例配置不预设服务账号中的可用部署。

## 环境配置流程

离线基线验证：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .

emoplay validate-config --config config.offline.toml
emoplay serve --config config.offline.toml --materials materials/sample --port 0
```

远程素材生成配置：

```bash
cp config.creator.example.toml config.local.toml

export AZURE_TEXT_ENDPOINT='[AZURE_TEXT_ENDPOINT]'
export AZURE_TEXT_API_KEY='[AZURE_TEXT_API_KEY]'
export AZURE_TEXT_MODEL='[AZURE_TEXT_DEPLOYMENT_OR_MODEL]'

export AZURE_IMAGE_ENDPOINT='[AZURE_IMAGE_ENDPOINT]'
export AZURE_IMAGE_API_KEY='[AZURE_IMAGE_API_KEY]'
export AZURE_IMAGE_MODEL='[AZURE_IMAGE_DEPLOYMENT_OR_MODEL]'

# DeepSeek 文本降级可选；未启用时保持这些变量为空。
export DEEPSEEK_ENDPOINT='https://api.deepseek.com/v1'
export DEEPSEEK_API_KEY='[DEEPSEEK_API_KEY]'
export DEEPSEEK_TEXT_MODEL='[DEEPSEEK_TEXT_MODEL]'

emoplay validate-config --config config.local.toml
emoplay capabilities --config config.local.toml
emoplay serve --config config.local.toml --materials materials/sample --port 0
```

`validate-config` 只验证 TOML 结构；`capabilities` 只检查本地环境变量与本机能力，输出
`static configuration present; live endpoint not tested` 时仍不等于远端调用成功。首次实调
必须具备显式操作授权和预算上限，并检查 provider call 日志、HTTP 分类、模型名、usage
与计费状态。

## 降级与失败边界

- Azure 配置缺失：文本尝试已配置的 DeepSeek，否则 deterministic；图片使用非最终
  placeholder。
- Azure 返回认证、权限、参数或内容策略错误：停止，不把数据自动发送给另一家模型。
- Azure 返回明确未计费的部署缺失或限流：文本可进入已配置的 DeepSeek 降级链。
- 超时、连接中断、5xx、畸形成功响应等计费不明状态：停止，不跨 provider 重试。
- macOS 本地语音程序不存在：报告 prompt-only，不再误报本地语音可用。
- 所有 placeholder、prompt-only 和未人工接受的候选都不能进入最终媒体清单。

## 本仓库实际验证的范围

- 无凭据的混合配置可启动 loopback 服务并返回页面；启动期间不会生成 provider call。
- 无凭据时文本、图片和非 macOS 语音分别安全降级到 deterministic、placeholder 和
  prompt-only。
- 远程适配器有 fixture 合同测试，覆盖 Azure Responses、Azure 图片、DeepSeek 文本、
  endpoint 限制、密钥脱敏、限流降级以及未知计费停止。
- Fixture 合同测试不构成远程部署的实时可用性证明；实时状态必须通过对应部署的最小
  实调验证确认。
