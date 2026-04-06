"""Lightweight in-process event bus to decouple modules."""
import asyncio
import logging
from collections import defaultdict
from typing import Callable, Any

logger = logging.getLogger("localmind.events")
_listeners: dict[str, list[Callable]] = defaultdict(list)


def on(event: str, callback: Callable):
    """Register a callback for an event."""
    _listeners[event].append(callback)


def off(event: str, callback: Callable):
    """Unregister a callback for an event."""
    _listeners[event] = [cb for cb in _listeners[event] if cb != callback]


async def emit(event: str, **data: Any):
    """Fire an event, calling all registered listeners."""
    for cb in _listeners.get(event, []):
        try:
            result = cb(**data)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            logger.error(f"Event handler error for '{event}': {e}")
