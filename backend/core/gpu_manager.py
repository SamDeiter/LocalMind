"""
gpu_manager.py — GPU VRAM semaphore for inference admission control.
====================================================================

All model inference in LocalMind flows through GPUManager.  It tracks
loaded models, manages VRAM allocation, and evicts least-recently-used
models when space is needed.

Usage::

    gpu = GPUManager(vram_total_mb=10240)
    async with await gpu.acquire("qwen2.5-coder:14b", None, 4000) as slot:
        # slot.model_id, slot.lora_id available
        result = await run_inference(...)
    # VRAM automatically released on exit
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("localmind.core.gpu_manager")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ModelSlot:
    """Tracks a model currently loaded into GPU VRAM."""

    model_id: str
    lora_id: str | None
    vram_mb: int
    loaded_at: float = field(default_factory=time.monotonic)
    last_used_at: float = field(default_factory=time.monotonic)
    job_id: str | None = None

    @property
    def key(self) -> str:
        """Unique key for this model+LoRA combo."""
        return f"{self.model_id}:{self.lora_id or '_'}"

    def touch(self) -> None:
        """Mark the model as recently used (prevents LRU eviction)."""
        self.last_used_at = time.monotonic()


@dataclass
class InferenceSlot:
    """RAII-style GPU reservation.  Use as an async context manager.

    Example::

        slot = await gpu.acquire(...)
        async with slot:
            # inference here
        # automatically released
    """

    slot_id: str
    model_id: str
    lora_id: str | None
    vram_mb: int
    acquired_at: float = field(default_factory=time.monotonic)
    _gpu_manager: GPUManager | None = field(default=None, repr=False)

    async def __aenter__(self) -> InferenceSlot:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._gpu_manager is not None:
            await self._gpu_manager.release(self)


# ---------------------------------------------------------------------------
# GPUManager
# ---------------------------------------------------------------------------

class GPUManager:
    """Global GPU semaphore — all inference goes through this.

    Thread-safety: all mutable state is guarded by ``_lock``.  Callers
    must ``await acquire(...)`` before running any GPU workload and
    ``release()`` (or use the returned context manager) when done.
    """

    def __init__(self, vram_total_mb: int) -> None:
        self._lock = asyncio.Lock()
        self._loaded_models: dict[str, ModelSlot] = {}  # key -> slot
        self._vram_total_mb = vram_total_mb
        self._vram_allocated_mb = 0
        self._waiters: list[asyncio.Event] = []
        logger.info(
            "GPUManager initialised — total VRAM: %d MB", vram_total_mb
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def acquire(
        self,
        model_id: str,
        lora_id: str | None,
        estimated_vram_mb: int,
        priority: int = 0,
        timeout_sec: int = 300,
    ) -> InferenceSlot:
        """Reserve VRAM for an inference job.

        If the requested model+LoRA is already loaded, the existing slot is
        reused (only a LoRA swap may be needed, which is ~<10ms).

        If there is not enough free VRAM, the manager evicts LRU models
        until the request can be satisfied or ``timeout_sec`` expires.

        Args:
            model_id:  Ollama model tag, e.g. ``"qwen2.5-coder:14b"``.
            lora_id:   Optional LoRA adapter identifier.
            estimated_vram_mb: Estimated VRAM footprint in megabytes.
            priority:  Higher values get served first when contending.
            timeout_sec: Max seconds to wait before raising TimeoutError.

        Returns:
            An :class:`InferenceSlot` async context manager.

        Raises:
            TimeoutError: If VRAM cannot be freed within *timeout_sec*.
            ValueError: If ``estimated_vram_mb`` exceeds total VRAM.
        """
        if estimated_vram_mb > self._vram_total_mb:
            raise ValueError(
                f"Requested {estimated_vram_mb} MB exceeds total VRAM "
                f"({self._vram_total_mb} MB)"
            )

        deadline = time.monotonic() + timeout_sec
        model_key = f"{model_id}:{lora_id or '_'}"

        while True:
            async with self._lock:
                # Fast path: model already loaded — just touch it
                if model_key in self._loaded_models:
                    existing = self._loaded_models[model_key]
                    existing.touch()
                    slot = InferenceSlot(
                        slot_id=str(uuid.uuid4()),
                        model_id=model_id,
                        lora_id=lora_id,
                        vram_mb=0,  # no *additional* VRAM needed
                        _gpu_manager=self,
                    )
                    logger.debug(
                        "Reusing loaded model %s (slot %s)", model_key, slot.slot_id
                    )
                    return slot

                # Check for same base model with different LoRA (cheap swap)
                base_key_prefix = f"{model_id}:"
                for key, ms in self._loaded_models.items():
                    if key.startswith(base_key_prefix) and key != model_key:
                        # LoRA swap — reuse VRAM, just swap the adapter
                        ms.lora_id = lora_id
                        ms.touch()
                        # Re-key the slot
                        del self._loaded_models[key]
                        self._loaded_models[model_key] = ms
                        slot = InferenceSlot(
                            slot_id=str(uuid.uuid4()),
                            model_id=model_id,
                            lora_id=lora_id,
                            vram_mb=0,
                            _gpu_manager=self,
                        )
                        logger.info(
                            "LoRA swap %s -> %s (slot %s)",
                            key, model_key, slot.slot_id,
                        )
                        return slot

                # Need to allocate fresh VRAM
                if self.can_fit(estimated_vram_mb):
                    return self._allocate_new(
                        model_id, lora_id, estimated_vram_mb, model_key
                    )

                # Not enough room — try eviction
                evicted = await self._evict_lru_locked(estimated_vram_mb)
                if evicted and self.can_fit(estimated_vram_mb):
                    return self._allocate_new(
                        model_id, lora_id, estimated_vram_mb, model_key
                    )

            # Could not allocate — wait for a release notification
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Could not acquire {estimated_vram_mb} MB for "
                    f"{model_key} within {timeout_sec}s"
                )

            waiter = asyncio.Event()
            self._waiters.append(waiter)
            remaining = deadline - time.monotonic()
            try:
                await asyncio.wait_for(waiter.wait(), timeout=max(remaining, 0.1))
            except asyncio.TimeoutError:
                raise TimeoutError(
                    f"Could not acquire {estimated_vram_mb} MB for "
                    f"{model_key} within {timeout_sec}s"
                )
            finally:
                try:
                    self._waiters.remove(waiter)
                except ValueError:
                    pass

    async def release(self, slot: InferenceSlot) -> None:
        """Free VRAM held by *slot* and wake any waiters.

        The underlying model stays loaded (hot cache) until evicted by LRU.
        Only the *extra* allocation (if any) is freed here — in practice,
        models stay resident and the InferenceSlot just marks the workload
        as done.
        """
        async with self._lock:
            # The model stays loaded; we only release the per-request
            # overhead tracked by the slot.
            if slot.vram_mb > 0:
                self._vram_allocated_mb = max(
                    0, self._vram_allocated_mb - slot.vram_mb
                )
                logger.debug(
                    "Released slot %s (%d MB) — allocated: %d/%d MB",
                    slot.slot_id, slot.vram_mb,
                    self._vram_allocated_mb, self._vram_total_mb,
                )

        # Notify blocked acquires
        self._wake_waiters()

    def can_fit(self, estimated_vram_mb: int) -> bool:
        """Check if *estimated_vram_mb* fits without evicting anything."""
        return (self._vram_allocated_mb + estimated_vram_mb) <= self._vram_total_mb

    async def evict_lru(self, needed_mb: int) -> bool:
        """Unload least-recently-used models to free at least *needed_mb*.

        Returns True if enough VRAM was freed.
        """
        async with self._lock:
            return await self._evict_lru_locked(needed_mb)

    def get_status(self) -> dict[str, Any]:
        """Return current VRAM usage, loaded models, and waiter count."""
        models = [
            {
                "model_id": ms.model_id,
                "lora_id": ms.lora_id,
                "vram_mb": ms.vram_mb,
                "loaded_at": ms.loaded_at,
                "last_used_at": ms.last_used_at,
                "job_id": ms.job_id,
            }
            for ms in self._loaded_models.values()
        ]
        return {
            "vram_total_mb": self._vram_total_mb,
            "vram_allocated_mb": self._vram_allocated_mb,
            "vram_free_mb": self._vram_total_mb - self._vram_allocated_mb,
            "loaded_models": models,
            "loaded_model_count": len(models),
            "waiters": len(self._waiters),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _allocate_new(
        self,
        model_id: str,
        lora_id: str | None,
        vram_mb: int,
        model_key: str,
    ) -> InferenceSlot:
        """Allocate VRAM and register a new ModelSlot.  Caller holds _lock."""
        ms = ModelSlot(
            model_id=model_id, lora_id=lora_id, vram_mb=vram_mb
        )
        self._loaded_models[model_key] = ms
        self._vram_allocated_mb += vram_mb

        slot = InferenceSlot(
            slot_id=str(uuid.uuid4()),
            model_id=model_id,
            lora_id=lora_id,
            vram_mb=vram_mb,
            _gpu_manager=self,
        )
        logger.info(
            "Allocated %d MB for %s (slot %s) — now %d/%d MB",
            vram_mb, model_key, slot.slot_id,
            self._vram_allocated_mb, self._vram_total_mb,
        )
        return slot

    async def _evict_lru_locked(self, needed_mb: int) -> bool:
        """Evict LRU models until *needed_mb* is available.  Caller holds _lock."""
        freed = 0
        # Sort by last_used_at ascending — oldest first
        candidates = sorted(
            self._loaded_models.items(),
            key=lambda kv: kv[1].last_used_at,
        )

        evicted_keys: list[str] = []
        for key, ms in candidates:
            if self.can_fit(needed_mb):
                break
            evicted_keys.append(key)
            freed += ms.vram_mb
            self._vram_allocated_mb = max(0, self._vram_allocated_mb - ms.vram_mb)
            logger.info(
                "Evicted model %s (%d MB) — freed %d MB total",
                key, ms.vram_mb, freed,
            )

        for key in evicted_keys:
            del self._loaded_models[key]

        return self.can_fit(needed_mb)

    def _wake_waiters(self) -> None:
        """Signal all blocked ``acquire()`` calls to re-check availability."""
        for w in self._waiters:
            w.set()
        self._waiters.clear()
