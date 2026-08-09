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

输出目录会出现：

- `generated_model_v001.py`：不可变版本；
- `generated_model.py`：最新版本别名；
- `python_export_manifest_v001.json`：输入工件哈希和许可证边界。

导入该 Python 文件不会启动 AEDT。它只暴露 `build(hfss)`，真正修改模型必须显式传入 HFSS
对象。也可以使用 `examples/leam_paper_cases/*/run_in_aedt.py` 的 native adapter 方式，通过
AEDT 的 **Tools > Run Script** 构建几何。

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
antenna-workflow optimization-run <optimization-job-id>
```

优化器复制输入工程，不覆盖原文件；每次试验追加到 `trials.jsonl`，当前最优写入 `best.json`。
