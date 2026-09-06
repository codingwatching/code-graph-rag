"""The integration container reaper removes exactly our orphaned corpses (#1628)."""

from __future__ import annotations

import ast
import os
from pathlib import Path
from unittest.mock import MagicMock

from loguru import logger

from codebase_rag.tests.container_reaper import (
    CGR_CONTAINER_LABEL,
    CGR_CONTAINER_LABEL_VALUE,
    CGR_OWNER_HOST_LABEL,
    CGR_OWNER_PID_LABEL,
    cgr_container_labels,
    pid_is_alive,
    reap_orphaned_containers,
)

HOST = "this-box"
DEAD = {4242}


def _dead(pid: int) -> bool:
    return pid not in DEAD


def _container(name: str, *, host: str = HOST, pid: int | str = 4242) -> MagicMock:
    container = MagicMock(name=name)
    container.name = name
    container.labels = cgr_container_labels(host=host, pid=pid)  # type: ignore[arg-type]
    if not isinstance(pid, int):
        container.labels[CGR_OWNER_PID_LABEL] = str(pid)
    return container


def _client(*containers: MagicMock) -> MagicMock:
    client = MagicMock()
    client.containers.list.return_value = list(containers)
    return client


def _reap(client: MagicMock) -> list[str]:
    return reap_orphaned_containers(client, host=HOST, pid_alive=_dead)


def test_every_orphaned_container_is_force_removed() -> None:
    first, second = _container("magical_archimedes"), _container("adoring_edison")
    client = _client(first, second)

    removed = _reap(client)

    client.containers.list.assert_called_once_with(
        all=True,
        filters={"label": f"{CGR_CONTAINER_LABEL}={CGR_CONTAINER_LABEL_VALUE}"},
    )
    first.remove.assert_called_once_with(force=True)
    second.remove.assert_called_once_with(force=True)
    assert removed == ["magical_archimedes", "adoring_edison"]


def test_a_live_sessions_container_is_kept() -> None:
    # Two sessions on one daemon: the later one must not remove the earlier
    # one's running database (#1751 review). The live pid is the discriminator.
    live, dead = _container("live", pid=os.getpid()), _container("dead")
    client = _client(live, dead)

    removed = _reap(client)

    live.remove.assert_not_called()
    dead.remove.assert_called_once_with(force=True)
    assert removed == ["dead"]


def test_containers_this_host_cannot_judge_are_kept() -> None:
    # Another host's pid space is not ours to probe; a container with no
    # owner labels, or an unparseable pid, is not provably a corpse either.
    elsewhere = _container("elsewhere", host="other-box")
    unowned = MagicMock()
    unowned.name, unowned.labels = (
        "unowned",
        {CGR_CONTAINER_LABEL: CGR_CONTAINER_LABEL_VALUE},
    )
    garbled = _container("garbled", pid="not-a-pid")
    client = _client(elsewhere, unowned, garbled)

    assert _reap(client) == []
    for container in (elsewhere, unowned, garbled):
        container.remove.assert_not_called()


def test_a_container_that_refuses_to_go_is_logged_and_does_not_stop_the_others() -> (
    None
):
    stuck, fine = _container("stuck"), _container("fine")
    stuck.remove.side_effect = RuntimeError("engine error")
    client = _client(stuck, fine)
    records: list[str] = []
    sink = logger.add(lambda m: records.append(str(m)), level="WARNING")
    try:
        removed = _reap(client)
    finally:
        logger.remove(sink)

    fine.remove.assert_called_once_with(force=True)
    assert removed == ["fine"], "the failure must be skipped, not raised or counted"
    assert any("stuck" in r and "engine error" in r for r in records), records


def test_nothing_to_reap_is_a_no_op() -> None:
    assert _reap(_client()) == []


def test_pid_probe_tells_this_process_from_a_dead_one() -> None:
    assert pid_is_alive(os.getpid())
    if os.name != "nt":
        # A pid no process holds; the largest allowed value is never in use
        # on a box that is not at its process limit.
        assert not pid_is_alive(2**22 - 1)


def test_own_labels_name_this_session() -> None:
    labels = cgr_container_labels()
    assert labels[CGR_CONTAINER_LABEL] == CGR_CONTAINER_LABEL_VALUE
    assert labels[CGR_OWNER_HOST_LABEL]
    assert labels[CGR_OWNER_PID_LABEL] == str(os.getpid())


def test_the_fixture_labels_its_container_and_reaps_before_starting() -> None:
    """The fixture needs Docker, so its wiring is pinned at the source level.

    Both halves are load-bearing and neither implies the other: without the
    label the reaper matches nothing, and without the reaper call the label
    is decoration. The reaper must also run BEFORE `start()`, or a session
    would remove the container it just created.
    """
    source = (Path(__file__).parent / "integration" / "conftest.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    fixture = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "memgraph_container"
        ),
        None,
    )
    assert fixture is not None, "the memgraph_container fixture is gone"
    # Walk the FIXTURE only: a same-named call elsewhere in the module must
    # not satisfy this sequence while the fixture itself is miswired (#1751
    # review).
    calls: list[str] = []
    for node in ast.walk(fixture):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else getattr(func, "id", "")
            )
            if name in {"reap_orphaned_containers", "with_kwargs", "start"}:
                calls.append(name)
                if name == "with_kwargs":
                    labels = {kw.arg: kw.value for kw in node.keywords}
                    assert "labels" in labels, "with_kwargs must pass labels="
                    value = labels["labels"]
                    assert isinstance(value, ast.Call), (
                        "labels= must be a call, or the reaper matches nothing"
                    )
                    assert getattr(value.func, "id", "") == "cgr_container_labels", (
                        "labels= must be cgr_container_labels(), or the reaper matches nothing"
                    )
    assert "reap_orphaned_containers" in calls, "the fixture never reaps"
    assert "with_kwargs" in calls, "the fixture never labels its container"
    assert calls.index("reap_orphaned_containers") < calls.index("start"), (
        f"the reaper must run before start(): {calls}"
    )
    # `with_kwargs` REPLACES the container's kwargs, so it must precede
    # start() and be the last such call, or the label is silently dropped.
    assert calls.index("with_kwargs") < calls.index("start"), (
        f"the label must be set before start(): {calls}"
    )
    assert calls.count("with_kwargs") == 1, calls
