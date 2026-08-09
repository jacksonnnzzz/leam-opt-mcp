from antenna_mcp.discovery import discover_aedt_installations, preferred_aedt_version


def test_discovers_custom_aedt_root_from_environment(tmp_path):
    root = tmp_path / "custom" / "v251" / "AnsysEM"
    root.mkdir(parents=True)
    (root / "ansysedt.exe").write_text("", encoding="utf-8")
    (root / "syslib").mkdir()
    found = discover_aedt_installations({"ANSYSEM_ROOT251": str(root)})
    assert found[0]["version"] == "2025.1"
    assert found[0]["root"] == str(root.resolve())
    assert preferred_aedt_version({"ANSYSEM_ROOT251": str(root)}) == "2025.1"


def test_explicit_executable_has_priority(tmp_path):
    root = tmp_path / "v252" / "AnsysEM"
    root.mkdir(parents=True)
    executable = root / "ansysedt.exe"
    executable.write_text("", encoding="utf-8")
    (root / "syslib").mkdir()
    found = discover_aedt_installations({"ANTENNA_MCP_AEDT_EXECUTABLE": str(executable)})
    assert found[0]["version"] == "2025.2"
