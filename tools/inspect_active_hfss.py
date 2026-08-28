"""Read-only structural and message inspection for one open HFSS design."""

from __future__ import annotations

import argparse
import json

from antenna_mcp.aedt_runtime import is_aedt_app_released
from export_active_s11 import _attach_existing_hfss


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect an explicitly selected open HFSS design.")
    parser.add_argument("--grpc-port", type=int, required=True)
    parser.add_argument("--expect-project", required=True)
    parser.add_argument("--expect-design", required=True)
    args = parser.parse_args()

    hfss = None
    try:
        hfss = _attach_existing_hfss(
            port=args.grpc_port,
            expected_project=args.expect_project,
            expected_design=args.expect_design,
        )
        boundaries = [
            {
                "name": str(getattr(boundary, "name", "")),
                "type": str(getattr(boundary, "type", getattr(boundary, "boundary_type", ""))),
            }
            for boundary in getattr(hfss, "boundaries", [])
        ]
        messages = list(
            hfss.desktop_class.odesktop.GetMessages(
                str(hfss.project_name), str(hfss.design_name), 0
            )
        )
        geometry = []
        for name in hfss.modeler.object_names:
            obj = hfss.modeler[name]
            geometry.append(
                {
                    "name": name,
                    "bounding_box": list(getattr(obj, "bounding_box", []) or []),
                    "vertices": [
                        list(getattr(vertex, "position", []) or [])
                        for vertex in getattr(obj, "vertices", [])
                    ],
                }
            )
        result = {
            "status": "inspected_read_only",
            "project": str(hfss.project_name),
            "design": str(hfss.design_name),
            "objects": list(hfss.modeler.object_names),
            "geometry": geometry,
            "boundaries": boundaries,
            "setups": list(getattr(hfss, "setup_names", [])),
            "messages": messages,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    finally:
        if hfss is not None and not is_aedt_app_released(hfss):
            try:
                hfss.release_desktop(close_projects=False, close_desktop=False)
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
