"""The integration container reaper removes exactly our labelled corpses (#1628)."""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock

from codebase_rag.tests.container_reaper import (
    CGR_CONTAINER_LABEL,
    CGR_CONTAINER_LABEL_VALUE,
    cgr_container_labels,
    reap_orphaned_containers,
)


def _client(*containers: MagicMock) -> MagicMock:
    client = MagicMock()
    client.containers.list.return_value = list(containers)
    return client


def test_every_labelled_container_is_force_removed() -> None:
    first, second = MagicMock(name="c1"), MagicMock(name="c2")
    first.name, second.name = "magical_archimedes", "adoring_edison"
    client = _client(first, second)

    removed = reap_orphaned_containers(client)

    client.containers.list.assert_called_once_with(
        all=True,
        filters={"label": f"{CGR_CONTAINER_LABEL}={CGR_CONTAINER_LABEL_VALUE}"},
    )
    first.remove.assert_called_once_with(force=True)
    second.remove.assert_called_once_with(force=True)
    assert removed == ["magical_archimedes", "adoring_edison"]


def test_a_container_that_refuses_to_go_does_not_stop_the_others() -> None:
    stuck, fine = MagicMock(), MagicMock()
    stuck.name, fine.name = "stuck", "fine"
    stuck.remove.side_effect = RuntimeError("engine error")
    client = _client(stuck, fine)

    removed = reap_orphaned_containers(client)

    fine.remove.assert_called_once_with(force=True)
    assert removed == ["fine"], "the failure must be skipped, not raised or counted"


def test_nothing_to_reap_is_a_no_op() -> None:
    assert reap_orphaned_containers(_client()) == []


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
    calls: list[str] = []
    for node in ast.walk(tree):
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
                    assert (
                        isinstance(value, ast.Call)
                        and getattr(value.func, "id", "") == "cgr_container_labels"
                    ), (
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
    assert cgr_container_labels() == {CGR_CONTAINER_LABEL: CGR_CONTAINER_LABEL_VALUE}
