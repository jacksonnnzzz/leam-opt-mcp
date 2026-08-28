# 天线自动建模系统正确性验证汇总报告

报告日期：2026-08-29

项目版本：`leam-opt-mcp 0.1.0`

验证范围：论文证据抽取、参数与拓扑合同、HFSS 参考模型、S11 论文门槛、独立生成候选对比

本地求解环境记录：AEDT 2025.1、PyAEDT 0.26.3

## 1. 执行摘要

当前活动共包含 **7 个参考设计**：1 个 Ansys 官方示例和 3 篇开放论文拆分出的
6 个论文设计。

| 指标 | 当前结果 | 可得出的结论 |
| --- | ---: | --- |
| 已完成论文/官方来源审计 | 7/7 | 参数、图片拓扑和响应目标已经冻结 |
| 已完成确定性建模脚本与离线测试 | 7/7 | 代码可以复查，缺失信息不会被冒充为论文参数 |
| 已完成 HFSS 参考求解 | 7/7 | 所有设计均得到可审计的本地 S11 与门槛判定 |
| 已完成 HFSS 参考求解并通过自身门槛 | 1/7 | 只有官方探针馈电贴片具备本地电磁基线证据 |
| 已完成 HFSS 参考求解但未通过论文门槛 | 6/7 | 六个论文案例均不得作为候选参考 |
| 已完成一轮项目独立候选工件生成 | 1/7 | 官方探针贴片已通过 `simulation_setup`、代码门禁和 124/124 冻结合同 |
| 已通过完整验证的独立自动生成候选 | 1/7 | 官方探针贴片已通过 G0-G5，同版本 S11 三项比较全部达标 |

因此，当前可以确认的是：**正确性验证框架、论文参数合同和参考建模代码已经建立；
官方示例的参考与独立生成候选均已在同一 AEDT 2025.1/PyAEDT 0.26.3 环境完成求解，
并通过完整 G0-G5。该结果证明系统至少已有一个端到端电磁正确性案例。两个 Yeo 贴片
均已完成本地参考与三组受控工程假设求解，但没有任何一组通过论文门槛；Wi-Fi 贴片的
来源校正版本及 10 组冻结工程假设变体全部收敛但均未覆盖完整论文频带；Kaur 三个设计
也全部未通过其完整 VSWR 门槛。**

## 2. “正确”的分层定义

本项目不以“模型看起来像”作为正确性结论，而使用六道门槛：

| 门槛 | 验证内容 | 通过含义 |
| --- | --- | --- |
| G0 来源证据 | 论文图片、正文、表格、材料和响应指标 | 输入证据可追溯 |
| G1 建模合同 | 参数、材料、对象、坐标、布尔操作、端口和求解设置 | 生成结果没有漏项、混参或静默推断 |
| G2 确定性实现 | Python 导入、几何调用、端口、边界、扫频和异常处理 | 参考构建器在接口层面可执行 |
| G3 参考电磁门槛 | 本地 HFSS 参考曲线与论文明确目标比较 | 该 HFSS 翻译可以成为本地参考基线 |
| G4 独立候选合同 | 图像和文本流程独立生成的候选与冻结合同比较 | 自动生成的结构和设置满足合同 |
| G5 独立候选电磁对比 | 同版本参考/候选 S11 的谐振、带宽、带边和 RMSE | 自动生成候选获得电磁正确性证据 |

只有 G0-G5 全部通过，才能把一个自动生成案例标记为“完整正确性验证通过”。

## 3. 案例状态矩阵

| # | 参考设计 | G0 | G1 | G2 | G3 HFSS 参考 | G4 独立候选 | G5 电磁对比 |
| ---: | --- | --- | --- | --- | --- | --- | --- |
| 1 | Ansys 官方探针馈电贴片 | 通过 | 通过 | 通过 | **通过** | **通过：124/124，代码门禁通过** | **通过：谐振 0.601%，带宽 0.974%，RMSE 0.931 dB** |
| 2 | Yeo 普通 inset-fed 贴片 | 通过 | 通过 | 通过 | **未通过：4 种 HFSS 翻译均失败** | 按策略关闭 | 按策略关闭 |
| 3 | Yeo 缩放槽加载贴片 | 通过 | 通过 | 通过 | **未通过：4 种 HFSS 翻译均失败** | 按策略关闭 | 按策略关闭 |
| 4 | El-Gendy 5.25 GHz Wi-Fi 贴片 | 通过 | 通过 | 通过 | **未通过：10 个假设变体均收敛，最佳最坏点 -8.020 dB** | 按策略关闭 | 按策略关闭 |
| 5 | Kaur UWB 无陷波基线 | 通过 | 通过 | 通过 | **未通过：全带最坏 S11 -1.244 dB** | 按策略关闭 | 按策略关闭 |
| 6 | Kaur WLAN 陷波设计 | 通过 | 通过 | 通过 | **未通过：带外失配且峰值偏移** | 按策略关闭 | 按策略关闭 |
| 7 | Kaur X-band 陷波设计 | 通过 | 通过 | 通过 | **未通过：带外失配且峰值偏移** | 按策略关闭 | 按策略关闭 |

两个 Yeo 贴片于 2026-08-26 在当前本地 AEDT 2025.1/PyAEDT 0.26.3 会话完成求解，因此
不再标为许可证阻塞。它们的“未通过”只针对冻结的论文电磁门槛：不能被提升为候选
比较基线。Wi-Fi 的 10 个假设变体与 Kaur 三案例也已求解但未通过各自完整门槛。至此 7 个参考设计均已
求解：1 个通过，6 个论文跨求解器翻译失败；失败不等于论文几何本身错误。

## 4. 已获得的 HFSS 数值证据

### 4.1 Ansys 官方探针馈电贴片

本地参考设计 `OfficialProbeFedPatch` 已在 AEDT 2025.1 中完成构建和求解：

| 检查项 | 目标 | 实际结果 | 结论 |
| --- | ---: | ---: | --- |
| 建模对象数 | 9 | 9 | 通过 |
| 边界 | Radiation、Perfect E、Wave Port | 全部存在 | 通过 |
| 自适应频率 | 10 GHz | 10 GHz | 通过 |
| 扫频 | 8-12 GHz | 8-12 GHz | 通过 |
| 目标谐振 | 10 GHz | 9.98 GHz | 通过 |
| 谐振相对误差 | <= 1% | 0.2% | 通过 |
| 最低 S11 | <= -10 dB | -16.1151 dB | 通过 |
| -10 dB 带边 | 有有效带宽 | 9.853843-10.111105 GHz | 通过 |
| -10 dB 带宽 | > 0 | 257.262 MHz | 通过 |

参考 S11 文件共有 401 个数据点，SHA-256 为
`31932398eb7711aea6ac97e6ce7a5ed58548906195f4d720d2f6e6457fe2766d`。
该结果先建立本地参考基线；独立自动生成候选的通过证据见 4.4。

在该合格基线上，2026-08-28 又完成了 12 组真实 HFSS 自动优化回归。优化前先验证
`Patch_length`、`Patch_width` 和 `probe_x_rel` 均会实际改变几何；12/12 个试验均满足
自适应 `Delta S <= 0.02` 且扫频收敛，无失败和无拒绝。第 9 组把 9.9-10.1 GHz 内最差
S11 从基线 -9.934836 dB 改善至 -11.499926 dB，同时把 10 GHz 点从 -15.475447 dB
改善至 -19.285516 dB。源工程运行前后 SHA-256 完全一致，最优工程另存并独立哈希。
机器可读证据为
[`optimization_study_2026_08_28.json`](ansys_pyaedt_probe_patch/reference_data/optimization_study_2026_08_28.json)。
这证明自动优化执行、收敛门禁、审计和源文件保护在一个合格基准上闭环可用；不证明
12 个样本找到了全局最优，也不能把该结论外推到未通过 G3-G5 的论文案例。

### 4.2 项目独立候选端到端尝试

2026-08-20 使用 GitHub 项目自身的 `antenna-workflow model-run` 流水线，从冻结的
官方探针贴片合同独立生成候选。第一次使用本机 `qwen3-vl:8b`、32K 上下文生成，
在 `model_3d` 阶段达到 900 秒上限并以 `TimeoutError: timed out` 结束；此次失败发生在
离线代码生成阶段，未启动 AEDT，与许可证无关。

本次端到端尝试发现并修复了两个项目缺陷：

1. Ollama provider 原先对所有阶段强制 `format: json`，与 `model_3d`、`model_2d`、
   `boolean` 和 `simulation_setup` 的 Python 输出合同冲突；现仅对结构化阶段启用 JSON grammar。
2. 失败任务重试原先会重新调用已经成功的前序 LLM 阶段；现会校验并复用失败阶段之前的
   任务内工件，从真实失败点续跑，同时清除陈旧错误状态。

随后在同一任务中把文本阶段切换到 DeepSeek，保留 Ollama `qwen3-vl:8b` 作为视觉模型，
从 `materials` 安全续跑至 `simulation_setup`。续跑先生成版本化 receipt，冻结旧工件的
路径、大小和 SHA-256，再复用已审核的 `source_analysis` 与 `parameters`；没有删除旧文件。
该次调用返回 `completed`，表示各阶段均产出了文件，**不表示候选已通过正确性验证**。

项目通用 job 合同装配器随后对完整候选执行第二版 G4：**103 项检查通过 90 项、失败
13 项（87.4%）**。分项结果为：参数 24/24、材料 5/5、操作 42/42、对象 15/22、
求解器 4/10。七个对象失败包括三个 stackup layer 被改写为普通 `box`、`Patch` 的
`rectangular_patch` 被改写为 `rectangle` 且缺少 `parent_layer=signal`、`Region` 的
`open_region` 被改写为 `box` 且缺少 `boundary=radiation`。六个求解器失败包括缺少
`design_type`、setup 类型错误、自适应频率不是结构化数值、扫频缺少 `start/stop`，以及
缺少 `s_parameter`。完整机器报告为任务目录中的 `validation_report_v002.json`；第一版
报告仍保留，没有被覆盖。

代码执行审计还发现两项独立硬失败：`model_3d.py` 和 `simulation_setup.py` 都错误地再次
定义 `def build(hfss)`，作为阶段片段拼装后只会定义内层函数而不会执行建模/仿真设置。
此外，ground subtraction 在 `model_3d` 和 `boolean` 中重复，`Probe_feed_outer` 被建成
实心圆柱而非尺寸合同中的环形圆柱，PEC port cap 与 `simulation_spec` 自己记录的 wave-port
电气冲突尚未解决。因此这套工件**不得进入 AEDT，也没有执行 G5**。

上述假阳性路径已经在项目代码中关闭：结构化阶段现在强制逐项保持 source analysis 的
参数和 component 合同；Python 阶段必须是立即作用于既有 `hfss` 的片段，禁止 `def`、
`class`、`lambda` 或重绑定 `hfss`。新任务遇到同类输出会在写入下游工件前失败，而不会再
仅凭“文件已生成”返回可接受候选。

随后从 `solids` 再次严格续跑。新工件已经保持九个 component 的名称、role、primitive 和
material，三个 Python 阶段也都改为立即执行片段；因此生成命令返回 `completed`。但第三版
G4 仍为 **104 项通过 96 项、失败 8 项（92.3%）**：对象 20/22，缺少
`Patch.parent_layer` 与 `Region.boundary`；求解器 5/11，缺少 `design_type`、setup type、
sweep start/stop 和 `s_parameter`，且 sweep 名称为 `Sweep` 而不是 `Sweep1`。参数 24/24、
材料 5/5、操作 42/42 均通过。机器报告为 `validation_report_v003.json`。

PyAEDT 0.26.3 静态审计进一步证明该候选仍不可运行：`create_rectangle/create_circle` 缺少
`orientation`，`create_cylinder` 错用 `axis=`，调用了不存在的 `get_face_by_position` 与
`assign_wave_port`，sweep 错用 `units=`。同时 `model_3d` 越界执行了 boolean、边界、端口、
setup 和 sweep，随后 `boolean` 与 `simulation_setup` 又重复执行；最终 builder 会重复减孔、
创建同名对象/边界/setup。整面 signal 铜层与 Patch 重叠、实心 vacuum 外柱与铜内导体重叠、
完整 PEC 圆片覆盖内导体且没有 integration line，也使端口物理定义未闭合。因此本次
`completed` 仍只表示工件生成结束，**没有进入 AEDT，也没有通过 G4/G5**。

针对该审计，流水线现已加入 stage ownership、dimensions/solver 结构和 PyAEDT 0.26.3
静态 API 三组 fail-closed 门禁，并强化相应生成提示。当前旧工件在只读复核中会分别被这些
门禁拒绝；门禁没有改写任务工件，也没有启动 AEDT。下一次从 `solids` 重试时，同类错误会
停在最早责任阶段，不能再以 `completed` 进入 codegen。

最新一次从 `solids` 的重试已验证这一行为：新 `solids.json` 正确保留了
`Patch.parent_layer=signal` 和 Region/port 等 boundary 关系，但模型生成的 dimensions
候选漏抄了 Patch 的 `parent_layer`。任务因此在 `dimensions` 阶段以
`StructuredContractError` 失败，候选未登记为当前 dimensions 工件，后续 Python 也没有生成。
磁盘上可能仍看到旧 `dimensions.json`，它只是“不删除旧文件”策略保留的历史内容，不在当前
state 的 artifacts 中。dimensions 提示合同现已全局强化为必须逐项复制 `parent_layer`、
`boundary`、层填充/实体材料和 `required_relationships`。

随后针对安装中的 PyAEDT 0.26.3 `Stackup3D` 源码进行的独立审计发现，问题实际还要上溯：
旧 `source_analysis` 没有保留 25% stackup 外扩、signal 层“空气填充体 + 铜 Patch”语义，
并把探针 X 位置错误写成了整段偏移；由此生成的 `solids` 把 signal 建成整块铜、把 Patch
放在 signal 顶面且降为零厚度，并让 Probe 穿过 signal 铜层。当前通用 Stackup 门禁会分别以
`patch_parent_elevation_mismatch`、`patch_parent_thickness_mismatch` 和
`probe_stackup_span_mismatch` 拒绝这些几何。因最早证据已经错误，**不得只从 dimensions
继续，也不得运行 AEDT**。冻结基准现已补入机器可读的 PyAEDT 0.26.3 `generation_evidence`；
下一步从 `source_analysis` 重新生成并先审核该阶段，不直接运行后续建模或 AEDT。

首次 source-only 重试（v004）又暴露出一个独立的通用路由缺陷：旧路由把任何非空附件都交给
视觉提供方，因此纯文本 `benchmark.json` 被错误发送给 Ollama，后者返回空响应。v004 保持
fail-closed，没有登记新的 source 或下游可变工件。路由现已按附件模态修正：UTF-8 JSON、
Markdown、CSV 等文本证据在大小限制和不可信内容边界内嵌给 DeepSeek；只有图片/PDF 或混合
视觉附件才调用 Ollama/OpenAI 视觉提供方。该失败任务可直接用 `model-run` 从当前
`source_analysis` 失败点恢复，无需再创建一份 retry receipt。

路由修复后的首次恢复已确认 DeepSeek 被正确调用，但其返回的
`coordinate_system.axes` 不是合同要求的数组，结构门禁再次在写入前拒绝。提示合同现已给出
精确示例 `{"plane":"XY","origin":[0,0,0],"axes":["X","Y","Z"]}` 并明确禁止 axes
对象/字符串。系统同时新增版本化 rejected-candidate 审计：今后任何已返回但未通过结构、代码
或 API 门禁的内容都会以 `.txt` 和带 SHA-256/错误类型的报告保存，但绝不会注册为有效阶段
或执行。当前这次响应发生在该审计功能加入之前，不能事后还原其原文。

第二次恢复修正了坐标格式，但把参数证据字段写成 `evidence` 而不是合同规定的
`evidence_source`，并同时把 `required_relationships` 写成自然语言、把空气填充的 signal
物理实体误写成铜。该响应已作为 `rejected_source_analysis_v001.txt` 和对应报告留档，仍未注册
或执行。系统现已对这类冻结 JSON 基准增加通用 source-contract 门禁：在 source 阶段一次性
核对组件集合、机器角色、primitive、材料/填充/实体语义、参数值和单位、关系字段以及完整操作
顺序。官方基准也已补入 Stackup3D 内部的 `signal - Patch`（`keep_originals=true`）操作，防止
后续生成整面信号铜层。该门禁由附件中的机器合同驱动，不含本案例名称或硬编码尺寸。

再次运行后，DeepSeek 已生成并登记一份包含 9 个组件、12 个参数和 14 个操作的
`source_analysis`；参数、对象集合、材料语义和操作合同均一致。但提交下游前的人工复核发现，
模型仍把精确几何范围压缩成 `geometric_evidence` 自然语言，并在 uncertainty 中把已经明确的
“signal 铜导体定义 + air 填充/实体”误称为语义含糊。该工件因此只代表当时结构门禁 completed，
不被批准进入 Python/AEDT。冻结 source contract 现已进一步提供每个组件的结构化几何对象，
source 阶段必须逐项复制，solids 阶段也必须原样保留；后续 dimensions 门禁才能对 Stackup Z、
Patch 厚度和 Probe 跨层范围作数值检查。

结构化几何重试（receipt v005）已正确返回上述几何、9 个组件和 14 个操作，但又把
`percentage_offset_argument=0.25` 从 producer 实现常数提升为第 13 个独立设计参数；冻结
`reference.parameters` 只有 12 项，因此 source contract 正确拒绝并保存为 rejected v002。
该常数仍可保留在 `derived_relations`，但不得进入可优化/可扫参的参数集合。提示与错误报告现已
明确这一区分；该失败任务应直接 `model-run` 恢复 source 阶段，无需新增 retry receipt。

随后直接恢复成功：当前 `source_analysis` 通过扩展 source contract 和 Stackup 拓扑门禁，包含
恰好 9 个组件、12 个参考参数、14 个操作和 X/Y/Z 坐标轴；每个组件的
`geometric_evidence` 均为结构化对象，signal/Patch/Probe/Region 关系与材料语义一致。该阶段现可
作为下游生成的冻结输入，但这仍不代表 Python 候选、AEDT 建模或电磁结果已经通过。

从 parameters 到 simulation_setup 的 v006 重试首次停在 dimensions。只读复核证明被拒绝的
dimensions v001 实际已完整给出 9 个对象的范围、原点、尺寸、半径和高度；失败来自通用验证器
自身：它错误要求 `Region.material` 必须为非空字符串，并且没有识别项目既有的嵌套
`dimensions` 数值容器。验证器现已允许上游明确为 null 的材料逐字保留，同时读取
`geometric_evidence`、`geometry` 或 `dimensions` 中的显式范围，并对 X/Y/Z 三轴防漂移。
原 v001 候选在修复后的门禁下只读复验通过，但仍保持 rejected 审计身份；任务应直接从当前
dimensions 失败点恢复，重新登记新候选后再进入 Python 门禁。

### 4.3 2026-08-26 独立候选离线验收更新

同一独立任务随后在 fail-closed 诊断反馈下从失败阶段逐步恢复。最终工件已完成
`model_3d`、`boolean`、`simulation_spec` 和 `simulation_setup`，并同时通过通用代码安全、
阶段职责、PyAEDT 0.26.3 API、端口 helper 语义和远场开关门禁。最终脚本使用 Terminal
求解、`Setup1/HFSSDriven/10 GHz`、`Sweep1/Interpolating/8-12 GHz`，端口绑定审核过的
`Probe_feed_outer.bottom_face_z` 并由 `create_pec_cap=True` 生成 PEC cap。

冻结 benchmark 的第四版 G4 报告结果为 **124/124 全部通过**：参数 24/24、材料 5/5、
对象 34/34、操作 48/48、求解器 13/13。完整候选已导出为不可变
`generated_model_v001.py`，并有 SHA-256 与 source-artifact manifest。该结果证明候选的
结构、拓扑和求解合同正确，**不证明候选 S11 正确**。`electromagnetic_results_validated`
仍为 false；下一步必须在与参考曲线相同的 AEDT 2025.1/PyAEDT 0.26.3 环境中构建、求解并
比较谐振误差、带宽误差和曲线 RMSE，才能完成 G5。

### 4.4 2026-08-26 独立候选 HFSS 求解与 G5

不可变导出脚本 `generated_model_v001.py` 已在用户打开的 `Project7 / CandidateProbePatch`
空 HFSS Terminal design 中构建。人工复查确认 9 个对象、Radiation、Perfect E、Wave Port、
`Setup1` 和 `Sweep1` 均存在。候选在 10 GHz 自适应求解中于第 11 个 pass 收敛：最终
`Max Mag. Delta S=0.013212`，低于 0.02 门槛；8-12 GHz interpolating sweep 同时报告
`converged and is passive`，求解为 `Normal Completion`。

只读导出器从 `Setup1 : Sweep1` 强制选择唯一 `dB(S(i,i))`，导出 401 个候选点；候选 CSV
SHA-256 为 `ad52401ee13aa7b7f015cbf5e6605425c27de792cac276af067d79366531ed1c`。
第五版完整验证报告为 `validation_report_v005.json`，结论如下：

| G5 检查 | 参考 | 候选 | 误差/门槛 | 结论 |
| --- | ---: | ---: | ---: | --- |
| 频率覆盖和比较点 | 8-12 GHz / 401 | 8-12 GHz / 401 | 覆盖率 100%，至少 20 点 | 通过 |
| 谐振频率 | 9.98 GHz | 10.04 GHz | 0.6012% <= 1% | 通过 |
| -10 dB 带边 | 9.853843-10.111105 GHz | 9.912146-10.171913 GHz | — | 记录 |
| -10 dB 带宽 | 257.262 MHz | 259.767 MHz | 0.9736% <= 5% | 通过 |
| 整条曲线 RMSE | — | — | 0.930577 dB <= 1 dB | 通过 |

最终机器判定为 `status=passed`、`quality_gate_passed=true`、
`validation_level=electromagnetic`。合同层仍为 124/124。因此该官方示例是本项目首个
从冻结证据、独立生成、Python 导出、HFSS 构建求解到同版本数值比较全部通过的 G0-G5 案例。

## 5. 六个论文设计的验收目标与当前状态

### 5.1 Yeo 2019 普通贴片

- RF-35：`er=3.5`、`tan(delta)=0.0018`、厚度 `0.76 mm`。
- 贴片 `40 x 31.9 mm`，馈线 `1.66 x 24.5 mm`，inset `2.8 x 9 mm`。
- 论文门槛：2.5 GHz 谐振，第一 -10 dB 频带 2.490-2.510 GHz。
- 2026-08-26 已求解原始 solid-copper/wave-port 翻译和三组受控变体；所有论文明确
  尺寸与 RF-35 参数保持不变。
- 原始翻译：2.468 GHz、-7.833 dB，无 -10 dB 频带。
- 最接近的 solid-copper/internal-lumped-port 变体：2.470 GHz、-10.061 dB，
  -10 dB 频带 2.469031-2.472113 GHz；谐振误差 1.20% > 1%，带边和带宽也失败。
- PEC/wave-port 与 PEC/lumped-port 变体同样未通过。因此 G3 失败，G4/G5 按策略关闭；
  不允许静默修改论文尺寸来追曲线。

### 5.2 Yeo 2019 缩放槽加载贴片

- 贴片 `31.8 x 25.4 mm`，馈线 `1.66 x 27.3 mm`，inset `2.3 x 12 mm`。
- 槽 `1 x 29.8 mm`，距辐射边 `1 mm`。
- 论文门槛：2.5 GHz 和 3.465 GHz 双谐振；第一 -10 dB 频带
  2.496-2.503 GHz。
- 图中 7 MHz 窄带仅约两个像素宽，因此严格门槛采用论文正文数值，数字化曲线只作形状审计。
- 2026-08-26 的基线第一模为 2.482 GHz、-12.307 dB，第一 -10 dB 频带为
  2.478674-2.486260 GHz；频带宽度接近，但两条带边均比论文低约 17 MHz，且 3.465 GHz
  附近没有第二局部最低点。
- solid/lumped、PEC/wave 和 PEC/lumped 三组受控变体同样无法同时恢复双模和窄带位置，
  因此 G3 失败、G4/G5 关闭。

### 5.3 El-Gendy 5.25 GHz Wi-Fi 贴片

- FR-4：`er=4.5`、`tan(delta)=0.025`、厚度 `1.5 mm`。
- `Lg=25.92`、`Wg=WR=34.44`、`LR=20`、`Lp=12.55`、`Wp=17.22`、
  `Xp=2.89 mm`。
- 论文门槛：5.15-5.35 GHz **整个频带**均满足 S11 <= -10 dB，而不只是中心点低于 -10 dB。
- 首次实现把 `Xp` 错当成中心基准；Figure 2 与公式 (3) 证明它是辐射边基准，已修正为
  `x=-Lp/2+Xp=-3.385 mm`，旧曲线仅作为被否决证据保留。
- 校正版本谐振 5.183 GHz、最低 S11 -19.320 dB，但完整频带最坏点为 -7.107 dB，
  因此 G3 失败；未披露 SMA/导体/边界仍是后续研究假设，不允许改 Table 1 尺寸。
- 2026-08-27 完成 10 组“一次只改一个未披露假设”的 AEDT 求解：导体模型、同轴介质、
  探针内外半径、馈线长度和辐射边界距离均有版本化结果；13 个论文参数的冻结哈希不变。
- 10/10 均通过自适应 `Delta S <= 0.02` 与 interpolating sweep 收敛检查，但 0/10 通过
  完整频带门槛。最佳为 `radiation_padding_mm=10.0`：谐振 5.196 GHz、最低 S11
  -22.1215 dB、完整目标频带最坏点 -8.0203 dB。
- 中途 6 组曾因 FlexNet `-97,121` 暂停，许可证恢复后全部重试完成；该基础设施故障未被
  计作物理失败。共享 AEDT 的全局空闲门禁也已加入，避免遗留求解在切换设计时被中止。

### 5.4 Kaur 2021 UWB 无陷波基线

- 18 x 18 x 1.6 mm FR-4，`er=4.4`、`tan(delta)=0.02`。
- `WP=13.5`、`LP=9`、`WG=7.95`、`LG=5.4`、`WF=1.2`、
  `LF=5.934`、`X1=6`、`Y1=1.5 mm`。
- 论文门槛：3-12 GHz 全带 VSWR <= 2，即 S11 <= -9.542425 dB。
- 修正 PyAEDT 0.26.3 的 XZ 端口尺寸顺序后正常求解 901 点；3-12 GHz 全带最坏
  S11 为 -1.244 dB，因此 G3 失败。

### 5.5 Kaur 2021 WLAN 陷波设计

- 只允许使用 `R1=2.4`、`R2=2.1`、`S1=0.4 mm`，不得混入 X-band 参数。
- 论文门槛：5.15-5.81 GHz 全陷波区 VSWR >= 2；峰值中心约 5.3 GHz。
- 额外门槛：峰值中心误差 <= 0.15 GHz，模拟峰值 VSWR 相对误差 <= 20%。
- 实际陷波区保持高反射，但峰值落在 5.81 GHz（目标 5.3 GHz），两段带外最坏 S11
  为 -0.343/-1.172 dB；中心、峰值幅度和带外匹配均失败。

### 5.6 Kaur 2021 X-band 陷波设计

- 只允许使用 `R1'=2.1`、`R2'=1.6`、`S1'=0.4 mm`。
- 论文门槛：7.16-7.71 GHz 全陷波区 VSWR >= 2；峰值中心约 7.4 GHz。
- 陷波区之外仍必须保持 UWB 匹配，不能只检查陷波峰值。
- 实际峰值落在 7.71 GHz（目标 7.4 GHz），两段带外最坏 S11 为
  -0.186/-1.037 dB；中心、峰值幅度和带外匹配均失败。

## 6. 论文未披露信息与处理方式

论文通常没有完整披露下列信息：

- 导体厚度、导电率和表面粗糙度；
- SMA、同轴或 CPW 端口的精确尺寸；
- 空气盒尺寸、开边界类型及距离；
- 网格、收敛和扫频采样细节；
- 可直接下载的数值 S11 原始数据。

系统将这些内容存入独立的 `engineering_assumptions` 或 `assumptions.json`，并遵守：

1. 不把工程假设描述成论文参数；
2. 论文门槛失败时，优先审查假设、端口、边界和跨求解器差异；
3. 不允许为了“调到正确曲线”而静默修改论文明确尺寸；
4. 严格候选 RMSE 必须使用同一 AEDT/PyAEDT 版本生成的本地参考曲线。

## 7. 软件与数据完整性验证

截至报告生成时：

- 完整测试集：**333/333 通过**；
- 定向覆盖：合同比较、多谐振、完整通带、完整陷波区、带边、单位换算、
  非有限数据、非递增频点、重复/错误 S 参数、已有文件拒绝覆盖和严格 AEDT attach；
- 官方合同烟雾检查：124/124 字段通过，但明确标记为 `validation_level=contract`，
  不声称电磁正确；
- Python 源码包和 wheel 构建成功；
- `campaign.json` 可解析，所有登记的仓库内工件路径存在；
- 源码与文档中未发现真实 API 密钥，只有占位符和测试值。

唯一测试警告来自第三方 `defusedxml.cElementTree` 的弃用提示，不影响当前结果。

## 8. 当前总体判定

| 判定对象 | 结论 |
| --- | --- |
| 正确性验证软件框架 | 通过离线软件验证 |
| 参数/材料/拓扑抽取合同 | 7 个参考设计均已冻结并通过静态检查 |
| HFSS 参考模型电磁正确性 | 7 个均已求解；1 个通过，6 个论文案例未通过 G3 |
| 图像+文本自动生成候选正确性 | 官方探针贴片 1 个完整 G0-G5 案例通过；其余 6 个因 G3 失败未进入候选 |
| 自动优化系统可否开始做性能优化 | 官方探针贴片已完成 12/12 收敛的真实优化回归；论文案例仍应先各自通过 G3-G5 |

所以当前最严谨的研究结论是：

> 系统已经具备可审计的证据抽取、独立代码生成、HFSS 构建求解和同版本数值验证能力，
> 并已在一个官方 HFSS 案例上完成 G0-G5 和收敛门禁自动优化。该单案例结果不能外推为所有论文结构都正确；
> 六个论文案例已经获得可审计的阴性参考证据，当前都不能作为候选基线。
> 只有 G3 通过的案例才进入独立候选与 G5；当前唯一完整通过案例仍是官方探针贴片。

## 9. 下一阶段执行顺序

2026-08-27 已实现通用版本化工程假设搜索内核，并完成 Wi-Fi 贴片首个 10 组搜索空间。
全部试验已有收敛证据与 S11 哈希，但没有通过论文完整频带门槛，因此不能把“最佳但未通过”
的结果提升为参考，更不能直接进入性能优化。

2026-08-28 又对论文正文、数据可用性声明及相关来源进行了逐页复核，没有发现可补入合同的
连接器尺寸、CST 边界/网格设置、数值 S11 或工程文件。因此已规划 V2：保持 13 个论文参数
哈希不变，只组合 V1 中改善完整频带指标的四个未披露假设，共 11 组二阶至四阶交互试验。
11 组现已全部求解并收敛，但 0 组通过完整频带门槛。最佳组合把最差带内点由 V1 的
-8.0203 dB 改善至 -8.4920 dB，仍比 -10 dB 门槛差 1.5080 dB。因此结果属于更强的阴性
跨求解器证据，不能提升为参考或性能优化基线。

1. 冻结官方探针贴片的参考/候选 S11 哈希、124/124 合同报告、收敛证据和 G5 报告；
   后续回归不得静默改写这些基线。
2. 把本次只读 S11 导出和 `antenna-workflow validate` 接入统一案例运行器，继续保持
   “不自动保存、不自动覆盖、不把未收敛结果标为通过”。
3. 冻结两个 Yeo 案例各四条本地曲线的 SHA-256 和阴性报告；在获得更强 CST/端口/边界
   证据之前，不生成这两个案例的独立候选，也不调整论文明确尺寸。
4. 冻结 Wi-Fi V2 的 11 组阴性结果及曲线哈希；如果继续该案例，应先获得新的来源证据或
   明确定义新的工程假设版本。不得调整论文明确尺寸，只有通过原论文门槛的新版本才能提升
   为候选参考；随后再把同一机制推广到其他失败案例。
5. 每个参考模型必须先通过论文门槛，随后冻结 `.aedt`、S11 CSV、报告和 SHA-256；只有
   合同和 S11 对比都通过的候选才进入自动优化阶段。

## 10. 可复核工件

- [机器可读活动清单](campaign.json)
- [活动状态说明](CAMPAIGN.md)
- [7 个独立案例 Python 入口](cases/CASE_INDEX.md)
- [完整验证器说明](../../docs/VALIDATION.md)
- [官方探针馈电贴片基准](ansys_pyaedt_probe_patch/README.md)
- [官方探针贴片 12 组自动优化记录](ansys_pyaedt_probe_patch/reference_data/optimization_study_2026_08_28.json)
- 官方参考求解报告位于本地忽略目录
  `ansys_pyaedt_probe_patch/local_results/reference_execution_report.json`；其关键数值已冻结在
  [机器可读活动清单](campaign.json)中
- [Yeo 两案例说明](yeo_slot_loaded_patch/README.md)
- [Yeo 普通贴片受控假设研究摘要](yeo_slot_loaded_patch/reference_data/conventional_hfss_assumption_study_2026_08_26.json)
- [Yeo 槽加载贴片受控假设研究摘要](yeo_slot_loaded_patch/reference_data/scaled_hfss_assumption_study_2026_08_26.json)
- [Wi-Fi 贴片来源校正与本地结果摘要](wifi_patch_5250/reference_data/hfss_reference_outcome_2026_08_26.json)
- [Wi-Fi 贴片 10 组工程假设搜索摘要](wifi_patch_5250/reference_data/engineering_assumption_search_2026_08_27.json)
- [Wi-Fi 贴片来源缺口复核](wifi_patch_5250/reference_data/source_gap_audit_2026_08_28.json)
- [Wi-Fi 贴片 V2 交互假设空间](wifi_patch_5250/assumption_space_v2.json)
- [Wi-Fi 贴片 V2 交互假设结果](wifi_patch_5250/reference_data/engineering_assumption_interactions_2026_08_28.json)
- [Kaur 三案例本地结果摘要](kaur_split_ring_monopole/reference_data/hfss_reference_outcomes_2026_08_26.json)
- [Wi-Fi 案例说明](wifi_patch_5250/README.md)
- [Kaur 三案例说明](kaur_split_ring_monopole/README.md)
