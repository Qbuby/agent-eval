from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_eval.config import settings
from agent_eval.omniagent_runtime.artifacts import (
    ArtifactError,
    ArtifactTooLarge,
    FilesystemArtifactStore,
    sanitize_filename,
    validate_file_type,
    ClamAVScanner,
    FailClosedScanner,
)
from agent_eval.omniagent_runtime.runner import (
    LocalDevelopmentPythonRunner,
    RunnerDisabled,
    RunnerLogLimit,
    RunnerOutputViolation,
    RunnerTimeout,
    collect_output_files,
)
from agent_eval.omniagent_runtime.schedules import (
    ScheduleError,
    compute_next_run,
    validate_schedule,
)


class BytesReader:
    def __init__(self, value: bytes) -> None:
        self.value = value
        self.offset = 0

    async def read(self, size: int = -1) -> bytes:
        if self.offset >= len(self.value):
            return b""
        end = len(self.value) if size < 0 else self.offset + size
        chunk = self.value[self.offset:end]
        self.offset = end
        return chunk


@pytest.mark.parametrize("name", ["../x.csv", "a/b.csv", "a\\b.csv", "C:x.csv", "x.exe"])
def test_filename_rejects_traversal_and_unsupported_types(name: str) -> None:
    with pytest.raises(ArtifactError):
        sanitize_filename(name)


@pytest.mark.asyncio
async def test_filesystem_store_hashes_and_limits_streams(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "objects")
    stored = await store.write_stream("tenant/user/id/content.csv", BytesReader(b"a,b\n1,2\n"))

    assert stored.size_bytes == 8
    assert store.path_for_read(stored.object_key).read_bytes() == b"a,b\n1,2\n"
    assert validate_file_type(stored.path, ".csv", "text/csv") == "text/csv"

    with pytest.raises(ArtifactTooLarge):
        await store.write_stream("tenant/user/id2/content.txt", BytesReader(b"12345"), max_bytes=4)


def test_file_type_rejects_mime_spoofing(tmp_path: Path) -> None:
    fake = tmp_path / "fake.pdf"
    fake.write_bytes(b"not a pdf")
    with pytest.raises(ArtifactError, match="magic"):
        validate_file_type(fake, ".pdf", "application/pdf")


@pytest.mark.asyncio
async def test_production_scanners_fail_closed_when_unavailable(tmp_path: Path, monkeypatch) -> None:
    sample = tmp_path / "sample.txt"
    sample.write_text("safe", encoding="utf-8")
    assert not (await FailClosedScanner("disabled").scan(sample)).clean
    monkeypatch.setattr(settings.omniagent, "clamav_command", "definitely-not-installed")
    result = await ClamAVScanner().scan(sample)
    assert result.clean is False
    assert result.engine == "clamav"


def test_store_rejects_symlink_output(tmp_path: Path) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks unavailable")
    store = FilesystemArtifactStore(tmp_path / "objects")
    source = tmp_path / "source.txt"
    source.write_text("ok", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(source)
    except OSError:
        pytest.skip("symlink creation is not permitted")
    with pytest.raises(ArtifactError, match="symbolic"):
        store.copy_from(link, "tenant/user/id/content.txt")


def test_collect_outputs_rejects_total_limit(tmp_path: Path) -> None:
    output = tmp_path / "outputs"
    output.mkdir()
    (output / "a.txt").write_bytes(b"123")
    (output / "b.txt").write_bytes(b"456")
    with pytest.raises(RunnerOutputViolation, match="byte limit"):
        collect_output_files(output, max_bytes=5)


@pytest.mark.asyncio
async def test_local_runner_is_fail_closed_by_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings.omniagent, "runner", "disabled")
    with pytest.raises(RunnerDisabled):
        await LocalDevelopmentPythonRunner().run(code="print('x')", workspace=tmp_path / "job")


@pytest.mark.asyncio
async def test_local_runner_collects_output_and_blocks_network(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings.omniagent, "runner", "local_dev")
    runner = LocalDevelopmentPythonRunner()
    result = await runner.run(
        code=(
            "import os\n"
            "from pathlib import Path\n"
            "Path(os.environ['OMNI_OUTPUT_DIR'], 'result.txt').write_text('done')\n"
            "print('complete')\n"
        ),
        workspace=tmp_path / "success",
        timeout_seconds=20,
    )
    assert result.exit_code == 0
    assert "complete" in result.logs
    assert [path.name for path in result.output_files] == ["result.txt"]

    blocked = await runner.run(
        code="import socket\nsocket.socket()\n",
        workspace=tmp_path / "blocked",
        timeout_seconds=20,
    )
    assert blocked.exit_code != 0
    assert "network access is disabled" in blocked.logs


@pytest.mark.asyncio
async def test_local_runner_enforces_timeout_and_log_limit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings.omniagent, "runner", "local_dev")
    runner = LocalDevelopmentPythonRunner()
    with pytest.raises(RunnerTimeout):
        await runner.run(
            code="while True: pass\n",
            workspace=tmp_path / "timeout",
            timeout_seconds=1,
        )
    with pytest.raises(RunnerLogLimit):
        await runner.run(
            code="print('x' * 4096)\n",
            workspace=tmp_path / "logs",
            timeout_seconds=20,
            log_bytes=1024,
        )


def test_schedule_validation_and_daily_timezone() -> None:
    with pytest.raises(ScheduleError, match="at least 15"):
        validate_schedule({"kind": "interval", "minutes": 14}, "Asia/Shanghai")
    schedule = validate_schedule({"kind": "daily", "at": "09:30"}, "Asia/Shanghai")
    now = __import__("datetime").datetime(2026, 1, 1, 0, 0, tzinfo=__import__("datetime").timezone.utc)
    assert compute_next_run(schedule, "Asia/Shanghai", now=now).isoformat() == (
        "2026-01-01T01:30:00+00:00"
    )
