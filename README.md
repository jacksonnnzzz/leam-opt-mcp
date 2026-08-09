# LEAM Opt MCP

从论文、天线结构图和自然语言要求生成**可审核、可版本化、可反馈修正的 HFSS Python 建模代码**，并在模型确认后选择性进入仿真和自动优化。

> 当前版本：`0.1.0-alpha`
>
> HFSS 是当前实现的后端；CST 接口已预留，但尚未实现。

## 这个项目解决什么问题

论文复现通常不是简单的“识别几个数字”：尺寸可能分散在图片、图注和正文中，部分参数没有公开，不同案例还可能出现在同一页。让大模型直接操作 HFSS，容易把识别错误直接变成不可追踪的模型错误。

本项目把这个过程拆成可检查的流水线：

```text
PDF / 图片 / 文字描述
        ↓
识别天线拓扑、尺寸、材料和布尔关系
        ↓
区分论文证据、视觉解释、工程假设和未确定项
        ↓
人工审核与内容哈希确认
        ↓
generated_model_v001.py
        ↓
用户在 HFSS 中运行并与来源图片对照
        ↓
文字或截图反馈
        ↓
generated_model_v002.py
        ↓
可选：HFSS 求解和参数自动优化
```

生成 Python 代码是默认终点。识别和代码生成不会自动启动 AEDT，也不需要 HFSS 许可证。

## 核心能力

- 接收自然语言、PNG/JPEG 和论文 PDF；
- 使用本地视觉模型或云端视觉模型读取结构图；
- 将识别结果保存为参数、材料、实体、尺寸和布尔操作等中间工件；
- 对图文冲突、跨案例尺寸污染、低置信度结构和未公开参数执行质量检查；
- 用内容哈希冻结人工审核结果，工件修改后旧批准自动失效；
- 生成 import-safe 的 `generated_model_vNNN.py`，只暴露 `build(hfss)`；
- 保存用户的 HFSS 对照意见和截图，生成新的代码版本而不覆盖旧版本；
- 在显式授权后构建 HFSS 工程、运行参数试验并保存最优结果；
- 同时提供 MCP 和命令行接口。

## 快速安装

需要 Python 3.10–3.13。仅使用离线建模流程时不需要安装 AEDT。

```powershell
git clone https://github.com/jacksonnnzzz/leam-opt-mcp.git
cd leam-opt-mcp

py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,local-vision]"
```

需要通过外部 Python 连接 HFSS 时，再安装 PyAEDT：

```powershell
python -m pip install -e ".[hfss,dev,local-vision]"
```

检查当前配置；报告只显示密钥是否存在，不显示密钥内容：

```powershell
antenna-doctor
```

## 配置视觉与文本模型

下面的组合使用本机 Ollama 识图、DeepSeek 处理文本和生成代码：

```powershell
ollama pull qwen3-vl:8b

$env:ANTENNA_VISION_PROVIDER = "ollama"
$env:OLLAMA_BASE_URL = "http://localhost:11434"
$env:OLLAMA_VISION_MODEL = "qwen3-vl:8b"

$env:ANTENNA_TEXT_PROVIDER = "deepseek"
$env:DEEPSEEK_API_KEY = "<your-key>"
$env:DEEPSEEK_BASE_URL = "https://api.deepseek.com"
$env:DEEPSEEK_MODEL = "<your-text-model>"

$env:ANTENNA_MCP_WORKSPACE = ".antenna-mcp"
```

环境变量只对当前 PowerShell 进程及其子进程生效。不要把真实 API Key 写入 README、配置样例、任务 JSON 或 Git 提交。

也可以将视觉提供方设置为 `openai`，具体变量见 [`.env.example`](.env.example)。

## 最短建模流程

### 1. 创建任务

```powershell
antenna-workflow model-create `
  --description "只复现目标图中的天线，不得混用论文其他案例的尺寸" `
  --template paper_reconstruction `
  --attachment "C:\path\to\paper.pdf"
```

命令会返回一个 `mdl-...` 任务编号，后续命令都使用这个编号。

### 2. 识别并审核来源

```powershell
antenna-workflow model-run <job-id> --through-stage source_analysis

antenna-workflow source-refine <job-id> `
  --description "列出全部实体、独立尺寸、派生关系、材料和未确定项"
```

检查任务目录中的：

- `source_analysis_candidate.json`；
- `source_refinement_report.json`；
- `source_review_packet.json`；
- 裁切后的视觉输入和视觉审计文件。

确认内容后，把命令返回的哈希原样提交：

```powershell
antenna-workflow source-approve <job-id> <source-approval-hash>
```

### 3. 生成建模代码

```powershell
antenna-workflow model-run <job-id> --through-stage boolean
antenna-workflow codegen <job-id>
```

主要输出：

| 文件 | 作用 |
| --- | --- |
| `generated_model_v001.py` | 不可变的第一版建模代码 |
| `generated_model.py` | 指向当前最新内容的稳定文件名 |
| `python_export_manifest_v001.json` | 记录输入工件、哈希和执行边界 |
| `parameters.json` | 参数及单位 |
| `materials.json` | 材料定义 |
| `solids.json` | 实体和拓扑 |
| `dimensions.json` | 坐标和尺寸关系 |

导入 `generated_model_v001.py` 不会启动 AEDT。只有主动调用其中的 `build(hfss)`，才会修改传入的 HFSS 设计。

## 在 HFSS 中查看模型

仓库提供两种方式。

### 方式 A：AEDT 内运行 wrapper

论文案例目录中包含 `run_in_aedt.py`。打开目标工程，然后在 AEDT 中选择：

```text
Tools > Run Script > run_in_aedt.py
```

wrapper 会新建一个唯一命名的 HFSS Design，只构建几何，不保存、不求解。

### 方式 B：外部 PyAEDT

```powershell
python .\tools\apply_generated_model.py `
  ".\.antenna-mcp\<job-id>\generated_model_v001.py" `
  --validate-only
```

去掉 `--validate-only` 前，应先打开目标 AEDT 工程和空的 HFSS Design。可以使用 `--expect-project` 与 `--expect-design` 防止连接到错误对象。

## 用户反馈与版本迭代

在 HFSS 中对照论文图片后，提交具体修改意见和可选截图：

```powershell
antenna-workflow feedback <job-id> `
  "馈线需要向左移动 0.5 mm；不得修改已经审核的其他尺寸" `
  --comparison-image ".\hfss-comparison.png"

antenna-workflow regenerate <job-id>
```

系统会生成 `generated_model_v002.py`，不会覆盖 `v001`。反馈附件会复制到任务目录并记录 SHA-256。

## 可选：仿真和自动优化

模型确认后，才能考虑端口、边界、空气区域、网格、扫频与优化范围。这些信息不能仅凭结构图片静默猜测。

执行 HFSS 前需要最终审核哈希和显式执行门：

```powershell
antenna-workflow artifact-review <job-id>

$env:ANTENNA_MCP_ALLOW_SIMULATION = "1"
antenna-workflow hfss-build <job-id> <artifact-approval-hash> `
  --project-name "antenna.aedt"
```

优化器会复制输入工程，在隔离任务目录中运行试验：

- 每次试验追加到 `trials.jsonl`；
- 当前最优参数写入 `best.json`；
- 最优工程单独保存；
- 原始 `.aedt` 文件不会被覆盖。

完整参数格式见 [`docs/PIPELINE.md`](docs/PIPELINE.md)。

## MCP Server

启动 stdio MCP Server：

```powershell
leam-opt-mcp
```

MCP 客户端配置示例：

```json
{
  "mcpServers": {
    "leam-opt": {
      "command": "C:/path/to/repository/.venv/Scripts/python.exe",
      "args": ["-m", "antenna_mcp.server"],
      "env": {
        "ANTENNA_TEXT_PROVIDER": "deepseek",
        "ANTENNA_VISION_PROVIDER": "ollama",
        "DEEPSEEK_API_KEY": "${DEEPSEEK_API_KEY}",
        "OLLAMA_VISION_MODEL": "qwen3-vl:8b",
        "ANTENNA_MCP_WORKSPACE": ".antenna-mcp"
      }
    }
  }
}
```

主要 MCP 工具：

- `analyze_antenna_source`
- `refine_antenna_source`
- `approve_antenna_source`
- `generate_antenna_python`
- `submit_antenna_model_feedback`
- `regenerate_antenna_python_from_feedback`
- `build_hfss_project`
- `create_hfss_optimization_job`
- `run_hfss_optimization_job`
- `get_antenna_job`

## 论文复现案例

[`examples/leam_paper_cases`](examples/leam_paper_cases) 包含四个离线案例：

| 案例 | 来源 | 当前状态 |
| --- | --- | --- |
| `demo_l_slot` | Fig. 3 | 图中导体尺寸已解析 |
| `case1_vivaldi` | Fig. 4 | 拓扑和公开尺寸已解析；样条点为显式假设 |
| `case2_slotted_patch` | Fig. 5 | 公开尺寸已解析；基板、馈线等保留工程假设 |
| `case3_monopole` | Fig. 7 | 七实体拓扑、修正式和 FR-4 数据已整理；铜厚为显式假设 |

论文 PDF 不随仓库分发。请把合法获取的 PDF 放入案例的 `references/`，或在请求文件中填写自己的本地路径。

## 项目结构

```text
src/antenna_mcp/       核心 Python 包与 MCP Server
examples/              论文案例、证据与生成代码
tools/                 AEDT native/PyAEDT 执行适配器
tests/                 不需要 API、AEDT 或许可证的测试
docs/                  架构、完整流程和发布说明
.github/               CI、Dependabot 与 Issue 模板
```

## 测试和打包

```powershell
pytest
python -m build
```

测试默认使用 fake provider 和 fake HFSS 对象，不会调用云端模型，不会启动 AEDT，也不会消耗 HFSS 许可证。

## 安全与工程边界

- 大模型生成的 Python 不是安全沙箱，执行前必须人工检查；
- AST 检查只能阻止部分明显危险构造，不能证明电磁模型正确；
- 图片相似不代表端口、边界、网格、频扫和材料设置正确；
- 自动优化依赖有效的 AEDT 安装、HFSS 许可证和可求解工程；
- 项目不提供、修改或绕过任何 Ansys 许可证；
- `.env`、API Key、论文 PDF、`.aedt` 和求解结果默认不提交 Git。

更多信息：

- [系统架构](docs/ARCHITECTURE.md)
- [完整命令行流程](docs/WORKFLOW.md)
- [离线代码生成](docs/OFFLINE_CODEGEN.md)
- [GitHub 发布清单](docs/GITHUB_RELEASE.md)
- [安全策略](SECURITY.md)
- [贡献指南](CONTRIBUTING.md)

## 上游工作与资料

- [LEAM](https://github.com/TaoWu974/LEAM)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [PyAEDT](https://github.com/ansys/pyaedt)

本项目不复制 LEAM 源码，而是实现相似的分阶段、可检查建模思想，并增加 MCP、离线代码版本、人工反馈和 HFSS 优化层。

## License

[MIT](LICENSE)
