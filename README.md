# Multi AI Assistant

> 通过快捷键在**任意界面**调用 AI 大模型 —— 复制文本、按快捷键、AI 回复自动键入光标位置。

基于 [litellm](https://docs.litellm.ai/docs/providers) 支持 **100+ AI 提供商**（OpenAI、Gemini、Anthropic、Groq、DeepSeek 等），开箱即用。

## 功能特性

- **100+ 模型** — 通过 litellm 统一路由，一套配置兼容所有主流 AI 提供商
- **快捷键随处调用** — 任意界面复制文本 → 按快捷键 → AI 回复自动键入光标
- **CLI + Web 双管理** — 技术用户用 `maa` 命令秒配，非技术用户用浏览器点点就好
- **渠道驱动** — 添加渠道自动获取模型列表，支持标准提供商和自定义 API 端点
- **角色系统** — 模型 + Prompt 组合，中英互译、代码审查、格式转换等一键调用
- **配置热重载** — 修改配置后按快捷键即刻生效，无需重启
- **YAML 配置** — 灵活的三级配置覆盖：默认 → 用户 → CLI

## 安装

```bash
pip install multi_ai_assistant

# 或从源码安装
git clone https://github.com/mofanx/multi_ai_assistant.git
cd multi_ai_assistant
pip install .
```

安装后提供两个等效命令：`multi_ai_assistant` 和 **`maa`**（推荐使用短命令）。

## 快速开始

### 1. 添加渠道 → 自动获取模型

```bash
# 自定义 API 端点（中转/自部署）— 添加后自动获取所有可用模型
maa channel add my_api --api-base-env MY_API_BASE --api-key-env MY_API_KEY

# 查看该渠道的模型
maa model list --channel my_api
```

对于标准提供商（OpenAI、Gemini 等），设置好环境变量后也可通过渠道管理：

```bash
export OPENAI_API_KEY=sk-your-key-here
```

### 2. 绑定快捷键

```bash
# 使用渠道同步的模型 key
maa hotkey set f9+g chat my_api/gpt-4o-mini
maa hotkey set f9+q chat my_api/qwen-max
```

### 3. 启动

```bash
maa run           # 仅快捷键模式
maa run --web     # 同时启动 Web 管理界面
```

### 4. 使用

1. 在任意界面复制文本到剪贴板
2. 按快捷键（如 `F9+G`）
3. AI 回复自动键入当前光标位置

> **非技术用户？** 直接 `maa run --web` 启动后，浏览器打开 `http://127.0.0.1:8199`，所有配置都可以在网页上完成。

## CLI 命令参考

```bash
maa [command] [subcommand] [args]
```

### 渠道管理

渠道是模型的来源。添加渠道时自动获取可用模型列表，删除渠道时自动清理关联模型。

```bash
maa channel list                        # 列出所有渠道
maa channel add NAME [options]          # 添加渠道（自动获取模型）
maa channel remove NAME                 # 删除渠道（级联删除关联模型）
maa channel test NAME                   # 测试渠道连接
maa channel models NAME [query]         # 查看渠道可用模型（支持搜索）

# 示例：添加自定义 OpenAI 兼容端点
maa channel add my_api \
  --api-base-env MY_API_BASE \
  --api-key-env MY_API_KEY \
  -d "我的中转 API"

# 查看渠道中包含 "qwen" 的模型
maa channel models my_api qwen
```

### 模型管理

模型通过渠道自动同步，也可手动更新。

```bash
maa model list                          # 列出已配置的模型
maa model list qwen                     # 按关键词搜索
maa model list --channel my_api         # 按渠道过滤
maa model remove KEY                    # 删除模型

# 从渠道同步/更新模型
maa model update                        # 从所有渠道同步模型
maa model update --channel my_api       # 只同步指定渠道

# 更新 litellm 内置模型数据库
maa model update-db                     # 从 GitHub 下载最新数据库

# 搜索 litellm 内置模型数据库
maa model search gpt                    # 按关键词搜索
maa model search --provider openai      # 按提供商筛选
maa model search flash -p google        # 组合筛选
maa model providers                     # 列出所有支持的 AI 提供商
```

### 快捷键管理

```bash
maa hotkey list                         # 列出所有快捷键
maa hotkey set KEY ACTION [TARGET]      # 设置快捷键
maa hotkey set f9+g chat my_api/gpt-4o  # 示例: 绑定模型对话
maa hotkey set f8+e role translator     # 示例: 绑定角色
maa hotkey remove KEY                   # 删除快捷键
```

### 角色管理

```bash
maa role list                           # 列出所有角色
maa role add KEY BASE_MODEL PROMPT_KEY  # 添加角色
maa role remove KEY                     # 删除角色

# 完整示例：创建翻译角色
maa prompt set trans_en '将以下文本准确翻译成英文，只输出翻译结果。'
maa role add translator my_api/gpt-4o trans_en
maa hotkey set f8+e role translator
```

### Prompt 管理

```bash
maa prompt list                         # 列出所有 Prompt
maa prompt set KEY "prompt text"        # 设置 Prompt
maa prompt remove KEY                   # 删除 Prompt
```

### 配置管理

```bash
maa config init                         # 初始化用户配置文件
maa config path                         # 显示配置文件路径
maa config show                         # 显示当前合并后的完整配置
maa config check                        # 检查环境变量状态
maa config edit                         # 用编辑器打开配置文件
```

### 运行

```bash
maa run                                 # 启动助手
maa run --web                           # 同时启动 Web 界面
maa run --web --web-port 8199           # 自定义端口
```

### 全局选项

| 选项 | 说明 |
|------|------|
| `--version`, `-V` | 显示版本号 |
| `--config PATH` | 指定配置文件路径 |
| `--log-level LEVEL` | 日志级别 (DEBUG/INFO/WARNING/ERROR) |

## 配置文件

配置采用 **YAML 格式**，支持三级覆盖：`默认配置 → 用户配置 → CLI`。

用户配置路径：`~/.config/multi_ai_assistant/config.yaml`

### 配置示例

```yaml
# 渠道 — 模型的来源，添加渠道后模型自动同步
channels:
  my_api:
    type: custom
    provider: custom
    api_base_env: "MY_API_BASE"
    api_key_env: "MY_API_KEY"
    description: "我的中转 API"

# 模型 — 由渠道自动同步，也可手动配置
# key 格式: 渠道名/模型标识符
models:
  my_api/gpt-4o-mini:
    type: litellm
    model: "gpt-4o-mini"
    provider: my_api
    api_key_env: "MY_API_KEY"
    api_base_env: "MY_API_BASE"
  my_api/qwen-max:
    type: litellm
    model: "qwen-max"
    provider: my_api
    api_key_env: "MY_API_KEY"
    api_base_env: "MY_API_BASE"
    enable_search: true

# 角色 — 模型 + Prompt 组合
roles:
  translator:
    base_model: my_api/gpt-4o-mini
    prompt_key: translate_to_english

# Prompt 模板
prompts:
  translate_to_english: "将以下文本准确翻译成英文，只输出翻译结果。"
  translate_to_chinese: "将以下文本准确翻译成中文，只输出翻译结果。"

# 快捷键绑定
hotkeys:
  "f9+g": { action: "chat", target: "my_api/gpt-4o-mini" }
  "f9+q": { action: "chat", target: "my_api/qwen-max" }
  "f8+e": { action: "role", target: "translator" }
  "f9+r": { action: "reload" }
  "esc":  { action: "cancel" }
  "esc+f9": { action: "exit" }

# Web 管理界面
web:
  enabled: false
  port: 8199
```

完整配置参考：`ai_assistant/default_config.yaml`

### 支持的 litellm 模型格式

| 提供商 | 模型标识符示例 |
|--------|----------------|
| OpenAI | `gpt-4o-mini`, `gpt-4o` |
| Google Gemini | `gemini/gemini-2.0-flash` |
| Anthropic | `anthropic/claude-3-5-sonnet-20241022` |
| Groq | `groq/llama-3.1-70b-versatile` |
| DeepSeek | `deepseek/deepseek-chat` |
| 自定义 OpenAI 兼容 | `openai/model-name` + `api_base_env` |

完整列表：[litellm Providers](https://docs.litellm.ai/docs/providers)

### 快捷键 Action 类型

| Action | 说明 | 需要 target |
|--------|------|-------------|
| `chat` | 模型对话 | 模型 key |
| `role` | 角色对话 | 角色 key |
| `cancel` | 取消当前操作 | - |
| `reload` | 热重载配置 | - |
| `exit` | 退出程序 | - |

## 热重载

修改配置后无需重启，以下方式均可触发热重载：

- **快捷键** — 默认 `F9+R`（可自定义）
- **Web API** — `POST /api/reload`
- **Web 界面** — 点击「重载配置」按钮

热重载会重新读取配置文件、清空模型缓存、重新注册快捷键。

## Web 管理界面

```bash
maa run --web
```

浏览器打开 `http://127.0.0.1:8199`，提供可视化管理：

- **仪表盘** — 查看系统状态和渠道健康状态
- **渠道管理** — 添加/测试/删除 API 端点，自动获取模型
- **模型管理** — 搜索过滤已配置模型，一键从渠道同步更新
- **角色管理** — 创建模型 + Prompt 组合角色
- **快捷键管理** — 可视化绑定快捷键到模型或角色
- **Prompt 管理** — 管理 Prompt 模板库
- **环境变量检查** — 查看各渠道的凭证配置状态

> Web 界面特别适合不熟悉命令行的用户，所有 CLI 能做的操作都可以在网页上完成。

## 项目结构

```
multi_ai_assistant/
├── ai_assistant/
│   ├── __init__.py               # 包入口
│   ├── ai_assistant.py           # CLI 子命令 + 热重载入口
│   ├── config.py                 # 配置加载与管理 (三级覆盖)
│   ├── default_config.yaml       # 内置默认配置
│   ├── utils.py                  # 公共工具函数
│   ├── hotkey_manager.py         # 快捷键注册与管理
│   ├── model_factory.py          # 模型工厂 (基于 litellm)
│   ├── assistant/
│   │   ├── base.py               # 助手基类
│   │   └── openai_model.py       # litellm 统一模型 (100+ 提供商)
│   └── web/
│       ├── server.py             # FastAPI 后端 API
│       └── static/index.html     # Web 管理界面
├── README.md
├── pyproject.toml
└── LICENSE
```

## 在代码中使用

```python
from ai_assistant import OpenAIAssistant

# 通过 litellm 模型标识符创建实例
assistant = OpenAIAssistant(model="gpt-4o-mini")
assistant.chat("你好")

# 自定义端点
assistant = OpenAIAssistant(
    model="openai/qwen-max",
    api_base="https://my-api.example.com/v1",
    api_key="sk-xxx",
    prompt="你是一个有帮助的助手"
)
```

## 许可证

MIT License