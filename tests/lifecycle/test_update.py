from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from support import Collector, wait_for

from sa_home_node import host
from sa_home_node.lifecycle import update
from sa_home_node.proto.wire import ProtoError
from sa_home_node.swarm.router import NodeRouter
from sa_home_node.swarm.service import NodeService

FIXTURES = Path(__file__).parent.parent / "fixtures" / "supervisor"
REPLIES = {item["case"]: item for item in json.loads((FIXTURES / "replies.json").read_text(encoding="utf-8"))["update"]}
EVENTS = [
    line["payload"]["data"]
    for line in map(json.loads, (FIXTURES / "events.ndjson").read_text(encoding="utf-8").splitlines())
    if line["payload"]["event"] == "update_finished"
]
REPO = REPLIES["check_update"]["reply"]["repo"]
TAGS = "a\trefs/tags/v1.0.0\nb\trefs/tags/v1.2.0\nc\trefs/tags/v1.10.0-rc\nd\trefs/tags/v1.1.9\ne\trefs/heads/main\n"
SPEC = "sa-home-node[tts]@ git+https://h/r.git@v1.0.0"


class FakeHost:
    def __init__(self, via_task: bool) -> None:
        self.update_via_task = via_task
        self.task_result: tuple[bool, str] = (True, "")
        self.triggered = 0

    async def trigger_update_task(self) -> tuple[bool, str]:
        self.triggered += 1
        return self.task_result


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.tags = TAGS
        self.git_error: Exception | None = None
        self.git_code = 0
        self.pipx_result = (0, "installed\n", "")
        self.pipx_error: Exception | None = None
        self.gate = asyncio.Event()
        self.gate.set()

    async def __call__(self, argv: list[str], **kwargs: Any) -> tuple[int, str, str]:
        self.calls.append((argv, kwargs))
        if argv[0] == "git":
            if self.git_error:
                raise self.git_error
            return self.git_code, self.tags, "fatal"
        await self.gate.wait()
        if self.pipx_error:
            raise self.pipx_error
        return self.pipx_result

    def pipx_calls(self) -> list[tuple[list[str], dict[str, Any]]]:
        return [call for call in self.calls if call[0][0] == "pipx"]


class World:
    def __init__(self, repo: str | None = REPO, version: str = "1.1.0") -> None:
        self.runner = FakeRunner()
        self.events = Collector()
        self.node_service = NodeService("node-a", kind="workstation", version=version, router=NodeRouter("node-a"))
        update.register_update(self.node_service, repo=repo, emit=self.events, run=self.runner)

    async def run(self, case: str) -> dict[str, Any]:
        item = REPLIES[case]
        return await self.node_service.run_command(item["action"], item["args"])

    def state(self) -> dict[str, Any]:
        return self.node_service.state_parts["update"]()

    async def finished(self) -> dict[str, Any]:
        await wait_for(lambda: self.state()["last"] is not None)
        return self.state()


def use_host(monkeypatch: pytest.MonkeyPatch, via_task: bool) -> FakeHost:
    fake = FakeHost(via_task)
    monkeypatch.setattr(host, "current", lambda: fake)
    return fake


@pytest.fixture
def installed(monkeypatch: pytest.MonkeyPatch) -> dict[str, str | None]:
    holder: dict[str, str | None] = {"value": "1.1.0"}
    monkeypatch.setattr(update, "installed_version", lambda: holder["value"])
    return holder


@pytest.fixture
def world(installed: dict, monkeypatch: pytest.MonkeyPatch) -> World:
    use_host(monkeypatch, False)
    return World()


def test_registered_only_with_source(world: World) -> None:
    bare = World(repo=None)
    assert [a.id for a in bare.node_service.describe().actions if "update" in a.id] == []
    assert "update" not in bare.node_service.state_parts
    assert [a.id for a in world.node_service.describe().actions if "update" in a.id] == ["check_update", "update"]


async def test_check_update_matches_reference(world: World) -> None:
    assert await world.run("check_update") == REPLIES["check_update"]["reply"]


@pytest.mark.parametrize("case", ["check_update_offline", "update_offline"])
@pytest.mark.parametrize("failure", ["error", "code", "no_tags"])
async def test_offline_errors_match_reference(world: World, case: str, failure: str) -> None:
    if failure == "error":
        world.runner.git_error = OSError("git")
    elif failure == "code":
        world.runner.git_code = 128
    else:
        world.runner.tags = "a\trefs/heads/main\n"
    with pytest.raises(ProtoError) as exc:
        await world.run(case)
    assert {"code": exc.value.code, "message": exc.value.message} == REPLIES[case]["error"]


@pytest.mark.parametrize(("case", "running"), [("up_to_date_restart_required", "1.1.0"), ("up_to_date", "1.2.0")])
async def test_up_to_date_compares_disk_version(installed: dict, monkeypatch, case: str, running: str) -> None:
    use_host(monkeypatch, False)
    installed["value"] = "1.2.0"
    world = World(version=running)
    assert await world.run(case) == REPLIES[case]["reply"]
    assert world.runner.pipx_calls() == []


async def test_scheduled_install_success(world: World) -> None:
    assert await world.run("scheduled") == REPLIES["scheduled"]["reply"]
    assert await world.finished() == REPLIES["get_state_update_done"]["state"]
    assert world.events.events == [("update_finished", EVENTS[0])]
    argv, kwargs = world.runner.pipx_calls()[0]
    assert argv[:3] == ["pipx", "install", "--force"]
    assert argv[3].endswith(f"git+{REPO}@v1.2.0")
    assert kwargs["env"]["PIPX_HOME"] == update.pipx_home()
    assert kwargs["timeout"] == update.PIPX_INSTALL_TIMEOUT_S


async def test_scheduled_install_failure_and_exception(world: World) -> None:
    world.runner.pipx_result = (1, EVENTS[1]["error"], "")
    assert await world.run("scheduled_fails") == REPLIES["scheduled_fails"]["reply"]
    assert await world.finished() == REPLIES["get_state_update_failed"]["state"]
    assert world.events.events == [("update_finished", EVENTS[1])]
    other = World()
    other.runner.pipx_error = OSError("нет pipx")
    await other.run("scheduled")
    assert (await other.finished())["last"] == {"ok": False, "version": "1.2.0", "error": "нет pipx"}


async def test_second_update_rejected_while_running(world: World) -> None:
    world.runner.gate.clear()
    await world.run("scheduled")
    assert world.state() == REPLIES["get_state_update_running"]["state"]
    with pytest.raises(ProtoError) as exc:
        await world.run("already_running")
    assert {"code": exc.value.code, "message": exc.value.message} == REPLIES["already_running"]["error"]
    world.runner.gate.set()
    await world.finished()
    assert len(world.runner.pipx_calls()) == 1
    await world.run("scheduled")
    await wait_for(lambda: len(world.runner.pipx_calls()) == 2)


async def test_windows_runs_task_and_stays_silent(installed: dict, monkeypatch) -> None:
    windows = use_host(monkeypatch, True)
    world = World()
    assert await world.run("windows_scheduled") == REPLIES["windows_scheduled"]["reply"]
    await wait_for(lambda: windows.triggered == 1)
    await asyncio.sleep(0)
    assert (world.runner.pipx_calls(), world.events.events, world.state()["last"]) == ([], [], None)


@pytest.mark.parametrize("exception", [False, True])
async def test_windows_task_failure_emits_event_and_releases_lock(installed: dict, monkeypatch, exception: bool) -> None:
    windows = use_host(monkeypatch, True)
    if exception:
        async def boom() -> tuple[bool, str]:
            raise RuntimeError("сбой")

        windows.trigger_update_task = boom
    else:
        windows.task_result = (False, "нет задачи")
    world = World()
    assert await world.run("windows_task_fails") == REPLIES["windows_task_fails"]["reply"]
    last = (await world.finished())["last"]
    assert last == {"ok": False, "version": "1.2.0", "error": "сбой" if exception else "нет задачи"}
    assert world.events.events == [("update_finished", last)]
    await world.run("windows_task_fails")


@pytest.mark.parametrize(("disk", "restart"), [("1.1.0", False), ("1.2.0", True), (None, False)])
def test_state_restart_required(world: World, installed: dict, disk: str | None, restart: bool) -> None:
    installed["value"] = disk
    assert world.state() == {"running": "1.1.0", "installed": disk, "restart_required": restart, "last": None}


async def test_tags_and_latest_tag() -> None:
    assert update.parse_tags(TAGS) == ["v1.0.0", "v1.2.0", "v1.1.9"]
    assert update.version_key("1.2.3") < update.version_key("1.10.0")
    assert update.version_key("0.21.0-rc1") == (0, 21, 0)
    runner = FakeRunner()
    assert await update.latest_tag(REPO, runner) == "v1.2.0"
    assert runner.calls[0][0] == ["git", "ls-remote", "--tags", "--refs", REPO]
    assert runner.calls[0][1]["timeout"] == update.LS_REMOTE_TIMEOUT_S
    runner.git_error = TimeoutError()
    assert await update.latest_tag(REPO, runner) is None


def venv_python(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spec: str | None) -> Path:
    venv = tmp_path / "pipx" / "venvs" / "sa-home-node"
    (venv / "bin").mkdir(parents=True)
    if spec is not None:
        (venv / "pipx_metadata.json").write_text(json.dumps({"main_package": {"package_or_url": spec}}))
    monkeypatch.setattr(sys, "executable", str(venv / "bin" / "python"))
    return tmp_path / "pipx"


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("sa-home-node[tts, stt,x]@ git+https://h/r.git@v1.0.0", "tts,stt,x"),
        ("sa-home-node[]@ git+https://h/r.git@v1.0.0", None),
        ("git+https://h/r.git@v1.0.0", None),
        (None, None),
    ],
)
def test_installed_extras(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spec: str | None, expected) -> None:
    venv_python(tmp_path, monkeypatch, spec)
    assert update.installed_extras() == expected


async def test_pipx_reinstall(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = venv_python(tmp_path, monkeypatch, SPEC)
    runner = FakeRunner()
    assert await update.pipx_reinstall("https://h/r.git", "v1.2.0", runner) == (True, "installed")
    argv, kwargs = runner.calls[0]
    assert argv[-1] == "sa-home-node[tts] @ git+https://h/r.git@v1.2.0"
    assert kwargs["env"]["PIPX_HOME"] == str(home)
    (home / "venvs" / "sa-home-node" / "pipx_metadata.json").unlink()
    runner.pipx_result = (1, "x" * 3000, "boom")
    ok, output = await update.pipx_reinstall("https://h/r.git", "v1.2.0", runner)
    assert (ok, len(output), output.endswith("boom")) == (False, update.OUTPUT_TAIL, True)
    assert runner.calls[1][0][-1] == "git+https://h/r.git@v1.2.0"
    runner.pipx_error = TimeoutError("долго")
    assert await update.pipx_reinstall("https://h/r.git", "v1.2.0", runner) == (False, "долго")


class FakeDist:
    def __init__(self, direct_url: str | None) -> None:
        self.direct_url = direct_url

    def read_text(self, name: str) -> str | None:
        return self.direct_url


@pytest.mark.parametrize(
    ("direct_url", "expected"),
    [
        ({"url": "https://h/r.git", "vcs_info": {"vcs": "git"}}, "https://h/r.git"),
        ({"url": "file:///r", "dir_info": {"editable": True}}, None),
        ({"url": "https://h/simple/"}, None),
        ("{", None),
        (None, None),
    ],
)
def test_origin_repo_url(monkeypatch: pytest.MonkeyPatch, direct_url, expected) -> None:
    raw = direct_url if direct_url is None or isinstance(direct_url, str) else json.dumps(direct_url)
    monkeypatch.setattr(update.importlib.metadata, "distribution", lambda name: FakeDist(raw))
    assert update.origin_repo_url() == expected


def test_package_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> None:
        raise update.importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(update.importlib.metadata, "distribution", missing)
    monkeypatch.setattr(update.importlib.metadata, "version", missing)
    assert (update.origin_repo_url(), update.installed_version()) == (None, None)
