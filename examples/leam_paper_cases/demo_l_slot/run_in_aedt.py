"""Select this file in AEDT: Tools > Run Script."""

import os
import runpy

CASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.abspath(os.path.join(CASE_DIR, "..", "..", "..", "tools", "run_generated_model_in_aedt.py"))
MODEL = os.path.join(CASE_DIR, "generated_model_v001.py")
runpy.run_path(RUNNER)["run_model"](MODEL, create_new_design=True)
