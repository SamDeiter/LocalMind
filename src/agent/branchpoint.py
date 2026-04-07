"""
Branchpoint Tracking (EnCompass pattern) for the ReAct agent.

Saves snapshots of conversation state before risky tool calls so the
agent can roll back and try a different approach when something fails
or the loop detects it is going in circles.
"""

import copy
import logging
from dataclasses import dataclass, field

logger = logging.getLogger("agent.branchpoint")


@dataclass
class _Snapshot:
    """Internal representation of a single branchpoint."""
    label: str
    messages: list[dict]
    iteration: int


class BranchpointManager:
    """Per-run manager that stores and restores conversation snapshots."""

    def __init__(self) -> None:
        self._snapshots: dict[str, _Snapshot] = {}
        # Ordered list of labels so we can find "most recent" easily
        self._order: list[str] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save(self, label: str, messages: list[dict], iteration: int) -> None:
        """Save a deep-copied snapshot of *messages* under *label*."""
        snapshot = _Snapshot(
            label=label,
            messages=copy.deepcopy(messages),
            iteration=iteration,
        )
        self._snapshots[label] = snapshot
        # Keep order deduplicated — if label already tracked, move to end
        if label in self._order:
            self._order.remove(label)
        self._order.append(label)
        logger.debug("Saved branchpoint '%s' at iteration %d (%d messages)",
                      label, iteration, len(messages))

    def restore(self, label: str) -> list[dict]:
        """Return the deep-copied messages from branchpoint *label*.

        Raises ``KeyError`` if no branchpoint with that label exists.
        """
        snapshot = self._snapshots[label]
        logger.info("Restoring branchpoint '%s' (iteration %d, %d messages)",
                     label, snapshot.iteration, len(snapshot.messages))
        return copy.deepcopy(snapshot.messages)

    def latest_label(self) -> str | None:
        """Return the label of the most recently saved branchpoint, or None."""
        return self._order[-1] if self._order else None

    def list_branchpoints(self) -> list[str]:
        """Return labels of all saved branchpoints in chronological order."""
        return list(self._order)

    def clear(self) -> None:
        """Remove all saved branchpoints."""
        self._snapshots.clear()
        self._order.clear()
        logger.debug("Cleared all branchpoints")
