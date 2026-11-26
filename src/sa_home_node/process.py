from __future__ import annotations

import asyncio
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import IO

from sa_home_node import host
from sa_home_node.proto.wire import ERR_INTERNAL, ERR_NEEDS_PRIVILEGE, ProtoError
from sa_home_node.requirements import looks_like_sudo_denied

Stream = int | IO[bytes] | None


@dataclass(frozen=True)
class ProcResult:
    returncode: int
    stdout: bytes
    stderr: bytes

    @property
    def stdout_text(self) -> str:
        return self.stdout.decode(errors="replace")

    @property
    def stderr_text(self) -> str:
        return self.stderr.decode(errors="replace").strip()


async def run(
    argv: Sequence[str],
    *,
    timeout: float | None = None,
    input: bytes | None = None,
    stdout: Stream = asyncio.subprocess.PIPE,
    stderr: Stream = asyncio.subprocess.PIPE,
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
) -> ProcResult:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE if input is not None else None,
        stdout=stdout,
        stderr=stderr,
        env=env,
        cwd=cwd,
    )
    exchange = proc.communicate(input)
    if timeout is not None:
        exchange = asyncio.wait_for(exchange, timeout=timeout)
    try:
        out, err = await exchange
    except BaseException:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        raise
    return ProcResult(proc.returncode or 0, out or b"", err or b"")


async def run_privileged(
    argv: Sequence[str], *, needs_privilege_text: str, timeout: float | None = None
) -> str:
    program, *args = argv
    elevated = host.current().elevate_noninteractive_argv([shutil.which(program) or program, *args])
    if elevated is None:
        raise ProtoError(ERR_NEEDS_PRIVILEGE, needs_privilege_text)
    result = await run(elevated, timeout=timeout)
    if result.returncode != 0:
        if looks_like_sudo_denied(result.stderr_text):
            raise ProtoError(ERR_NEEDS_PRIVILEGE, needs_privilege_text)
        raise ProtoError(ERR_INTERNAL, f"{program} {' '.join(args)} завершился ошибкой: {result.stderr_text}")
    return result.stdout.decode()
