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
# 可选：只覆盖本次进程使用的文本模型，包括续跑已有任务。
$env:ANTENNA_TEXT_MODEL = "<your-text-model>"

$env:ANTENNA_MCP_WORKSPACE = ".antenna-mcp"
```

环境变量只对当前 PowerShell 进程及其子进程生效。不要把真实 API Key 写入 README、配置样例、任务 JSON 或 Git 提交。

`ANTENNA_TEXT_MODEL` 是显式的运行时覆盖项，优先级高于任务创建时保存的
`request.model`，但不会改写任务 JSON。它只传给 `ANTENNA_TEXT_PROVIDER`；本地视觉仍由
`ANTENNA_VISION_PROVIDER` 和 `OLLAMA_VISION_MODEL` 独立控制。没有设置该变量时，继续使用
原有的任务模型/提供方默认模型行为，因此旧任务和旧配置保持兼容。

UTF-8 JSON、Markdown、CSV 等纯文本附件会在明确的不可信证据边界内嵌入文本提示，并由
`ANTENNA_TEXT_PROVIDER` 处理；图片和 PDF 才路由至 `ANTENNA_VISION_PROVIDER`。文本附件的
总字符数默认限制为 250000，可用 `ANTENNA_TEXT_ATTACHMENT_MAX_CHARS` 调整。

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

生成流程在落盘前同时检查跨阶段结构一致性、model/boolean/simulation 职责隔离，以及
PyAEDT 0.26.3 关键方法和参数名。`failed` 状态下不要运行 `codegen` 或 AEDT；`completed`
也只代表生成门禁通过，最终正确性仍需 benchmark contract 和独立 HFSS/S11 验证。

如果旧任务曾把 `qwen3-vl:8b` 保存到 `request.model`，并在 `model_3d` 阶段失败，可在
同一个 PowerShell 中只覆盖续跑所用的文本模型：

```powershell
$env:ANTENNA_TEXT_PROVIDER = "deepseek"
$env:ANTENNA_TEXT_MODEL = "<your-deepseek-text-model>"
$env:DEEPSEEK_API_KEY = "<your-key>"
$env:ANTENNA_VISION_PROVIDER = "ollama"
$env:OLLAMA_VISION_MODEL = "qwen3-vl:8b"

antenna-doctor
antenna-workflow model-run <job-id> --through-stage boolean
```

续跑会复用失败阶段之前已经验证并保存的工件，从失败的 `model_3d` 重新开始；文本覆盖
不会把 Ollama 的视觉模型改成 DeepSeek 模型。

主要输出：

| 文件 | 作用 |
| --- | --- |
| `generated_model_v001.py` | 不可变的第一版建模代码 |
| `generated_model.py` | 指向当前最新内容的稳定文件名 |
| `run_in_aedt_v001.py` | 在 AEDT 内运行对应几何版（`boolean`）模型的入口 |
| `run_in_aedt.py` | 在 AEDT 内运行当前最新几何版模型的稳定入口 |
| `python_export_manifest_v001.json` | 记录输入工件、哈希和执行边界 |
| `parameters.json` | 参数及单位 |
| `materials.json` | 材料定义 |
| `solids.json` | 实体和拓扑 |
| `dimensions.json` | 坐标和尺寸关系 |

导入 `generated_model_v001.py` 不会启动 AEDT。只有主动调用其中的 `build(hfss)`，才会修改传入的 HFSS 设计。

## 在 HFSS 中查看模型

仓库提供两种方式。

### 方式 A：AEDT 内运行 wrapper

`codegen --through-stage boolean` 会在该任务目录自动生成 `run_in_aedt_vNNN.py`
和 `run_in_aedt.py`；论文案例目录也包含相同形式的入口。打开目标工程，然后在
AEDT 中选择：

```text
Tools > Run Script > run_in_aedt.py
```

wrapper 会调用仓库或安装包中统一维护、带哈希校验的 native adapter，新建一个
唯一命名的 HFSS Design，只构建几何，不保存、不求解。不要直接选择
`generated_model_vNNN.py`。若移动任务目录，应保留完整 GitHub 仓库，或重新运行
`codegen` 以记录当前安装位置的 adapter。

包含 `simulation_setup` 的导出不会生成或覆盖 native wrapper，因为 native adapter
只实现几何接口，不实现 setup、端口或边界 API。此类完整仿真代码必须使用下方的
外部 CPython/PyAEDT 方式执行。

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

论文参考模型尚未通过论文门槛时，先使用版本化工程假设搜索。它会锁定论文明确参数，
只改变标记为“来源未披露”的端口、导体、介质或边界假设，并把每次 S11、收敛证据、
哈希和排名保存为不可覆盖版本：

```powershell
antenna-workflow assumption-plan `
  --space ".\examples\validation\wifi_patch_5250\assumption_space.json" `
  --output-dir ".\examples\validation\wifi_patch_5250\local_results\assumption_search_v1" `
  --limit 10
```

完整的 AEDT 附加、恢复和失败重试命令见
[`docs/ASSUMPTION_SEARCH.md`](docs/ASSUMPTION_SEARCH.md)。只有工程假设版本通过参考门槛
G3 后，才能进入独立候选 G4/G5 和后续性能优化。

执行 HFSS 前需要最终审核哈希和显式执行门：

```powershell
antenna-workflow artifact-review <job-id>

$env:ANTENNA_MCP_ALLOW_SIMULATION = "1"
antenna-workflow hfss-build <job-id> <artifact-approval-hash> `
  --project-name "antenna.aedt"
```

优化器会复制输入工程，在隔离任务目录中运行试验：

- 先验证每个优化变量确实会改变 HFSS 几何；
- 未通过自适应收敛或扫频收敛门禁的试验不会参与最优排名；
- 每次试验追加到 `trials.jsonl`；
- 当前最优参数写入 `best.json`；
- 最优工程单独保存；
- 原始 `.aedt` 文件不会被覆盖；
- 中断后可从已有试验继续，已完成任务重复执行不会再求解。

建议先执行不求解的预检，再正式运行：

```powershell
antenna-workflow optimization-create .\optimization_request.json
antenna-workflow optimization-preflight <optimization-job-id>

$env:ANTENNA_MCP_ALLOW_SIMULATION = "1"
antenna-workflow optimization-run <optimization-job-id>
```

官方探针贴片已完成一次 12 组真实 HFSS 回归：12/12 组均收敛，目标小频段内最差
S11 从 −9.9348 dB 改善到 −11.4999 dB，源工程 SHA-256 前后一致。可复核记录见
[`optimization_study_2026_08_28.json`](examples/validation/ansys_pyaedt_probe_patch/reference_data/optimization_study_2026_08_28.json)。

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
- `preflight_hfss_optimization_job`
- `run_hfss_optimization_job`
- `validate_antenna_model`
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

## 正确性验证

论文几何复现案例与正确性基准严格分开。仓库以 Ansys 官方 PyAEDT 探针馈电
贴片作为已求解本地基线，并加入三篇开放论文的六个独立参考设计；它们都可分别
执行离线结构检查和完整 S11 对比：

```powershell
antenna-workflow validate `
  --benchmark ".\examples\validation\ansys_pyaedt_probe_patch\benchmark.json" `
  --candidate ".\examples\validation\ansys_pyaedt_probe_patch\candidate_contract.example.json" `
  --contract-only `
  --report ".\tmp\probe-patch-contract-report.json"
```

没有参考与候选两条 S11 CSV 时，完整验证只会返回 `incomplete`，不会把几何相似
误报为电磁正确。完整格式与本地参考求解流程见
[`docs/VALIDATION.md`](docs/VALIDATION.md) 和
[`examples/validation`](examples/validation)。

多论文验证活动的当前状态、论文目标、未决假设和下一道验收门槛见
[`examples/validation/CAMPAIGN.md`](examples/validation/CAMPAIGN.md)。其中离线测试通过、
HFSS 参考求解通过和独立生成候选通过是三个不同结论，不会互相替代。
面向汇报的总体结论见
[`examples/validation/CORRECTNESS_REPORT.md`](examples/validation/CORRECTNESS_REPORT.md)。
7 个“一案例一文件夹”的运行入口见
[`examples/validation/cases/CASE_INDEX.md`](examples/validation/cases/CASE_INDEX.md)。

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
