"""A deleted file's method return types must leave with its registry rows.

`TypeInferenceEngine.method_return_types` records the return type of C++,
Rust and Dart free functions, of every language's methods, and of Go receiver
methods, under the definition's qualified name. `remove_file_from_state`
pruned the registry and, since #1668, the Go free-function map for a deleted
file, but never this one: on a reused updater a deleted file's entries
persisted, and a Go method keyed by its receiver's module could not even be
reached by a prefix sweep (issue #1738).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codebase_rag.graph_updater import GraphUpdater
from codebase_rag.parser_loader import load_parsers
from codebase_rag.tests.conftest import _MockIngestor

TYPES_GO = "package pkg\n\ntype A struct{}\n\ntype B struct{}\n"
# `Clone` is keyed by A's module (`proj.pkg.types.A.Clone`): no prefix of
# methods.go matches it, so only span-record ownership can find it.
METHODS_GO = "package pkg\n\nfunc (a A) Clone() A { return a }\n"
OTHER_GO = "package pkg\n\nfunc (b B) Twin() B { return b }\n"


def _updater(root: Path) -> GraphUpdater:
    parsers, queries = load_parsers()
    if "go" not in {str(k) for k in parsers}:
        pytest.skip("go parser not available")
    return GraphUpdater(
        ingestor=_MockIngestor(),
        repo_path=root,
        parsers=parsers,
        queries=queries,
        project_name="proj",
    )


def _project(root: Path) -> GraphUpdater:
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "types.go").write_text(TYPES_GO, encoding="utf-8")
    (root / "pkg" / "methods.go").write_text(METHODS_GO, encoding="utf-8")
    (root / "pkg" / "other.go").write_text(OTHER_GO, encoding="utf-8")
    updater = _updater(root)
    updater.run()
    recorded = updater.factory.type_inference.method_return_types
    assert recorded.get("proj.pkg.types.A.Clone") == "A", (
        f"fixture guard: the first run did not record Clone's return type: {recorded}"
    )
    assert recorded.get("proj.pkg.types.B.Twin") == "B", recorded
    return updater


def test_a_deleted_go_files_method_return_types_are_forgotten(
    temp_repo: Path,
) -> None:
    """Delete `methods.go` on a reused updater: `Clone`'s entry must go.

    The entry sits under `types.go`'s prefix, which this event does not
    touch, so this is exactly the qn a prefix sweep cannot reach; the span
    records name `methods.go` as the registering module.
    """
    root = temp_repo / "proj"
    updater = _project(root)
    recorded = updater.factory.type_inference.method_return_types

    (root / "pkg" / "methods.go").unlink()
    updater.remove_file_from_state(root / "pkg" / "methods.go")

    assert "proj.pkg.types.A.Clone" not in recorded, (
        "the deleted file's return-type entry survived remove_file_from_state"
    )
    # The control: another file's method under the SAME receiver module
    # stays; only the deleted file's rows go.
    assert recorded.get("proj.pkg.types.B.Twin") == "B", (
        "a sibling's entry was swept along with the deleted file's"
    )


def test_a_method_another_file_owns_survives_its_receiver_modules_delete(
    temp_repo: Path,
) -> None:
    """Delete `types.go`: the methods keyed under it stay, as their rows do.

    Both methods sit under `proj.pkg.types` although `methods.go` and
    `other.go` declare them, and neither of those files is re-parsed by
    this event. The registry keeps their rows for exactly that reason (the
    span records name the declaring file, so they are foreign to this
    delete), and the return-type map must agree with the registry rather
    than sweep by prefix alone.
    """
    root = temp_repo / "proj"
    updater = _project(root)
    recorded = updater.factory.type_inference.method_return_types

    (root / "pkg" / "types.go").unlink()
    updater.remove_file_from_state(root / "pkg" / "types.go")

    assert "proj.pkg.types.A.Clone" in updater.function_registry, (
        "fixture guard: the registry swept a row another file owns"
    )
    assert recorded.get("proj.pkg.types.A.Clone") == "A", (
        f"the map swept by prefix what the registry kept by ownership: {recorded}"
    )
    assert recorded.get("proj.pkg.types.B.Twin") == "B", recorded


def test_a_reparse_records_the_new_return_type(temp_repo: Path) -> None:
    """The point of forgetting: a re-parse must not be shadowed by the old."""
    root = temp_repo / "proj"
    updater = _project(root)
    recorded = updater.factory.type_inference.method_return_types

    (root / "pkg" / "methods.go").write_text(
        "package pkg\n\nfunc (a A) Clone() B { return B{} }\n", encoding="utf-8"
    )
    updater.remove_file_from_state(root / "pkg" / "methods.go")
    assert "proj.pkg.types.A.Clone" not in recorded
    updater.run()

    assert recorded.get("proj.pkg.types.A.Clone") == "B", recorded
