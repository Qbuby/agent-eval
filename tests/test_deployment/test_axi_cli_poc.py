from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
POC = ROOT / "scripts" / "axi-poc" / "run_smoke.py"


def _load():
    spec = importlib.util.spec_from_file_location("axi_cli_poc", POC)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_axi_cli_poc_is_loopback_only_and_has_no_embedded_secret() -> None:
    source = POC.read_text(encoding="utf-8")
    assert '("127.0.0.1", 0)' in source
    assert "files.pythonhosted.org" not in source
    assert "axi-license-reviewed" not in source
    assert "CANARY_TOKEN" in source


def test_axi_cli_poc_rejects_token_in_cli_output(tmp_path: Path, monkeypatch) -> None:
    module = _load()
    executable = tmp_path / "fake-axi"
    executable.write_text("placeholder", encoding="utf-8")

    class Completed:
        returncode = 0
        stdout = json.dumps({"token": module.CANARY_TOKEN})
        stderr = ""

    monkeypatch.setattr(module.subprocess, "run", lambda *_args, **_kwargs: Completed())
    try:
        module._command(executable, ["search", "data"], {})
    except AssertionError as exc:
        assert "leaked" in str(exc)
    else:
        raise AssertionError("token leak must fail the PoC")
