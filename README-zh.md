# Agenten

丹麦语AI任务规划器，支持工具使用 — 将提示分解为任务树，分析文件，重构代码，并通过LLM自主执行操作。

## 🚀 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env   # 用你的GitHub令牌编辑.env
python api_server.py   # 打开 http://localhost:5000
```

**要求：** [LM Studio](https://lmstudio.ai) 在 `localhost:1234` 上运行，使用兼容模型。  
**视觉：** 使用支持视觉的模型（Gemma 4, Qwen-VL, Llava）。  
**启动输出：** `🕐 Startet: 2026-05-19 15:21:30 | api_server=15:21:30 | llm=15:15:05` — 验证版本。

## 📁 项目结构

```
agent_core.py         # Agent 外观：初始化、工具注册、分解、执行、委托方法
agent_tasks.py        # 任务执行：solve_task_stream, solve_task, handle_tool_call
agent_tree.py         # 树操作：parse, create_fallback_tree, record_outcome, evolve_if_needed
agent_files.py        # 文件/块操作：读/写/分块、文件夹扫描（.env 已排除）
agent_skills.py       # 技能匹配、模板常量（TEMPLATE_TOOLS, TEMPLATE_TASK_TOOLS）
agent_issues.py       # Issue工具：read_issue, update_issue_status, create_issue, 文件大小检测
agent_git.py          # Git/PR工作流：is_pr_workflow, extract_branch_name, verify_pr_step
api_server.py         # Flask API：SSE流、会话、图片上传、版本、issues端点
llm_wrapper.py        # LM Studio HTTP客户端（聊天 + 流 + 视觉/图像编码）
tools.py              # Tool/ToolRegistry — 工具框架（parse_response, build_system_prompt）
task_tree.py          # TaskTree / TaskNode 数据结构
config.py             # 中心常量（CHUNK_SIZE, timeout, max_tokens）
git_ops.py            # Git + 文件操作（write_file, edit_file，带验证）
github_wrapper.py     # GitHub API：仓库、issues、PRs
session_manager.py    # 会话持久化（JSON），线程锁
web_searcher.py       # DuckDuckGo 网页抓取
ddg_search.py         # DuckDuckGo 搜索（备用）
flow_builder.py       # 提示流构建器
module_builder.py     # 动态模块构建器（实验性）
model_manager.py      # OpenAI + LM Link REST API 模型列表器
skill_loader.py       # 技能系统 — frontmatter、关键词评分、模板匹配
skill_evolution.py    # SkillFlow — 结果追踪、进化分析（保留/改进/剪枝/生成）
skill_tracker.py      # 按技能记录结果，含成功率
lang.py               # 翻译（da/en/es/zh）
i18n.py               # 国际化键（K 枚举）
AGENTS.md             # 知识库 — 错误、修复、调试工作流
BRUGERVEJLEDNING.md   # 用户指南（丹麦语）
static/index.html     # 浏览器UI：拖拽/调整面板、模板下拉、图片预览、issues查看器
tests/                # 384 项测试（pytest）
sessions/             # JSON 会话持久化（保存/加载/删除）
skills/               # 带 frontmatter 的 markdown 技能文件
```

## 🎯 模板

在分解前从下拉菜单中选择模板 — LLM 接收固定部分：

| 模板 | 描述 |
|------|------|
| 🌳 **自由分解** | LLM 动态确定任务树（3-6个主要任务，最多2层） |
| 📄 **摘要** | 概述 → 要点 → 结论 → 建议 |
| 🔍 **代码分析** | 目的 → 导入 → 架构 → 代码质量 → 安全性 |
| 📊 **差异分析** | Git日志 + diff → 风险评估 → 建议 |
| 🔀 **PR Agent** | 分支 → 提交 → 推送 → PR（自动化PR工作流） |
| 💻 **编程任务** | 需求 → 架构 → 实施计划 → 安全 → 代码 |
| 🏗️ **Python 架构** | 架构规划，使用 `write_file` 输出到 `./docs/arkitektur.md` |
| 🖼️ **图像分析** | 描述 → 背景 → 细节 → 评估 → 导出 (.md) |
| 🔧 **重构** | 分析 → 计划 → 提取 → 更新 → 测试（SOLID重构） |
| 🧪 **测试生成** | 分析 → 测试（红）→ 实施 → 验证（绿） |
| 🐛 **Bugfix (TDD)** | 分析 → 测试（红）→ 实施 → 验证（绿）→ 更新 |
| 📋 **Issue Handler** | 读取 → 分析 → 修复 → 验证 → 更新状态 |

**图像分析要求：** 在点击"分解" **之前**，使用 🖼 按钮上传图像。WebP 图像会自动转换为 `image/png` MIME 以兼容 gemma。

## 🔧 工具

Agent 可以通过 `<<<TOOL>>>` 标记执行系统操作（28 个工具）：

| 工具 | 操作 |
|------|------|
| `list_chunks` | 列出所有已加载的文件 |
| `read_chunk` | 读取大文件的一个块 |
| `write_file` | 创建新文件（拒绝覆盖现有 .py — 使用 edit_file） |
| `edit_file` | 在现有文件中搜索替换（带语法检查） |
| `list_files` | 列出目录中的文件（支持模式过滤和最大深度） |
| `create_issue` | 创建新 issue |
| `create_refactor_issue` | 为大文件创建重构 issue |
| `read_issue` | 读取 issue |
| `update_issue_status` | 更新 issue 状态 |
| `run_tests` | 运行 pytest 并返回结果 |
| `add_image` | 添加图像到上下文（base64编码） |
| `github_create_repo` | 创建 GitHub 仓库 |
| `github_list_repos` | 列出你的仓库 |
| `github_create_issue` | 创建 GitHub issue |
| `github_create_pr` | 创建拉取请求 |
| `git_status` | 显示已更改的文件 |
| `git_add_all` | 暂存所有更改 |
| `git_commit` | 提交带有消息 |
| `git_push` | 推送到远程 |
| `git_set_remote` | 设置远程源 URL |
| `git_remote_status` | 检查远程配置 |
| `git_diff` | 显示提交之间的差异 |
| `git_log` | 显示最近的提交 |
| `git_create_branch` | 创建新分支 |
| `git_current_branch` | 显示当前分支 |
| `git_branch_list` | 列出所有分支 |
| `git_pull` | 从远程拉取更改 |
| `git_checkout` | 切换到分支 |

## 🖼️ 视觉 / 图像分析

使用 🖼 按钮或"浏览"+"读取文件"上传图像。支持 `.png`、`.jpg`、`.webp`、`.gif`、`.bmp`。

**图像与会话绑定** — 随会话保存、切换会话时加载、新建会话时清除。

### 模型兼容性

| 模型 | 格式 | JSON 类型 |
|------|------|-----------|
| **Gemma 4** (26b/e4b) | `data:image/png;base64,...` | `image_url` |
| Qwen / GPT / Llava | `data:image/png;base64,...` | `image_url` |

> **重要：** Gemma 4 在 LM Studio 中拒绝 `image/webp` MIME — 会自动映射为 `image/png`。图像放置在内容数组中**文本之前**（Gemma 要求）。

参见 `skills/vision_models.md` 了解完整兼容性矩阵和 `AGENTS.md` 了解调试工作流。

## ✅ 验证

`write_file` 和 `edit_file` 对写入的文件执行自动验证：

| 验证 | 时机 | 描述 |
|------|------|------|
| **语法检查** | `.py` 文件 | `ast.parse()` — 防止写入语法错误的文件 |
| **依赖检查** | `.py` 文件 | 扫描 imports 对照 `requirements.txt` — 自动更新 |
| **路由不匹配** | `.py/.html/.js` | 比较前端/后端 URL — 返回 `route_warnings` |
| **覆盖保护** | `.py` 文件 | `write_file` 拒绝覆盖现有文件 — 使用 `edit_file` |

## 🔐 安全性

- **令牌在 `.env` 中**：GitHub 令牌仅在 `.env` 中（不在代码中，不在 git 中）
- **`.env` 永不扫描**：`.env` 文件已从文件夹扫描和 `read_file_content` 中排除
- **提示注入**：`<<<TOOL>>>` 和 `<<<DONE>>>` 标记从用户输入中剥离。通过 `_sanitize_prompt()` 进行额外清理
- **写入前语法检查**：`write_file` 和 `edit_file` 在写入前验证 Python 语法
- **仅注册的工具**：`ToolRegistry.execute()` 拒绝未知工具名称
- **按阶段工具限制**：模板的每个阶段只能访问相关工具
- **API 密钥认证**：`/api/*` 端点可选 API 密钥保护（设置 `AGENT_API_KEY`）
- **魔数验证**：图片上传验证魔数（不仅仅是文件扩展名）
- **子进程安全**：Git 命令使用列表参数（无 shell）
- **LM Studio**：本地运行 — 不向外部发送数据（GitHub API 除外）

## 🤖 LM Studio 设置

1. 下载 [LM Studio](https://lmstudio.ai)
2. 下载模型：
   - **视觉**：`google/gemma-4-26b-a4b` 或 `gemma-4-e4b`
   - **文本**：`qwen/qwen3.6-35b-a3b` 或 `qwen3-30b-a3b`
3. 在 `http://localhost:1234` 启动服务器
4. 设置上下文长度至少为 8192

## 🏗️ 架构

```
浏览器 (index.html)
    │ SSE (EventSource)
    ▼
Flask API (api_server.py)
    │
    ├── decompose() → agent_core.decompose_prompt()
    │       ├── agent_skills.get_templates() / match_skills()
    │       ├── agent_files.get_folder_context() / get_single_file_context()
    │       ├── agent_tree.parse_tree_from_llm() / create_fallback_tree()
    │       └── LLM（分解）
    │
    ├── execute_stream() → agent_core.solve_task_stream()
    │       ├── agent_tasks.solve_task_stream() → LLM + 工具循环
    │       ├── agent_tasks.handle_tool_call()
    │       ├── agent_git.verify_pr_step()
    │       ├── agent_tree.record_outcome()
    │       └── 当子节点存在时跳过根节点（无冗余重新执行）
    │
    ├── /api/image/* — 上传/列出/清除/删除
    ├── /api/issues — 列出所有跟踪的 issues
    ├── /api/version — 服务器版本 + 文件时间戳
    └── sessions/ (JSON 持久化)
```

**工具循环**：`solve_task_stream` → LLM → 解析响应 → 工具：执行 → 反馈结果 → LLM → ... → `<<<DONE>>>`

**PR 工作流**：`agent_git.verify_pr_step()` 强制按正确顺序执行：分支 → 提交 → 推送 → PR

**技能**：`skill_loader.py` 加载带有 frontmatter 的 `skills/*.md`。`agent_skills.match_skills()` 对提示进行评分并激活相关技能。`skill_evolution.py` 分析结果并建议保留/改进/剪枝/生成。

**SkillFlow**：`skill_tracker.py` 按技能记录结果。超过 15 个结果后，`agent_tree.evolve_if_needed()` 触发自动进化分析。

**版本跟踪**：服务器启动显示 `🕐 Startet:` + `📦 llm=HH:MM:SS`。`/api/version` 返回所有文件时间戳。

## 📝 功能特点

- **自主Bug修复**：🐛 Bugfix (TDD) 模板 → 分析 → 测试 → 实施 → 验证 → 更新
- **自主重构**：🔧 重构模板 → 分析 → 计划 → 提取 → 更新 → 测试
- **测试生成**：🧪 为未测试的类/函数/方法生成测试
- **图像分析**：上传 → 分解 → 5阶段结构化分析 → 导出 .md
- **视觉支持**：自动模型检测、格式适配 (Gemma 需要 raw_b64 + 图像在文本前)
- **Issues查看器**：🐛 Issues 按钮显示所有跟踪的 issues，附带详细信息和"用作任务"操作
- **Issue Handler**：📋 自动问题修复工作流（读取 → 分析 → 修复 → 验证）
- **精确文件编辑**：`edit_file` 搜索替换而不是全文件重写
- **自动发现**：`create_issue` 工具在分析期间报告新的错误/issues
- **会话**：保存/加载/重命名/删除 — 带原子写入的持久 JSON 存储
- **文件分析**：上传文件、文件夹扫描（已排除 .env）、自动分块
- **流式输出**：带思考切换和停止按钮的实时 SSE 输出
- **结果级联**：前置任务结果反馈到下一个任务
- **拖拽/调整面板**：自由布局，支持最大化/最小化
- **Markdown导出**：会话报告的预览 + 下载
- **多语言**：UI 和 LLM 指令支持丹麦语、英语、西班牙语和中文
- **按阶段工具限制**：每个阶段只能访问相关工具（例如：分析阶段 = 只读）
- **超时**：任务执行超过 30 分钟自动终止（EXECUTION_TIMEOUT）
- **自动完成**：防止 10-15 次迭代后出现无限工具循环
- **健壮JSON解析**：`json.JSONDecoder().raw_decode()` 处理 AI 输出
- **LM Link支持**：兼容 OpenAI 的 REST API 模型
- **Context-CoT integration**: Extract-first guidance (LLM 必须总结上下文后再调用工具)，anti-leakage read_issue (默认只读问题，请求时包含提示)，基于技能的验证（二元检查 → retry）
- **OpenCode Go支持**：设置 `OPENCODE_BASE_URL` + `OPENCODE_API_KEY` 以使用 OpenCode Go 而不是 LM Studio
- **原生函数调用**：OpenAI 原生 `tools` 参数随聊天完成一起发送 — 模型返回结构化 tool_calls 而非标记解析
- **会话持久化修复**：`current_session_id` 在测试文件间的泄漏已修复，`_save_session_data` 的防抖移除（总是在 SSE 结束时保存），树序列化现在包含 `result` 字段
- **阶段检查和自动推进**：阶段的确定性成功标准。系统在所有模块存在或计划写入后自动完成。
- **重构模板迭代限制**：更高的预算 (15-12 次迭代) 以处理大的重构工作量。
- **384 项测试**：覆盖所有模块的 pytest 测试套件

## 📋 要求

```
flask>=3.1.3
flask-cors>=6.0.2
requests>=2.33.1
beautifulsoup4>=4.14.3
python-dotenv>=1.2.2
openai>=1.0.0
```

## 🔄 Git 工作流

```bash
git add -A
git commit -m "描述"
git push
```

使用 **🔀 PR Agent** 模板实现自动化 PR 工作流，**💻 编程任务** 用于代码生成，**🖼️ 图像分析** 用于视觉任务，**🔧 重构** 用于代码重构，**🐛 Bugfix (TDD)** 用于错误修复。
