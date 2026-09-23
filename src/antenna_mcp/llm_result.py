"""String-compatible responses and content-free request diagnostics."""
import json


class LlmText(str):
    def __new__(cls, text, metadata):
        value = super().__new__(cls, text)
        value.metadata = metadata
        return value


class LlmRequestError(RuntimeError):
    def __init__(self, message, metadata):
        super().__init__(message)
        self.metadata = metadata


class LlmOutputTruncatedError(LlmRequestError):
    """Token-limited output must not be accepted, even when it parses as JSON."""


def record_request_metadata(store, state, stage, result):
    metadata = getattr(result, "metadata", None)
    if not isinstance(metadata, dict):
        return
    version = 1
    while (store.job_dir(state.job_id) / f"llm_{stage}_v{version:03d}.json").exists():
        version += 1
    key = f"llm_{stage}_v{version:03d}"
    path = store.write_artifact(state.job_id, key + ".json", json.dumps(metadata, ensure_ascii=False, indent=2))
    state.artifacts[key] = str(path)
    store.save_state(state)
