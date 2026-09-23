from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

from .pii_engine import PIIEngine
from .pii_vault import PIIVault


DEFAULT_MAX_REPLAY_TURNS = 200
DEFAULT_MAX_REPLAY_CHARACTERS = 100_000

ReplayRole = Literal["assistant", "agent", "user"]


@dataclass(frozen=True)
class TranscriptTurn:
    role: ReplayRole
    content: str


@dataclass(frozen=True)
class ReplayedUserTurn:
    turn_index: int
    original_text: str
    redacted_text: str
    replacements: dict[str, str]


@dataclass(frozen=True)
class TranscriptReplayResult:
    user_turns: tuple[ReplayedUserTurn, ...]
    token_to_value: dict[str, str]


def replay_transcript(
    turns: Sequence[TranscriptTurn],
    *,
    engine: PIIEngine,
    non_name_allowlist: Sequence[str] | None = None,
    max_turns: int = DEFAULT_MAX_REPLAY_TURNS,
    max_characters: int = DEFAULT_MAX_REPLAY_CHARACTERS,
) -> TranscriptReplayResult:
    """Redact user turns sequentially in a fresh, replay-only vault.

    The returned replacement evidence contains raw values and must remain inside
    the trusted redaction boundary. This helper performs no logging or I/O.
    """
    if max_turns < 1:
        raise ValueError("max_turns must be positive")
    if max_characters < 1:
        raise ValueError("max_characters must be positive")
    if len(turns) > max_turns:
        raise ValueError(f"Transcript exceeds the {max_turns}-turn replay limit")

    total_characters = 0
    normalized_turns: list[tuple[str, str]] = []
    for turn in turns:
        role = str(turn.role).strip().lower()
        if role not in {"assistant", "agent", "user"}:
            raise ValueError(f"Unsupported transcript role: {turn.role!r}")
        if not isinstance(turn.content, str) or not turn.content.strip():
            raise ValueError("Transcript turn content must be a non-empty string")
        total_characters += len(turn.content)
        if total_characters > max_characters:
            raise ValueError(f"Transcript exceeds the {max_characters}-character replay limit")
        normalized_turns.append((role, turn.content))

    vault = PIIVault()
    allowlist = tuple(non_name_allowlist or ())
    previous_assistant_message: str | None = None
    replayed_user_turns: list[ReplayedUserTurn] = []

    for turn_index, (role, content) in enumerate(normalized_turns):
        if role in {"assistant", "agent"}:
            previous_assistant_message = content
            continue

        result = engine.redact(
            content,
            vault,
            previous_assistant_message=previous_assistant_message,
            non_name_allowlist=allowlist,
        )
        replayed_user_turns.append(
            ReplayedUserTurn(
                turn_index=turn_index,
                original_text=content,
                redacted_text=result.redacted_text,
                replacements=dict(result.replacements),
            )
        )

    return TranscriptReplayResult(
        user_turns=tuple(replayed_user_turns),
        token_to_value=vault.items(),
    )
