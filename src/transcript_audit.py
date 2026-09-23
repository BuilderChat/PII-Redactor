from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from threading import BoundedSemaphore, Lock
from typing import Sequence

from .allowlist_cache import LocalAllowlistCache
from .pii_engine import PIIEngine
from .transcript_replay import TranscriptReplayResult, TranscriptTurn, replay_transcript


class TranscriptAuditSaturatedError(RuntimeError):
    """Raised when all bounded audit workers are occupied."""


class TranscriptAuditTimeoutError(RuntimeError):
    """Raised when an audit exceeds its configured response deadline."""


class TranscriptAuditService:
    """Runs isolated transcript replays without creating live middleware scopes."""

    def __init__(
        self,
        *,
        engine: PIIEngine,
        allowlist_cache: LocalAllowlistCache | None = None,
        max_turns: int = 200,
        max_characters: int = 100_000,
        timeout_seconds: float = 2.0,
        max_concurrency: int = 2,
        acquire_timeout_seconds: float = 0.1,
    ) -> None:
        self._engine = engine
        self._allowlist_cache = allowlist_cache
        self._max_turns = max(1, int(max_turns))
        self._max_characters = max(1, int(max_characters))
        self._timeout_seconds = max(0.01, float(timeout_seconds))
        self._max_concurrency = max(1, int(max_concurrency))
        self._acquire_timeout_seconds = max(0.0, float(acquire_timeout_seconds))
        self._slots = BoundedSemaphore(self._max_concurrency)
        self._executor = ThreadPoolExecutor(
            max_workers=self._max_concurrency,
            thread_name_prefix="pii-transcript-audit",
        )
        self._state_lock = Lock()
        self._active = 0
        self._saturated = 0
        self._timed_out = 0

    @property
    def status(self) -> dict[str, int | float]:
        with self._state_lock:
            return {
                "active": self._active,
                "max_concurrency": self._max_concurrency,
                "saturated_count": self._saturated,
                "timeout_count": self._timed_out,
                "max_turns": self._max_turns,
                "max_characters": self._max_characters,
                "timeout_seconds": self._timeout_seconds,
            }

    def audit(
        self,
        *,
        client_id: str,
        assistant_id: str,
        turns: Sequence[TranscriptTurn],
        non_name_allowlist: Sequence[str] | None = None,
    ) -> TranscriptReplayResult:
        if not self._slots.acquire(timeout=self._acquire_timeout_seconds):
            with self._state_lock:
                self._saturated += 1
            raise TranscriptAuditSaturatedError("Transcript audit concurrency saturated")

        try:
            future = self._executor.submit(
                self._run_replay,
                client_id,
                assistant_id,
                tuple(turns),
                tuple(non_name_allowlist or ()),
            )
        except RuntimeError:
            self._slots.release()
            raise

        with self._state_lock:
            self._active += 1
        future.add_done_callback(self._release_slot)

        try:
            return future.result(timeout=self._timeout_seconds)
        except FutureTimeoutError as exc:
            with self._state_lock:
                self._timed_out += 1
            future.cancel()
            raise TranscriptAuditTimeoutError("Transcript audit timed out") from exc

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _run_replay(
        self,
        client_id: str,
        assistant_id: str,
        turns: tuple[TranscriptTurn, ...],
        request_allowlist: tuple[str, ...],
    ) -> TranscriptReplayResult:
        combined_allowlist = set(request_allowlist)
        if self._allowlist_cache is not None:
            combined_allowlist.update(self._allowlist_cache.get(client_id, assistant_id))

        return replay_transcript(
            turns,
            engine=self._engine,
            non_name_allowlist=sorted(combined_allowlist),
            max_turns=self._max_turns,
            max_characters=self._max_characters,
        )

    def _release_slot(self, _future: Future[TranscriptReplayResult]) -> None:
        with self._state_lock:
            self._active = max(0, self._active - 1)
        self._slots.release()
