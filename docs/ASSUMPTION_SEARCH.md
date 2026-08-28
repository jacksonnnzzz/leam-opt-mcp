# 版本化工程假设搜索

论文明确参数与论文未披露的 HFSS 实现细节必须分开。工程假设搜索只允许改变后者，
不能把“自动调参”变成对论文尺寸的静默修改。

## 工件

- `assumption_space.json`：冻结论文参数、基线假设、允许搜索的未披露字段、约束和门槛；
- `study_snapshot.json`：空间与论文参数的内容哈希；已有输出目录不能换用另一份空间；
- `trials/ast-*/build_receipt*.json`：设计名、假设哈希与 HFSS 结构签名；构建器修订使用新设计名和新凭据，不覆盖旧设计；
- `trials/ast-*/s11_vNNN.csv`：每次尝试独立保存的 S11；
- `trials/ast-*/result_vNNN.json`：不可覆盖的求解、收敛和论文门槛结果；
- `summary_vNNN.json`：版本化排名，不覆盖旧报告。

试验 ID 由完整工程假设的规范 JSON 哈希生成。同一组假设始终得到同一 ID；论文参数另有
独立哈希，并在每个试验中重复验证。搜索字段必须标记为
`source_status=unresolved_from_source`，与 `paper_parameters` 重名会立即失败。

笛卡尔空间还可以用 `minimum_changed_assumptions` 和
`maximum_changed_assumptions` 限制每个试验相对基线改变的字段数。例如设为 `2` 和 `4`
可以只研究二阶至四阶交互，跳过已经完成的基线和单因素试验。两者必须是非负整数，最小值
不能大于最大值，最大值也不能超过搜索字段数。

## 离线计划

计划不会启动 AEDT：

```powershell
antenna-workflow assumption-plan `
  --space ".\examples\validation\wifi_patch_5250\assumption_space.json" `
  --output-dir ".\examples\validation\wifi_patch_5250\local_results\assumption_search_v1" `
  --limit 10
```

## 在已打开的 AEDT 中运行

先从 AEDT Message Manager 读取实际 gRPC 端口，并确认目标项目已经打开。运行器严格
附加到该端口；连接失败时不会启动备用 AEDT，也不会切换项目。

```powershell
antenna-workflow assumption-run `
  --space ".\examples\validation\wifi_patch_5250\assumption_space.json" `
  --adapter ".\examples\validation\wifi_patch_5250\assumption_adapter.py" `
  --output-dir ".\examples\validation\wifi_patch_5250\local_results\assumption_search_v1" `
  --grpc-port 50051 `
  --active-project Project7 `
  --aedt-version 2025.1 `
  --limit 10 `
  --resume
```

`--resume` 跳过已有完整结果。失败试验不会被覆盖；需要重新尝试时显式增加
`--retry-failed`，系统写入下一个 `result_vNNN.json` 与 `s11_vNNN.csv`。

若 AEDT 已经显示 `SOLVED`，但客户端在证据导出前中断，可以增加
`--postprocess-existing`。该模式只接受与不可变构建凭据完全匹配的已有设计，不新建、
不重新求解、也不保存工程：

```powershell
antenna-workflow assumption-run ... --resume --retry-failed --postprocess-existing
```

运行器在切换活动设计前会等待共享 AEDT Desktop 完全空闲，防止前一个客户端中断后
遗留的求解被切换设计操作终止并污染后续试验。求解失败会读取本次 AEDT Message
Manager 错误并写入 `failure_kind`。许可证不可用、客户端/遗留求解中断、几何校验
失败、求解器失败和证据导出失败必须分开；基础设施故障不能参与物理排名。

## 收敛与排名

正常完成求解并不等于自适应收敛。运行器从 PyAEDT solver profile 读取最后一个
`Max Mag. Delta S`，同时要求 interpolating sweep 报告 converged。只有收敛证据和论文
电磁门槛同时通过，结果才标记 `paper_gate_passed=true`。

```powershell
antenna-workflow assumption-report `
  --space ".\examples\validation\wifi_patch_5250\assumption_space.json" `
  --output-dir ".\examples\validation\wifi_patch_5250\local_results\assumption_search_v1"
```

当前 Wi-Fi 试点采用一次只改变一个未披露假设的策略。找到通过 G3 的版本后，必须冻结
该版本，再进入独立候选 G4/G5；不能直接把搜索中最好的未通过结果送入性能优化。

Wi-Fi V2 使用 `assumption_space_v2.json`，只组合 V1 中改善完整频带指标的四个未披露
假设，并通过上述最小/最大改变数生成 11 组交互试验。它仍属于 G3 假设诊断；只有某组
同时通过收敛门槛和原论文完整频带门槛，才允许冻结为新的参考版本。

该 V2 已于 2026-08-28 完成：11/11 组求解且收敛，0 组通过论文门槛。最佳组合为
`probe_outer_radius_mm=1.0` 与 `radiation_padding_mm=10.0`，完整目标频带最差点为
`-8.4920 dB`，仍未达到 `-10 dB`。结果冻结在
`reference_data/engineering_assumption_interactions_2026_08_28.json`，不能作为性能优化基线。
