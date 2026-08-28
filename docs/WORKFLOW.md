# 从论文到可反馈 Python 模型

下面是推荐的通用离线流程。除最后单独标出的 HFSS 步骤外，它不会启动 AEDT，也不需要许可证。

## 1. 安装和配置

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,local-vision]"
Copy-Item .env.example .env
ollama pull qwen3-vl:8b
```

PowerShell 不会自动读取 `.env`。请在当前终端设置环境变量，或由 MCP 客户端在 server 配置的
`env` 字段注入。密钥只放在本机环境变量中，不要写进 JSON、Python、README 或截图。

```powershell
$env:ANTENNA_TEXT_PROVIDER = "deepseek"
$env:DEEPSEEK_API_KEY = "<your-key>"
$env:ANTENNA_TEXT_MODEL = "<your-text-model>" # 可选的运行时文本覆盖
$env:ANTENNA_VISION_PROVIDER = "ollama"
$env:OLLAMA_VISION_MODEL = "qwen3-vl:8b"
antenna-doctor
```

## 2. 创建并识别来源

```powershell
antenna-workflow model-create `
  --description "只复现目标图中的天线，不混用论文其他案例尺寸" `
  --template paper_reconstruction `
  --attachment "C:\path\to\paper.pdf"
```

保存返回的 `mdl-...`，然后运行视觉阶段：

```powershell
antenna-workflow model-run <job-id> --through-stage source_analysis
antenna-workflow source-refine <job-id> `
  --description "列出所有实体、尺寸、派生关系和未确定项"
```

检查候选、差异报告和 review packet。只有确认后才回传返回的哈希：

```powershell
antenna-workflow source-approve <job-id> <source-approval-hash>
```

## 3. 生成几何 Python

```powershell
antenna-workflow model-run <job-id> --through-stage boolean
antenna-workflow codegen <job-id>
```

`model-run` 会在写入阶段工件前检查 source/solids/dimensions/solver 的结构一致性、Python
阶段职责、本机支持的 PyAEDT API，以及端口 helper 对象/远场开关等跨阶段执行语义。任何一项失败都会停在最早出错阶段，且不会生成该阶段
工件。若模型已经返回内容但随后被门禁拒绝，原响应会保存为
`rejected_<stage>_vNNN.txt`，并配套 `rejected_<stage>_report_vNNN.json` 记录错误与
SHA-256；这些仅供审计，不会注册成有效阶段或执行。不要在状态为 `failed` 时运行
`codegen` 或 AEDT。

### 失败阶段续跑并切换文本模型

任务的 `request.model` 是创建时的审计记录，不应为了重试而手工编辑 `state.json`。例如旧任务
保存了视觉模型名 `qwen3-vl:8b`，并在 `model_3d` 失败，可显式覆盖本次进程的文本模型：

```powershell
$env:ANTENNA_TEXT_PROVIDER = "deepseek"
$env:ANTENNA_TEXT_MODEL = "<your-deepseek-text-model>"
$env:DEEPSEEK_API_KEY = "<your-key>"
$env:ANTENNA_VISION_PROVIDER = "ollama"
$env:OLLAMA_VISION_MODEL = "qwen3-vl:8b"

antenna-doctor
antenna-workflow model-run <job-id> --through-stage boolean
```

`ANTENNA_TEXT_MODEL` 只覆盖文本路由，并优先于任务中保存的 `model`；Ollama 视觉模型仍只由
`OLLAMA_VISION_MODEL` 控制。变量未设置或只含空白时，保持旧任务的原有模型选择。续跑会先
加载失败阶段之前的已有工件，再从失败阶段重新调用模型。

如果失败点并不是最早的错误（例如 `materials.json` 合同错误最终在 `model_3d` 才暴露），
不要手改 `state.json`。显式回退到最早需要重建的阶段：

```powershell
antenna-workflow model-retry <job-id> `
  --from-stage materials `
  --through-stage simulation_setup
```

重试前，系统会把被失效工件的原路径、大小和 SHA-256 写入不可变的
`model_retry_receipt_vNNN.json`。已审批的 source 链会先被重新校验并原地保留；所有
`generated_model_vNNN.py` 等版本化输出也会保留且不得改写。系统不删除旧文件，只从 job
状态中移除已过期的非版本化下游别名，然后复用更早阶段。已审批 source 不允许从
`source_analysis` 静默重建；这种情况必须新建 job 并重新审核来源。

若某个生成阶段被代码/API 门禁拒绝，随后直接执行 `model-run` 会从该失败阶段续跑，并把
上一次的 fail-closed 诊断作为不可信错误数据反馈给文本模型，使其修正诸如 `numSides` →
`num_sides` 这类明确的接口问题；更早的已验证工件不会重复生成。

当错误涉及对象关系、阶段越界或 PyAEDT 调用时，应从最早受影响的结构化阶段重试。例如
patch 的 `parent_layer` 或 Region 的 `boundary` 缺失时从 `solids` 开始，而不是只重做
`model_3d`。

输出目录会出现：

- `generated_model_v001.py`：不可变版本；
- `generated_model.py`：最新版本别名；
- `run_in_aedt_v001.py`：AEDT 内运行对应几何版（`boolean`）模型的安全入口；
- `run_in_aedt.py`：AEDT 内运行最新几何版模型的稳定入口；
- `python_export_manifest_v001.json`：输入工件哈希和许可证边界。

导入该 Python 文件不会启动 AEDT。它只暴露 `build(hfss)`，真正修改模型必须显式传入 HFSS
对象。`codegen` 生成的 `run_in_aedt_vNNN.py` 可通过 AEDT 的 **Tools > Run Script**
在新建 Design 中构建几何；它复用带哈希校验的统一 native adapter，不保存、不求解。
不要在 AEDT 中直接运行 `generated_model_vNNN.py`。
若导出包含 `simulation_setup`，系统不会生成或覆盖 native wrapper；native adapter
不支持 setup、端口和边界 API，这类完整仿真工件必须通过外部 CPython/PyAEDT 执行，
以避免先创建部分几何后再失败。

## 4. 对照和反馈

用户在 HFSS 中对照来源图片后提交具体差异：

```powershell
antenna-workflow feedback <job-id> `
  "馈线应左移 0.5 mm；保持所有已审核尺寸和材料不变" `
  --comparison-image ".\hfss-comparison.png"
antenna-workflow regenerate <job-id>
```

系统生成 `generated_model_v002.py`，不会覆盖 `v001`。重复此步骤即可形成可追溯的人工反馈闭环。

## 5. 可选 HFSS 构建与优化

先审核端口、边界、网格、扫频、优化变量和目标，再显式开启执行：

```powershell
$env:ANTENNA_MCP_ALLOW_SIMULATION = "1"
antenna-workflow artifact-review <job-id>
antenna-workflow hfss-build <job-id> <artifact-approval-hash> `
  --project-name "antenna.aedt"
```

对已有工程的优化请求可写入 JSON，并执行：

```powershell
antenna-workflow optimization-create .\optimization_request.json
antenna-workflow optimization-preflight <optimization-job-id>
antenna-workflow optimization-run <optimization-job-id>
```

`optimization-preflight` 不求解，只检查每个优化变量在上下界探针值处是否真的改变模型
几何。正式运行会复制输入工程并使用任务唯一的工作工程名，不覆盖原文件；每次试验立即
追加到 `trials.jsonl`，当前最优写入 `best.json`。默认要求自适应解达到指定 Delta S、
扫频收敛，未收敛试验会记录为 `rejected` 且 `score=null`，不会成为最优。中断后再次运行
会从已有 JSONL 继续；已完成任务重复运行是幂等的，不会重复求解。

优化请求应显式写出 `SetupName : SweepName`、`require_convergence`、`max_delta_s`、
`maximum_adaptive_passes` 和 `verify_parameter_effects`。可运行示例及真实 12 组回归结果见
[`examples/validation/ansys_pyaedt_probe_patch`](../examples/validation/ansys_pyaedt_probe_patch)。
