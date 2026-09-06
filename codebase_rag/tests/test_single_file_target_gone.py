"""A single-file target deleted after construction is a deletion, not a no-op.

`GraphUpdater(repo_path=<existing file>)` records the file as its single
target. If the file is deleted between construction and `run()`, the run used
to take the unreadable path: it logged "Skipped 1 unreadable files", returned
normally and republished a hash cache that still carried the file, with the
file's definitions still in the registry, so the caller got a success signal
for a file that was not indexed (issue #1737; the orphan prune had already
removed its graph nodes). The run now removes the file's state, its Module
subtree and File node, and its cache entry, the way a project run treats a
deleted file.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from codebase_rag import constants as cs
from codebase_rag.graph_updater import GraphUpdater
from codebase_rag.parser_loader import load_parsers
from evals.cgr_graph import _StatefulIngestor


def _create_graph_updater(target: Path, store: _StatefulIngestor) -> GraphUpdater:
    parsers, queries = load_parsers()
    return GraphUpdater(
        ingestor=store,
        repo_path=target,
        parsers=parsers,
        queries=queries,
        project_name="proj",
    )


def _writes(spy: MagicMock) -> list[tuple[str, dict[str, object]]]:
    # The updater may reach the store through a forwarding wrapper that passes
    # the query and params by keyword, so read both spellings.
    out: list[tuple[str, dict[str, object]]] = []
    for call in spy.call_args_list:
        query = call.args[0] if call.args else call.kwargs["query"]
        params = call.args[1] if len(call.args) > 1 else call.kwargs.get("params")
        out.append((str(query), dict(params or {})))
    return out


def _cache(root: Path) -> dict[str, str]:
    return json.loads((root / cs.HASH_CACHE_FILENAME).read_text(encoding="utf-8"))


def test_a_single_file_target_gone_before_run_is_deleted_not_skipped(
    temp_repo: Path,
) -> None:
    (temp_repo / "module_a.py").write_text("def func_a():\n    pass\n")
    (temp_repo / "module_b.py").write_text("def func_b():\n    pass\n")
    store = _StatefulIngestor()
    _create_graph_updater(temp_repo, store).run()
    store.flush_all()
    before = _cache(temp_repo)
    assert "module_a.py" in before, f"fixture guard: {before}"

    target = temp_repo / "module_a.py"
    updater = _create_graph_updater(target, store)
    target.unlink()
    with patch.object(store, "execute_write", wraps=store.execute_write) as spy:
        updater.run()
    store.flush_all()

    deletes = [
        (query, params.get(cs.KEY_PATH))
        for query, params in _writes(spy)
        if query in (cs.CYPHER_DELETE_MODULE, cs.CYPHER_DELETE_FILE)
    ]
    assert (cs.CYPHER_DELETE_MODULE, "module_a.py") in deletes, (
        f"the deleted target's Module subtree was not removed: {deletes}"
    )
    assert (cs.CYPHER_DELETE_FILE, target.resolve().as_posix()) in deletes, (
        f"the deleted target's File node was not removed: {deletes}"
    )
    # Exactly the target: a single-file run must not sweep its siblings.
    assert all(
        path in ("module_a.py", target.resolve().as_posix()) for _, path in deletes
    ), deletes
    assert not any(
        qn.startswith("proj.module_a") for qn in updater.function_registry.keys()
    ), "the deleted target's definitions are still registered"

    after = _cache(temp_repo)
    assert "module_a.py" not in after, (
        f"the cache still lists the deleted target: {after}"
    )
    assert after.get("module_b.py") == before["module_b.py"], (
        "the sibling's cache entry did not survive the single-file run"
    )


def test_a_single_file_target_that_still_exists_is_indexed_as_before(
    temp_repo: Path,
) -> None:
    # The control: the deletion branch must not fire for a present target.
    (temp_repo / "module_a.py").write_text("def func_a():\n    pass\n")
    store = _StatefulIngestor()
    _create_graph_updater(temp_repo, store).run()
    store.flush_all()

    target = temp_repo / "module_a.py"
    target.write_text("def func_a():\n    return 1\n")
    updater = _create_graph_updater(target, store)
    with patch.object(store, "execute_write", wraps=store.execute_write) as spy:
        updater.run()
    store.flush_all()

    assert not [
        query for query, _params in _writes(spy) if query == cs.CYPHER_DELETE_FILE
    ], "a present single-file target was treated as deleted"
    assert "module_a.py" in _cache(temp_repo)
    assert "proj.module_a.func_a" in updater.function_registry
