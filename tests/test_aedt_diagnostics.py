from antenna_mcp.aedt_runtime import aedt_failure_diagnostic


def test_flexnet_failure_is_extracted_from_batch_log(tmp_path):
    (tmp_path / "batch.log").write_text(
        """[error] The desired vendor daemon is down.
Feature
hfss_gui
License path
1055@localhost;
FlexNet Licensing error
-97,121
""",
        encoding="utf-8",
    )

    message = aedt_failure_diagnostic([tmp_path])

    assert "vendor daemon is not running" in message
    assert "feature=hfss_gui" in message
    assert "license_server=1055@localhost;" in message
    assert "FlexNet=-97,121" in message


def test_latest_non_license_error_is_reported(tmp_path):
    (tmp_path / "batch.log").write_text(
        """Ansys Electronics Desktop Version 2025.1.0
[error] FlexNet Licensing error
-97,121
Ansys Electronics Desktop Version 2025.1.0
[error] Unable to detect installed products.
""",
        encoding="utf-8",
    )

    assert "Unable to detect installed products" in aedt_failure_diagnostic([tmp_path])
