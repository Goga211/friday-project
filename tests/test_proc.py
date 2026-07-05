"""Тесты friday.shared.proc — сабпроцессы в пуле потоков.

Работают на любом event loop (в том числе на виндовом SelectorEventLoop,
где asyncio-сабпроцессы не реализованы — ради этого модуль и существует).
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

from friday.shared import proc

_SLEEP_FOREVER = "import time; time.sleep(30)"
# Родитель порождает внука и ждёт его — проверка, что kill_tree валит всё дерево
_SPAWN_CHILD_AND_WAIT = (
    "import subprocess, sys; "
    "subprocess.run([sys.executable, '-c', 'import time; time.sleep(30)'])"
)


async def test_run_returns_code_stdout_stderr() -> None:
    code, stdout, stderr = await proc.run(
        sys.executable,
        "-c",
        "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)",
    )
    assert code == 3
    assert stdout.strip() == b"out"
    assert stderr.strip() == b"err"


async def test_run_pipes_stdin_data() -> None:
    code, stdout, _ = await proc.run(
        sys.executable,
        "-c",
        "import sys; sys.stdout.write(sys.stdin.read())",
        stdin_data=b"ping",
    )
    assert code == 0
    assert stdout == b"ping"


async def test_run_timeout_kills_process() -> None:
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        await proc.run(sys.executable, "-c", _SLEEP_FOREVER, timeout=0.3)
    assert time.monotonic() - started < 10


async def test_run_timeout_kills_process_tree() -> None:
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        await proc.run(sys.executable, "-c", _SPAWN_CHILD_AND_WAIT, timeout=0.5, kill_tree=True)
    # если внук выжил и держит pipe'ы — communicate() после kill зависнет и тест не уложится
    assert time.monotonic() - started < 15


async def test_run_respects_cwd(tmp_path: Path) -> None:
    _, stdout, _ = await proc.run(
        sys.executable, "-c", "import os; print(os.getcwd())", cwd=str(tmp_path)
    )
    assert Path(stdout.decode(errors="replace").strip()) == tmp_path


async def test_spawn_detached_runs_program(tmp_path: Path) -> None:
    marker = tmp_path / "marker.txt"
    await proc.spawn_detached(sys.executable, "-c", f"open({str(marker)!r}, 'w').close()")
    for _ in range(100):
        if marker.exists():
            break
        await asyncio.sleep(0.1)
    assert marker.exists()
