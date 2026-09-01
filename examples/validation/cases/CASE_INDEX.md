# 独立案例目录

这里按“一案例一文件夹”提供 9 个独立入口。每个目录至少包含：

- `run_case.py`：外部 Python/PyAEDT 建模与可选求解入口；
- `case.json`：设计名、共享建模实现、benchmark、论文门槛和默认输出。

共享 `reference_model.py` 仍只保留一份，单案例入口固定 `--case`，因此既满足逐例运行，
也避免复制几何代码后产生参数漂移。

> **重要：不要在 AEDT 的 `Tools > Run Script` 中选择这些 `run_case.py`。**
> 它们是外部 CPython/PyAEDT 启动器，必须由仓库虚拟环境中的 Python 在 PowerShell
> 运行，再通过 gRPC 操作已打开的 AEDT。AEDT 内置宏解释器可能报
> `future feature is not defined: annotations`，这表示执行环境选错，并非模型语法错误。

> **这些入口构建的是冻结参考模型，不是项目自动生成的独立候选。** 它们用于得到
> reference S11 和论文门槛，不能单独证明 `antenna-workflow` 的论文识别与代码生成正确。
> 项目正确性必须另外生成 `generated_model_vNNN.py`，再与这里的参考合同和曲线比较。

| 案例目录 | HFSS 设计名 | 求解后的主要输出 |
| --- | --- | --- |
| `ansys_probe_patch` | `OfficialProbeFedPatch` | `reference_s11.csv` |
| `yeo_conventional_patch` | `YeoConventionalPatch` | S11 + `paper_target_report.json` |
| `yeo_scaled_slot_loaded_patch` | `YeoScaledSlotLoadedPatch` | S11 + `paper_target_report.json` |
| `wifi_patch_5250` | `ElGendySinglePatch5250_EdgeReferencedXp` | S11；终端 JSON 给出完整 Wi-Fi 频带门槛 |
| `ibrahim_38ghz_monopole` | `Ibrahim2023Antenna3_38GHz` | S11 + `paper_target_report.json`；已收敛但未通过论文门槛 |
| `khan_28_38ghz_monopole` | `Khan2024SingleElement28_38GHz_V2` | 双频 S11 + `paper_target_report.json`；V2 与 3 个受控变体均收敛但未通过论文门槛 |
| `kaur_baseline_uwb` | `Kaur2021BaselineUWB` | S11 + `paper_target_baseline.json` |
| `kaur_wlan_notch` | `Kaur2021WLANNotch` | S11 + `paper_target_wlan_notch.json` |
| `kaur_xband_notch` | `Kaur2021XBandNotch` | S11 + `paper_target_xband_notch.json` |

## 自行验证

先在 AEDT Message Manager 中读取 `gRPC server running on port: XXXXX`；如果要附加到
当前窗口，下面的 `50051` 必须替换成实际端口。以下命令粘贴到 **PowerShell**，不要粘贴
到 AEDT 的脚本窗口。

仅生成模型，省略 `--solve`：

```powershell
.\.venv\Scripts\python.exe `
  ".\examples\validation\cases\yeo_conventional_patch\run_case.py" `
  --version 2025.1
```

使用官方/学校许可证，在新会话中生成并求解：

```powershell
.\.venv\Scripts\python.exe `
  ".\examples\validation\cases\yeo_conventional_patch\run_case.py" `
  --version 2025.1 `
  --solve
```

严格附加到已打开的空项目（把端口和项目名换成 Message Manager 中的实际值）：

```powershell
.\.venv\Scripts\python.exe `
  ".\examples\validation\cases\yeo_conventional_patch\run_case.py" `
  --version 2025.1 `
  --grpc-port 50051 `
  --active-project Project7 `
  --solve
```

把路径中的案例目录替换成表格中的任意一项即可逐个运行。脚本会拒绝覆盖同名设计、
项目和结果文件；附加模式不会关闭已经打开的 AEDT。`local_results/` 被 Git 忽略。

成功执行 Python 并不自动等于论文正确。Yeo、Kaur、Ibrahim 和 Khan 会生成独立论文门槛报告，
Wi-Fi 会在终端结果中给出 `paper_band_target_passed`。只有报告通过后，S11 才能作为本地参考。
