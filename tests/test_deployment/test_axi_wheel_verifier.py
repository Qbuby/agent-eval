from __future__ import annotations

import base64
import csv
import hashlib
import importlib.util
import io
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
VERIFIER = (
    ROOT / "deploy" / "omniagent" / "execution-runtime" / "verify_axi_wheel.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("axi_wheel_verifier", VERIFIER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _digest(data: bytes) -> str:
    value = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
    return f"sha256={value.decode('ascii')}"


def _wheel(tmp_path: Path, *, metadata_version: str = "0.0.11", extra=None) -> Path:
    dist = "axi_cli-0.0.11.dist-info"
    files = {
        "axi/__init__.py": b"",
        "axi/cli.py": b"app = object()\n",
        f"{dist}/METADATA": (
            "Metadata-Version: 2.4\n"
            "Name: axi-cli\n"
            f"Version: {metadata_version}\n"
            "Requires-Python: >=3.12\n\n"
        ).encode(),
        f"{dist}/WHEEL": (
            b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ),
        f"{dist}/entry_points.txt": b"[console_scripts]\naxi = axi.cli:app\n",
    }
    if extra:
        files.update(extra)
    record = io.StringIO()
    writer = csv.writer(record, lineterminator="\n")
    for name, data in files.items():
        writer.writerow((name, _digest(data), len(data)))
    writer.writerow((f"{dist}/RECORD", "", ""))
    files[f"{dist}/RECORD"] = record.getvalue().encode()

    wheel = tmp_path / "axi_cli-0.0.11-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return wheel


def test_axi_wheel_verifier_accepts_complete_matching_wheel(tmp_path: Path) -> None:
    module = _load()
    wheel = _wheel(tmp_path)
    expected = hashlib.sha256(wheel.read_bytes()).hexdigest()

    result = module.verify_wheel(wheel, expected_sha256=expected)

    assert result["name"] == "axi-cli"
    assert result["version"] == "0.0.11"
    assert result["license_metadata_declared"] is False
    assert result["license_review_required"] is True


def test_axi_wheel_verifier_pins_pypi_artifact_identity() -> None:
    module = _load()

    assert module.EXPECTED_FILENAME == "axi_cli-0.0.11-py3-none-any.whl"
    assert module.EXPECTED_SHA256 == (
        "ab9473092cac37e4f00347cd92d9e629424bcba99731c8d082b8031a29d6fbdf"
    )
    assert module.EXPECTED_SOURCE_URL == (
        "https://files.pythonhosted.org/packages/2f/5d/"
        "4c1081f2c29c0aa6333239cea43ba555c520cad5f07afc26c24fa1784820/"
        "axi_cli-0.0.11-py3-none-any.whl"
    )
    assert module.EXPECTED_UPSTREAM_COMMIT == (
        "290b20e9d584d5d61cdf7bae47a83e142db569da"
    )
    assert module.EXPECTED_UPSTREAM_TREE == (
        "5ddd9f7b9c7e2b3db6e43fb1f463bb231827bff3"
    )


def test_agent_eval_axi_package_uses_reviewed_native_entry_point() -> None:
    package = tomllib.loads(
        (ROOT / "packages" / "agent-eval-axi-tools" / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )

    assert package["project"]["requires-python"] == ">=3.12,<3.13"
    assert package["project"]["entry-points"]["axi.native_tools"] == {
        "data": "agent_eval_axi_tools.data"
    }


def test_axi_wheel_verifier_rejects_outer_hash_mismatch(tmp_path: Path) -> None:
    module = _load()
    wheel = _wheel(tmp_path)

    with pytest.raises(module.WheelVerificationError, match="sha256"):
        module.verify_wheel(wheel, expected_sha256="0" * 64)


def test_axi_wheel_verifier_rejects_oversize_before_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load()
    wheel = _wheel(tmp_path)
    monkeypatch.setattr(module, "MAX_WHEEL_BYTES", wheel.stat().st_size - 1)

    with pytest.raises(module.WheelVerificationError, match="compressed size"):
        module.verify_wheel(wheel, expected_sha256="0" * 64)


def test_axi_wheel_verifier_rejects_wrong_metadata_version(tmp_path: Path) -> None:
    module = _load()
    wheel = _wheel(tmp_path, metadata_version="0.0.10")
    expected = hashlib.sha256(wheel.read_bytes()).hexdigest()

    with pytest.raises(module.WheelVerificationError, match="version 0.0.11"):
        module.verify_wheel(wheel, expected_sha256=expected)


def test_axi_wheel_verifier_rejects_unsafe_member_path(tmp_path: Path) -> None:
    module = _load()
    wheel = _wheel(tmp_path, extra={"../escape.py": b"bad"})
    expected = hashlib.sha256(wheel.read_bytes()).hexdigest()

    with pytest.raises(module.WheelVerificationError, match="unsafe path"):
        module.verify_wheel(wheel, expected_sha256=expected)


def test_axi_wheel_verifier_rejects_record_tampering(tmp_path: Path) -> None:
    module = _load()
    wheel = _wheel(tmp_path)
    with zipfile.ZipFile(wheel) as archive:
        contents = {
            item.filename: archive.read(item.filename) for item in archive.infolist()
        }
    contents["axi/cli.py"] = b"tampered\n"
    with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
    expected = hashlib.sha256(wheel.read_bytes()).hexdigest()

    with pytest.raises(module.WheelVerificationError, match="RECORD sha256 mismatch"):
        module.verify_wheel(wheel, expected_sha256=expected)


def test_axi_wheel_verifier_rejects_unreviewed_compression(tmp_path: Path) -> None:
    module = _load()
    wheel = _wheel(tmp_path)
    with zipfile.ZipFile(wheel) as archive:
        contents = {
            item.filename: archive.read(item.filename) for item in archive.infolist()
        }
    with zipfile.ZipFile(wheel, "w", zipfile.ZIP_BZIP2) as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
    expected = hashlib.sha256(wheel.read_bytes()).hexdigest()

    with pytest.raises(module.WheelVerificationError, match="compression method"):
        module.verify_wheel(wheel, expected_sha256=expected)
