"""Reap integration-suite Memgraph containers a killed run left behind (#1628).

`memgraph_container` stops its container after `yield`, which is correct for
every run that ends normally. A run killed by an OOM kill, a CI timeout or a
`kill -9` never reaches that line, and nothing in-process can: eight such
containers were found running on one box, one per killed run, the oldest three
weeks old and each a permanent charge against the memory the next run needs.

The remedy that survives the process dying is external to it: label our
containers with a marker of our own, and at the START of a session remove
every container carrying it. The suite runs one container per session and the
host runs one suite at a time, so a labelled container that already exists
when a session starts is a previous run's corpse.
"""

from __future__ import annotations

from typing import Protocol

CGR_CONTAINER_LABEL = "cgr.integration"
CGR_CONTAINER_LABEL_VALUE = "memgraph"


class _Removable(Protocol):
    name: str

    def remove(self, force: bool = ...) -> None: ...


class _Containers(Protocol):
    def list(self, all: bool = ..., filters: dict[str, str] | None = ...) -> list: ...


class ContainerClient(Protocol):
    containers: _Containers


def cgr_container_labels() -> dict[str, str]:
    return {CGR_CONTAINER_LABEL: CGR_CONTAINER_LABEL_VALUE}


def reap_orphaned_containers(client: ContainerClient) -> list[str]:
    """Remove every container carrying our label; return the names removed.

    `all=True` so a stopped-but-not-removed corpse goes too. A container that
    refuses to go (already gone, or an engine error) is skipped rather than
    failing the session: the reaper is housekeeping, not a precondition.
    """
    removed: list[str] = []
    label = f"{CGR_CONTAINER_LABEL}={CGR_CONTAINER_LABEL_VALUE}"
    for container in client.containers.list(all=True, filters={"label": label}):
        try:
            container.remove(force=True)
        except Exception:  # noqa: BLE001 -- housekeeping must not fail the run
            continue
        removed.append(str(getattr(container, "name", "")))
    return removed
