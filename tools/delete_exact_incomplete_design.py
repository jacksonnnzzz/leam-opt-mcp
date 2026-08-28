"""Delete one explicitly verified incomplete design from an open AEDT project."""

from __future__ import annotations

import argparse
import json

from antenna_mcp.aedt_runtime import is_aedt_app_released
from export_active_s11 import _attach_existing_hfss


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Delete an exact incomplete HFSS design after fail-closed verification."
    )
    parser.add_argument("--grpc-port", type=int, required=True)
    parser.add_argument("--expect-project", required=True)
    parser.add_argument("--expect-design", required=True)
    parser.add_argument("--fallback-design", required=True)
    parser.add_argument(
        "--expect-objects",
        required=True,
        help="Comma-separated exact object-name set expected in the incomplete design.",
    )
    parser.add_argument(
        "--expect-boundaries",
        default="",
        help="Comma-separated exact boundary-name set expected in the incomplete design.",
    )
    args = parser.parse_args()

    expected_objects = {item.strip() for item in args.expect_objects.split(",") if item.strip()}
    expected_boundaries = {
        item.strip() for item in args.expect_boundaries.split(",") if item.strip()
    }
    if not expected_objects:
        parser.error("--expect-objects must contain at least one object")
    if args.expect_design.casefold() == args.fallback_design.casefold():
        parser.error("--fallback-design must differ from --expect-design")

    hfss = None
    try:
        hfss = _attach_existing_hfss(
            port=args.grpc_port,
            expected_project=args.expect_project,
            expected_design=args.expect_design,
        )
        actual_objects = set(hfss.modeler.object_names)
        boundaries = list(getattr(hfss, "boundaries", []))
        actual_boundaries = {str(boundary.name) for boundary in boundaries}
        setups = list(getattr(hfss, "setup_names", []))
        designs = list(hfss.design_list)
        if actual_objects != expected_objects:
            raise RuntimeError(
                f"refusing deletion: objects are {sorted(actual_objects)!r}, "
                f"expected exactly {sorted(expected_objects)!r}"
            )
        if actual_boundaries != expected_boundaries:
            raise RuntimeError(
                f"refusing deletion: boundaries are {sorted(actual_boundaries)!r}, "
                f"expected exactly {sorted(expected_boundaries)!r}"
            )
        if setups:
            raise RuntimeError(
                "refusing deletion: incomplete design unexpectedly contains "
                f"{len(setups)} setup(s)"
            )
        if args.fallback_design not in designs:
            raise RuntimeError(
                f"refusing deletion: fallback design {args.fallback_design!r} is not present"
            )
        if not hfss.delete_design(args.expect_design, fallback_design=args.fallback_design):
            raise RuntimeError(f"AEDT failed to delete {args.expect_design!r}")
        if args.expect_design in hfss.design_list:
            raise RuntimeError(f"AEDT still lists deleted design {args.expect_design!r}")
        if not hfss.save_project():
            raise RuntimeError("AEDT deleted the design but failed to save the project")
        print(
            json.dumps(
                {
                    "status": "deleted_verified_incomplete_design",
                    "project": args.expect_project,
                    "deleted_design": args.expect_design,
                    "fallback_design": args.fallback_design,
                    "deleted_objects": sorted(actual_objects),
                    "deleted_boundaries": sorted(actual_boundaries),
                    "recoverable": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        if hfss is not None and not is_aedt_app_released(hfss):
            try:
                hfss.release_desktop(close_projects=False, close_desktop=False)
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
