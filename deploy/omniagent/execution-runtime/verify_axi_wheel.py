#!/usr/bin/env python3
"""Verify the exact reviewed Axi wheel before an execution image can use it."""

from __future__ import annotations

import argparse
import base64
import configparser
import csv
import hashlib
import io
import json
import stat
import sys
import zipfile
from email.parser import Parser
from pathlib import Path, PurePosixPath
from typing import NoReturn


EXPECTED_FILENAME = "axi_cli-0.0.11-py3-none-any.whl"
EXPECTED_SHA256 = "ab9473092cac37e4f00347cd92d9e629424bcba99731c8d082b8031a29d6fbdf"
EXPECTED_SOURCE_URL = (
    "https://files.pythonhosted.org/packages/2f/5d/"
    "4c1081f2c29c0aa6333239cea43ba555c520cad5f07afc26c24fa1784820/"
    "axi_cli-0.0.11-py3-none-any.whl"
)
EXPECTED_UPSTREAM_COMMIT = "290b20e9d584d5d61cdf7bae47a83e142db569da"
EXPECTED_UPSTREAM_TREE = "5ddd9f7b9c7e2b3db6e43fb1f463bb231827bff3"
DIST_INFO = "axi_cli-0.0.11.dist-info"
MAX_MEMBERS = 128
MAX_WHEEL_BYTES = 8 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 16 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
ALLOWED_COMPRESSION = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}


class WheelVerificationError(RuntimeError):
    pass


def _fail(message: str) -> NoReturn:
    raise WheelVerificationError(message)


def _record_digest(data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
    return f"sha256={digest.decode('ascii')}"


def _safe_member_name(name: str) -> None:
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or name.startswith("/")
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or ":" in path.parts[0]
    ):
        _fail(f"wheel contains unsafe path: {name!r}")
    if not (name.startswith("axi/") or name.startswith(f"{DIST_INFO}/")):
        _fail(f"wheel contains unexpected top-level path: {name!r}")


def _metadata_fields(raw: bytes, member: str):
    try:
        return Parser().parsestr(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise WheelVerificationError(f"{member} is not valid UTF-8 metadata") from exc


def verify_wheel(
    wheel: Path,
    *,
    expected_sha256: str = EXPECTED_SHA256,
    require_expected_filename: bool = True,
) -> dict[str, object]:
    if not wheel.is_file():
        _fail(f"wheel does not exist: {wheel}")
    if require_expected_filename and wheel.name != EXPECTED_FILENAME:
        _fail(f"wheel filename must be {EXPECTED_FILENAME}")
    if wheel.stat().st_size > MAX_WHEEL_BYTES:
        _fail("wheel compressed size exceeds the review limit")

    body = wheel.read_bytes()
    actual_sha256 = hashlib.sha256(body).hexdigest()
    if actual_sha256 != expected_sha256:
        _fail(
            "wheel sha256 does not match the reviewed PyPI artifact: "
            f"expected {expected_sha256}, got {actual_sha256}"
        )

    try:
        archive = zipfile.ZipFile(io.BytesIO(body))
    except zipfile.BadZipFile as exc:
        raise WheelVerificationError("wheel is not a valid ZIP archive") from exc

    with archive:
        infos = archive.infolist()
        if not infos or len(infos) > MAX_MEMBERS:
            _fail(f"wheel member count must be between 1 and {MAX_MEMBERS}")
        names = [item.filename for item in infos]
        if len(names) != len(set(names)):
            _fail("wheel contains duplicate archive members")
        if any(item.is_dir() for item in infos):
            _fail("wheel must not contain explicit directory members")
        if sum(item.file_size for item in infos) > MAX_UNCOMPRESSED_BYTES:
            _fail("wheel uncompressed size exceeds the review limit")
        for item in infos:
            _safe_member_name(item.filename)
            if item.compress_type not in ALLOWED_COMPRESSION:
                _fail(f"wheel member uses an unsupported compression method: {item.filename!r}")
            if item.file_size > max(1, item.compress_size) * MAX_COMPRESSION_RATIO:
                _fail(f"wheel member exceeds the compression ratio limit: {item.filename!r}")
            mode = (item.external_attr >> 16) & 0xFFFF
            if mode and stat.S_ISLNK(mode):
                _fail(f"wheel contains a symbolic link: {item.filename!r}")
            if item.flag_bits & 0x1:
                _fail(f"wheel contains an encrypted member: {item.filename!r}")

        try:
            contents = {name: archive.read(name) for name in names}
        except (KeyError, RuntimeError, zipfile.BadZipFile) as exc:
            raise WheelVerificationError("wheel member integrity check failed") from exc

    metadata_name = f"{DIST_INFO}/METADATA"
    wheel_name = f"{DIST_INFO}/WHEEL"
    entry_points_name = f"{DIST_INFO}/entry_points.txt"
    record_name = f"{DIST_INFO}/RECORD"
    required = {metadata_name, wheel_name, entry_points_name, record_name, "axi/cli.py"}
    missing_required = sorted(required - contents.keys())
    if missing_required:
        _fail(f"wheel is missing required members: {', '.join(missing_required)}")

    metadata = _metadata_fields(contents[metadata_name], metadata_name)
    if metadata.get("Name") != "axi-cli" or metadata.get("Version") != "0.0.11":
        _fail("METADATA must declare axi-cli version 0.0.11")
    if metadata.get("Requires-Python") != ">=3.12":
        _fail("METADATA must require Python >=3.12")

    wheel_metadata = _metadata_fields(contents[wheel_name], wheel_name)
    if wheel_metadata.get("Root-Is-Purelib") != "true":
        _fail("WHEEL must declare Root-Is-Purelib: true")
    if wheel_metadata.get_all("Tag", []) != ["py3-none-any"]:
        _fail("WHEEL must contain only the py3-none-any tag")

    entry_points = configparser.ConfigParser(interpolation=None)
    try:
        entry_points.read_string(contents[entry_points_name].decode("utf-8"))
    except (UnicodeDecodeError, configparser.Error) as exc:
        raise WheelVerificationError("entry_points.txt is invalid") from exc
    if dict(entry_points.items("console_scripts")) != {"axi": "axi.cli:app"}:
        _fail("wheel console entry point must be exactly axi = axi.cli:app")

    try:
        rows = list(csv.reader(io.StringIO(contents[record_name].decode("utf-8"))))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise WheelVerificationError("RECORD is invalid") from exc
    records: dict[str, tuple[str, str]] = {}
    for row in rows:
        if len(row) != 3 or not row[0] or row[0] in records:
            _fail("RECORD contains an invalid or duplicate row")
        _safe_member_name(row[0])
        records[row[0]] = (row[1], row[2])
    if set(records) != set(contents):
        _fail("RECORD paths do not exactly match wheel members")

    for name, data in contents.items():
        digest, size = records[name]
        if name == record_name:
            if digest or size:
                _fail("RECORD must leave its own digest and size empty")
            continue
        if digest != _record_digest(data):
            _fail(f"RECORD sha256 mismatch for {name}")
        if size != str(len(data)):
            _fail(f"RECORD size mismatch for {name}")

    return {
        "name": "axi-cli",
        "version": "0.0.11",
        "filename": wheel.name,
        "sha256": actual_sha256,
        "source_url": EXPECTED_SOURCE_URL,
        "upstream_commit": EXPECTED_UPSTREAM_COMMIT,
        "upstream_tree": EXPECTED_UPSTREAM_TREE,
        "members": len(contents),
        "license_metadata_declared": bool(metadata.get("License")),
        "license_review_required": True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        result = verify_wheel(args.wheel)
    except (OSError, WheelVerificationError) as exc:
        print(f"AXI_WHEEL_VERIFICATION_FAILED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
