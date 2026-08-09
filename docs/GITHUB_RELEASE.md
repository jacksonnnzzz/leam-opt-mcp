# GitHub 发布清单

## 发布前

```powershell
antenna-doctor
pytest
python -m build
git status --short
```

确认以下内容没有进入提交：

- `.env` 和任何 API Key；
- 论文 PDF 或没有再分发许可的附件；
- `.antenna-mcp/`、`tmp/`、`outputs/`；
- `.aedt`、`.aedtresults/`、许可证文件和 AEDT 日志；
- 用户名、电脑专用绝对路径和本地第三方仓库路径。

仓库中的 `test_public_repository.py` 会自动检查常见个人 Windows profile 路径和 API Key
字面量。它是最后一道检查，不替代 GitHub secret scanning。

## 第一次上传

当前目录已经是 Git 仓库。创建一个空的 GitHub 仓库后，在本地执行：

```powershell
git add .
git commit -m "Initial public alpha"
git branch -M main
git remote add origin https://github.com/jacksonnnzzz/leam-opt-mcp.git
git push -u origin main
```

不要在 GitHub 网页创建 README 或 LICENSE，以免首次 push 前产生无关合并。如果远端已经有
提交，应先检查差异并正常合并，不要强制覆盖。

## 推荐仓库设置

- Description: `Reviewable multimodal antenna reconstruction and HFSS optimization MCP server`
- Topics: `mcp`, `hfss`, `pyaedt`, `antenna`, `multimodal`, `optimization`
- 启用 Actions、secret scanning、Dependabot alerts 和 private vulnerability reporting；
- 保护 `main`，要求 CI 通过后才能合并；
- 第一个标签使用 `v0.1.0-alpha.1`，避免把研究原型误标为稳定版本。

## 发布内容边界

GitHub Release 可以上传 Python wheel/sdist，但不要上传 Ansys 安装程序、许可证、论文 PDF 或
本机 `.aedt` 工作目录。论文案例只发布独立重建代码以及明确区分 `paper`、
`visual_interpretation`、`assumption` 的证据文件。
