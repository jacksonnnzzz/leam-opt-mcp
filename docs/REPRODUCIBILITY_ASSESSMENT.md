# 论文来源可复现性评价

系统在 `source_analysis` 完成后自动生成：

- `reproducibility_assessment.json`：机器可读评分、逐项证据状态和工作流策略；
- `reproducibility_report.md`：供研究者审核的可读报告。

该评价回答的是“当前来源是否足以唯一地定义一个 HFSS 重建”，不是“论文是否正确”，也不是
“候选模型已经实现电磁等价”。后者仍必须通过同版本 HFSS 求解和冻结的论文目标门槛。

## 分项来源提取（可选）

新任务可通过 `model-create --source-extraction-mode split` 启用四个顺序请求：
几何、材料、激励/求解、结果/证据追踪。Python API 的 `ModelingRequest` 和 MCP
`create_antenna_modeling_job` 接受同名 `source_extraction_mode="split"`。
默认仍为 `single`，旧任务无需迁移；此版本不自动改写既有任务的请求。

各分项只拥有指定证据 ID。几何建立组件身份；材料按名称核对材料并提供材料参数；其余分项
不重新创建组件。程序确定性合并，不再调用模型自由重写全稿。分项缺字段、重复实体、
共享参数值/单位/含义冲突、材料冲突时不会自动删除或覆盖来获得有效输出。
合并后仍经过原来源结构、拓扑和冻结附件合同检查。

每次尝试保存 `source_split_vNNN_report.json` 与各分项原始输出，记录请求/附件/提示哈希、
失败位置和错误。失败分项不会发布为 `source_analysis`，也不生成新的评估分数。
手动重跑创建新版本并重新提取四项，不跨次缓存复用分项。
默认每项仍读取原附件，不会自动删页或截断文本。可以显式指定几何专用附件，详见下节。

### 几何专用输入与结构约束

`model-create --source-extraction-mode split --geometry-attachment <path>` 可重复指定几何
附件（API/MCP 字段 `geometry_attachments`）。它们仅替换几何分项的输入；材料、求解和验证
仍使用原 `attachments`。未指定时保持原行为；single 模式拒绝此参数。缩减资料时应保留
尺寸图、图注、尺寸表及必要的层叠/厚度上下文，记录页码，不要只保留想要的数值。

几何采用专用简短指令，不再复用所有来源分析职责的长指令。后续分项只接收组件名称、角色、
材料作为身份上下文，不重复传入整份几何分析。几何响应必须符合共享的 Pydantic/JSON Schema：
组件、参数必填字段、置信度类型及范围、坐标结构和证据状态均受约束；响应只校验而不强制转换
或补值。原有重复实体、证据矛盾、关系和冻结合同检查继续生效。

Ollama 通过原生 `format` 接收 JSON Schema，而非仅设置 `format="json"`；结构化视觉请求中
将提取要求放在来源文本之后。其他尚未实现原生结构化调用的适配器使用提示中的 Schema 和
本地校验，不声称提供了原生解码约束。该接口依据
[Ollama Structured Outputs](https://docs.ollama.com/capabilities/structured-outputs)。

报告 1.2 保存几何 Schema、每分项实际附件路径/大小/哈希和选择方式。Schema 保证结构的能力
不等于语义正确性：同名或换名重复实体、漏组件、错读尺寸、错误引用仍须检查。精简附件后
“未发现”只针对所给片段，不可据此断言整篇论文缺失该信息。

### 几何三子请求（可选）

新建任务同时指定 `--source-extraction-mode split --geometry-extraction-mode staged`，
或 API/MCP 字段 `geometry_extraction_mode="staged"`，把原几何请求替换成三个顺序请求：

1. geometry_entities：摘要、天线类型、坐标、组件、布尔操作和该项不确定信息。
2. geometry_parameters：参数、推导关系和该项不确定信息；不得返回组件。
3. geometry_evidence：五个几何证据项和该项不确定信息；不得重写实体或参数。

三个请求读取相同的几何附件，各自使用严格 Schema 和独立输出预算。后两项收到前项提取作为
待核对上下文，不视其为原文。程序按字段归属合并，不调用模型改写总稿；不确定信息合并保留，
矛盾不会自动删除。合并后仍经过完整 GeometryOutput、现有来源校验，再进入材料等阶段。

默认 `geometry_extraction_mode="single"` 保持旧四请求行为。staged 仅在 split 模式可用，
完整来源提取变为六个请求；各最多两次格式纠错，最坏十八次调用。预算截断仍直接停止。
报告 1.4 记录三个子请求和 geometry_merge；每次原始输出、Schema、统计独立保存。
已通过结构检查的中间输出并不是已批准来源，不直接评分。子请求或合并失败均不发布新来源。

### 请求预算与统计

正式 Ollama 适配器对所有阶段设置 `OLLAMA_NUM_PREDICT`，默认 4096，允许 1–32768。
不接受 0 或 -1 等无上限设置。该上限不保证给定答案一定装得下；太小会导致安全停止。
`OLLAMA_TIMEOUT_SECONDS` 仍是连接/读取超时，不是精确的整个流程墙钟截止时间。

`done_reason=length`（以及兼容的 max_tokens/max_output_tokens）抛出
`LlmOutputTruncatedError`，即便响应碰巧能解析为 JSON 也拒绝接收；`done=false` 或错误响应
同样拒绝。截断是预算不足，不进入分项格式纠错循环、不自动提高预算，也不发布新的来源或评分。
原有已保存历史产物不会因此删除；应依据最新状态和版本记录判断本次是否成功。

分项报告 1.3 的每次尝试记录 `request_metadata`；非分项建模阶段使用独立版本的
`llm_<stage>_vNNN.json`。包含模型、实际输出/上下文上限、请求哈希、结束原因、token 计数和
耗时。wall_seconds 为客户端秒数，服务返回的 *_duration 为纳秒。缺失字段表示服务未提供，
不是零。仅记录已收到的统计；超时可能没有服务端 token 数据。诊断不包含正文或自由推理文本。

该输出预算及统计目前针对 Ollama；其他适配器没有因此获得同样的结束原因检查。

### 分项校验纠错

分项输出解析或校验出现 `ValueError`（包括已覆盖的证据矛盾）时，将错误、上一份原始输出及
原分项约束反馈给模型，继续读取相同附件。每项最多两次纠错，即含首次最多三次调用；默认四项
全部用满时最多十二次调用，几何 staged 模式最多十八次。只重试当前失败分项，已通过分项在本次调用内保持不变。

模型必须返回完整替换输出并再次经过相同检查。程序不填充缺失字段、不猜测物理参数、不提高
证据等级或置信度。`confidence` 是模型对提取的自评，不是论文给出的物理事实，也不是已校准概率。
网络、超时、模型服务故障及非校验程序错误不进入纠错循环；最终合并冲突和后续来源合同失败
也不会自动重写已通过分项。达到上限仍失败时，停止且不发布新的来源或分数。

报告格式 1.1 增加各分项的 `attempts` 和 `accepted_attempt`。每次调用前保存实际提示词，
返回后保存原始响应、哈希及校验错误。首次仍使用 `_geometry.txt` 等文件名，后续纠错使用
`_geometry_attempt_002.txt` 等独立路径，历史输出不覆盖。结构通过不代表原文事实已核实，
修正后的实体、参数、引用和缺口仍需审核。

## 计分前的一致性检查

通用检查在来源接收及直接评分入口生效：

- 重复组件名称、重复参数符号；
- `explicit`/`derived` 与首句主要断言中已覆盖的“not specified / 未提供”等缺失描述矛盾；
- 结构化缺口通过 `criterion_id` 或 `missing_<criterion_id>` 指向已声明明确的证据项；
- 结构化缺口通过 `parameter_symbol` 声称已给数值的参数缺失。

矛盾抛出 `EvidenceConsistencyError`，给出逐项路径和原因，不返回一个修改过的分数。
缺失信息应标为 missing，而不是让模型先标 explicit 再由程序猜测改成 missing。
这一层是保守规则检查，不是语义理解或引用真实性证明：同义改写、后续句子及自然语言缺口
仍可能漏检；判断不同方案的语境也需要审核。报告的 `source_citations_verified=false`
明确表示评分器并未自动核验页码引用。旧任务的兼容推断仍只能用作初筛。

评分权重不变。模型输出、助手页审、独立人工金标准和真实 HFSS 验证必须继续分别报告。

## 固定评分规则

总权重为 100 分，程序固定计算，LLM 不得提供总分或等级：

| 维度 | 权重 | 内容 |
|---|---:|---|
| 几何完整性 | 30 | 总体尺寸、组件尺寸、坐标、层叠和布尔拓扑 |
| 材料完整性 | 15 | 介电常数、损耗角、基板厚度、导体材料和厚度/薄片模型 |
| 馈电与激励 | 15 | 馈电几何、端口类型、端口面、参考导体/积分线 |
| 边界与求解 | 15 | 空气域、边界、网格、收敛条件和扫频 |
| 验证证据 | 15 | 谐振、完整频带、数值 S 参数、辐射指标和参考曲线 |
| 证据追踪 | 10 | 图表、公式、歧义披露和跨来源一致性 |

每个检查项只能是：

- `explicit`：来源直接明确，获得 100% 权重；
- `derived`：可由有证据的公式唯一推导，获得 75%；
- `assumed`：工程假设，获得 25%；
- `missing` 或 `conflicting`：不得分；
- `not_applicable`：从有效总权重中排除，必须给出来源与理由。

等级及默认建议：

| 等级 | 分数 | 建议 |
|---|---:|---|
| A | 85–100 | 来源足以直接重建；审核后可考虑自动执行和优化 |
| B | 65–84.99 | 条件性重建；必须审核并批准工程假设 |
| C | 0–64.99 | 只生成候选代码；不得据此宣称论文复现成功 |

任何等级都不自动允许“电磁复现成功”的结论。

## 命令行

审核一个已有任务，并把报告写入任务目录：

```powershell
antenna-workflow reproducibility-audit --job-id mdl-xxxxxxxxxxxx
```

只读审核一个独立的 `source_analysis.json`：

```powershell
antenna-workflow reproducibility-audit `
  --source-analysis "D:\path\to\source_analysis.json"
```

MCP 客户端使用 `assess_antenna_source_reproducibility`，并且必须在 `job_id` 与
`source_analysis_path` 中恰好提供一个。

## 兼容旧任务

新生成的 `source_analysis` 包含固定检查项的 `reproducibility_evidence`。旧任务没有该字段时，
系统使用保守的确定性兼容审计，并把 `extraction_mode` 标为
`deterministic_fallback`。兼容结果适合定位缺口；要形成正式实验数据，应重新运行来源提取或由
研究者提交结构化证据审计。
