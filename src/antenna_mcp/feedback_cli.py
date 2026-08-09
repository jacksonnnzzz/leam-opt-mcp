from __future__ import annotations

import argparse
import json

from .feedback import ModelFeedbackService
from .workspace import WorkspaceStore


def submit_main() -> None:
    parser = argparse.ArgumentParser(
        description="Record a user's HFSS comparison feedback without executing AEDT."
    )
    parser.add_argument("job_id")
    parser.add_argument("feedback")
    parser.add_argument("--comparison-image", action="append", default=[])
    args = parser.parse_args()
    result = ModelFeedbackService(WorkspaceStore()).submit(
        args.job_id,
        args.feedback,
        args.comparison_image,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def regenerate_main() -> None:
    parser = argparse.ArgumentParser(
        description="Regenerate a versioned offline Python model from recorded user feedback."
    )
    parser.add_argument("job_id")
    args = parser.parse_args()
    result = ModelFeedbackService(WorkspaceStore()).regenerate(args.job_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
