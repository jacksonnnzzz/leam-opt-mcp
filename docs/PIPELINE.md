# 通用天线建模与自动优化 Pipeline

Case 3 只是回归样例。系统入口是 `create_antenna_pipeline`，它不绑定某一种
天线结构，输入可以是自然语言、尺寸图、照片、论文 PDF 或这些内容的组合。

## 状态机

```text
created
  -> source_analysis
  -> source_refinement
  -> source_review_hash approval
  -> engineering_assumption proposal (only for null/unresolved source values)
  -> engineering_assumption review_hash approval
  -> parameters/materials/solids/dimensions
  -> model_3d/model_2d/boolean
  -> simulation_spec/simulation_setup
  -> optimization_spec
  -> awaiting_review
  -> HFSS build
  -> ready_to_optimize
  -> baseline trial + online surrogate optimization
  -> completed
```

系统有两个显式执行门：

1. 生成阶段不会启动 HFSS。所有 JSON 和 PyAEDT 代码都会写入任务目录。
2. `generate_antenna_pipeline` 返回 `approval_hash`。只有用户检查工件并把完全相同的
   哈希传给 `build_approved_antenna_pipeline`，HFSS 才会执行；任何工件修改都会让
   原哈希失效。真正求解还要求 `ANTENNA_MCP_ALLOW_SIMULATION=1`。

视觉输入另有一个更早的证据门：原始 `source_analysis.json` 先由文本模型结合 PDF 提取正文
校正为 `source_analysis_candidate.json`，系统检查重复/遗漏参数并生成差异报告。用户检查后
必须用 `source-approve` 提交候选与报告的联合哈希，才能生成下游使用的
`source_analysis_approved.json`。

纯文本请求也会调用配置的文本 provider 生成 `source_analysis.json`，不会再用空组件、空参数
占位。随后有一个不可绕过的跨阶段一致性门：`parameters.json` 必须逐项保留来源参数的
symbol/value/unit；`solids.json` 必须逐项保留来源组件的 name/role/primitive/material；
`materials.json` 必须且只能覆盖来源组件和 solids 引用的非空材料。任何遗漏、改名、数值或
单位变化、未经证据的新增项都会让 job 在对应 stage 进入 `failed`，下游代码不会生成。

下游还有三组不可绕过的离线门禁。`dimensions.json` 必须与 `solids.json` 一一对应并显式
保留 patch 所属层和 open-region boundary；`simulation_spec.json` 必须使用可比较的 HFSS
solver schema（design/setup/sweep/S 参数均不可缺失）。每个 Python fragment 还要同时通过：

1. 通用代码安全检查；
2. stage ownership 检查（model 只建 primitive、boolean 只做已审核布尔、simulation 只配求解）；
3. PyAEDT 0.26.3 关键接口静态检查，包括边界、端口、setup 和 sweep 的真实关键字；
4. 跨阶段执行语义检查，例如 helper 生成的端口帽不得提前建模、端口必须绑定审核过的
   源面，以及 `far_field.enabled=false` 时不得创建无限远球。

因此 `completed` 只会在这些生成门禁通过后出现；它仍不代替 benchmark contract 和 HFSS
电磁验证。若来源没有任何布尔操作，系统写入一个确定性的空 `boolean.py`，不会要求模型
编造 subtract/unite。

源证据门还会自动定位并裁切目标 PDF 图页，为组件和尺寸生成稳定的 `entity_id` / `claim_id`，
再执行文本校正和视觉否决式复核。确定性检查覆盖组件数量变化、低置信度核心几何、claim 绑定
冲突、视觉数值冲突和跨案例污染。审批哈希覆盖候选、报告、视觉审计、视觉判决及实际视觉输入。
如果本地小模型无法可靠解释复杂箭头，可显式传入工程师复核的 `visual_audit_path`；该文件仍会
进入哈希，候选也必须通过同一套绑定检查。`recheck_antenna_source` / `source-recheck` 可以把
候选确定性对齐到该审计，并在差异报告中列出每一处修复；它不会再次调用 LLM，也不会自动批准。

论文未给出的数值不能写回 `source_analysis_approved.json`。
`propose_antenna_engineering_assumption`（CLI 为 `model-assume-propose`）只生成独立的
`engineering_assumptions_candidate.json` 和审查哈希。它只能填充 `value=null` 且证据模式为
`unresolved` 的参数，不能覆盖图像或正文证据。用户检查候选并把完全相同的哈希传给
`approve_antenna_engineering_assumption`（CLI 为 `model-assume-approve`）后，系统才生成
`engineering_assumptions_approved.json` 和审批收据，并绑定批准源文件与源审查哈希。随后
`compile_reviewed_antenna_model`（CLI 为 `model-compile`）必须再次接收同一个工程假设审批哈希，
然后才可用确定性 profile 生成 HFSS
工件和几何检查报告；假设文件与可执行工件会一起进入最终构建哈希。

Case 3 示例：

```powershell
model-assume-propose mdl-xxxxxxxxxxxx CuT 0.035 --unit mm `
  --rationale "35 um copper is an engineering baseline; the paper leaves it unresolved."
model-assume-approve mdl-xxxxxxxxxxxx "返回的工程假设approval_hash"
model-compile mdl-xxxxxxxxxxxx --profile leam_case3 `
  --assumption-approval-hash "返回的工程假设approval_hash"
```

## MCP 调用顺序

### 1. 创建

```json
{
  "description": "根据附件复现该天线，并在目标频带内优化匹配和增益",
  "attachments": ["D:/papers/antenna.pdf", "D:/papers/dimensions.png"],
  "template": "paper_reconstruction",
  "project_name": "paper_antenna.aedt",
  "session_mode": "existing",
  "grpc_port": 50051
}
```

调用 `create_antenna_pipeline`，保存返回的 `pipe-...` ID。

### 2. 生成和审查

调用 `generate_antenna_pipeline(job_id)`。系统依次产生：

- `source_analysis.json`：图像文字、拓扑、尺寸、置信度和不确定项；
- `parameters.json`、`materials.json`、`solids.json`、`dimensions.json`；
- `model_3d.py`、`model_2d.py`、`boolean.py`；
- `simulation_spec.json`、`simulation_setup.py`；
- `optimization_spec.json`；
- `build_model.py` 和 `review_packet.json`。

检查所有 `executable: true` 的工件以及端口、边界、频率范围、优化变量和目标。检查
通过后，复制 `review.approval_hash`。

### 3. 构建

调用：

```json
{
  "job_id": "pipe-...",
  "approval_hash": "审查返回的64位哈希"
}
```

工具名为 `build_approved_antenna_pipeline`。成功后状态变为
`ready_to_optimize`，基线工程单独保存，不覆盖输入文件。

### 4. 求解和优化

设置：

```powershell
$env:ANTENNA_MCP_ALLOW_SIMULATION = "1"
```

调用 `optimize_antenna_pipeline(job_id)`。第一组 `initial_points` 应为当前基线设计；
后续采用拉丁超立方初始化和高斯过程 LCB 在线选点。每次 HFSS 结果立即追加到
`trials.jsonl`，当前最好参数写入 `best.json`，最好工程另存为独立 `.aedt`。

独立优化任务应先运行 `optimization-preflight`。预检会在不求解的情况下逐个改变变量并
比较全模型包围盒签名；任何变量没有几何效果时 fail closed。正式求解默认要求自适应
Delta S 和 sweep 同时收敛，未收敛或失败试验保存审计记录但不参加最优选择。输入工程、
请求和最优工程均保存 SHA-256；任务使用唯一工作工程名，支持基于 `trials.jsonl` 的确定性
续跑，且绝不覆盖源工程。

2026-08-28 的官方探针贴片真实回归完成 12/12 个收敛试验，把 9.9-10.1 GHz 内最差
S11 从 -9.9348 dB 改善到 -11.4999 dB。该结果证明上述执行和审计闭环可用，但 12 个点
不构成全局最优证明，也不能外推至其他天线。机器记录位于
`examples/validation/ansys_pyaedt_probe_patch/reference_data/optimization_study_2026_08_28.json`。

## 后端边界

- 图片识别可设置 `ANTENNA_VISION_PROVIDER=ollama`，使用本机 Ollama 与
  `qwen3-vl:8b`，无需云端视觉 API Key；PDF 通过 PyMuPDF 逐页渲染后作为图像输入。
  也可设置为 `openai` 并配置 `OPENAI_API_KEY` / `OPENAI_VISION_MODEL`。
- JSON、Markdown、CSV 等 UTF-8 文本附件不会因为“存在附件”就调用视觉模型；系统会给内容
  加上不可信证据边界并受 `ANTENNA_TEXT_ATTACHMENT_MAX_CHARS` 限制，然后交给文本提供方。
  只有包含图片/PDF 的请求（包括文本与视觉混合附件）才走视觉提供方。
- 文本规划与 PyAEDT 代码生成可设置 `ANTENNA_TEXT_PROVIDER=deepseek`，并通过
  `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL` 和 `DEEPSEEK_MODEL` 配置。当前 DeepSeek API
  模型不接收图片/PDF，因此附件阶段不能设置 `ANTENNA_VISION_PROVIDER=deepseek`。续跑旧任务
  时可设置 `ANTENNA_TEXT_MODEL`，让本次进程的文本模型覆盖任务中保存的 `request.model`；
  该变量不会传给视觉提供方，也不会改变 `OLLAMA_VISION_MODEL`。
- `session_mode=existing` 只允许严格连接已经以 gRPC 启动的 GUI，`grpc_port` 必须显式指定。
  系统会先验证该端口确实是活动 AEDT gRPC 会话；验证失败会直接拒绝，绝不回退为启动新 AEDT。
- `session_mode=new` 启动隔离的非图形 AEDT，会受到本机 HFSS 许可证状态约束。
- 端口、空气区域、网格、远场和报告表达式属于可审查生成工件，不从结构图中
  静默猜测。
- 当前在线代理模型为 GP-LCB；接口保留 `strategy` 字段，后续可以增加论文 [12]
  的 BNN-AdapLCB/差分进化实现而不改变 Pipeline 工具协议。
