from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re

from src.pii_engine import PIIEngine
from src.pii_vault import PIIVault


THREAD_START_RE = re.compile(r"^Started:\s+.*\|\s+Thread:\s+(?P<thread>\S+)\s*$")
THREAD_LINE_RE = re.compile(r"^Thread\s+(?P<thread>\S+):\s*$")
ROLE_HEADER_RE = re.compile(r"^(?P<role>User|Agent)\s+\([^)]*\):\s*$")
ROLE_INLINE_RE = re.compile(r"^(?P<prefix>(?P<role>User|Agent)\s+\([^)]*\):)\s*(?P<message>.*\S)\s*$")
SEPARATOR_PREFIX = "=" * 20
DASH_SEPARATOR_RE = re.compile(r"^-{10,}\s*$")


@dataclass
class Stats:
    threads: int = 0
    user_blocks: int = 0
    agent_blocks: int = 0
    processed_lines: int = 0
    replaced_tokens: int = 0



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Redact raw conversation transcript fixture files and write a sibling "
            "redacted file for manual diff review."
        )
    )
    parser.add_argument("input_file", type=Path, help="Path to source transcript file")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional output path. Default: <input_stem>.redacted<input_suffix>",
    )
    parser.add_argument(
        "--mode",
        choices=("user-only", "both"),
        default="user-only",
        help="Redact only User blocks (default) or both User/Agent blocks.",
    )
    parser.add_argument(
        "--engine-tag",
        type=str,
        default="",
        help="Optional suffix added to output filename before extension, e.g. 'heuristic' or 'gliner'.",
    )
    parser.add_argument(
        "--community-tree",
        type=Path,
        default=None,
        help="Optional JSON file with city/community terms to treat as non-name allowlist values.",
    )
    parser.add_argument(
        "--floor-plans",
        type=Path,
        default=None,
        help="Optional floor-plan JSON file; all 'name' fields are treated as non-name allowlist values.",
    )
    return parser.parse_args()



def derive_output_path(input_path: Path, explicit_output: Path | None, engine_tag: str) -> Path:
    if explicit_output:
        return explicit_output
    tag = f".{engine_tag}" if engine_tag else ""
    return input_path.with_name(f"{input_path.stem}.redacted{tag}{input_path.suffix}")



def is_boundary_line(line: str) -> bool:
    stripped = line.rstrip("\n")
    if ROLE_HEADER_RE.match(stripped):
        return True
    if ROLE_INLINE_RE.match(stripped):
        return True
    if THREAD_START_RE.match(stripped):
        return True
    if THREAD_LINE_RE.match(stripped):
        return True
    if stripped.startswith(SEPARATOR_PREFIX):
        return True
    if DASH_SEPARATOR_RE.match(stripped):
        return True
    return False



def extract_thread_id(line: str) -> str | None:
    match = THREAD_START_RE.match(line)
    if match:
        return match.group("thread")
    match = THREAD_LINE_RE.match(line)
    if match:
        return match.group("thread")
    return None



def load_non_name_terms_from_json(path: Path | None) -> list[str]:
    if path is None:
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    terms: set[str] = set()

    def _walk(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if isinstance(key, str):
                    terms.add(key)
                _walk(child)
            return
        if isinstance(value, list):
            for item in value:
                _walk(item)
            return
        if isinstance(value, str):
            terms.add(value)

    _walk(payload)
    return sorted(terms)


def load_floor_plan_name_terms(path: Path | None) -> list[str]:
    if path is None:
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    terms: set[str] = set()

    def _walk(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "name" and isinstance(child, str):
                    terms.add(child)
                _walk(child)
            return
        if isinstance(value, list):
            for item in value:
                _walk(item)
            return

    _walk(payload)
    return sorted(terms)



def redact_message_block(
    lines: list[str],
    engine: PIIEngine,
    vault: PIIVault,
    stats: Stats,
    previous_assistant_message: str | None,
    non_name_allowlist: list[str],
) -> list[str]:
    out: list[str] = []

    for raw_line in lines:
        stats.processed_lines += 1
        has_newline = raw_line.endswith("\n")
        content = raw_line[:-1] if has_newline else raw_line
        stripped = content.strip()

        if not stripped:
            out.append(raw_line)
            continue

        if stripped.startswith("[Intent:") or stripped.startswith("[Area:"):
            out.append(raw_line)
            continue

        result = engine.redact(
            content,
            vault,
            previous_assistant_message=previous_assistant_message,
            non_name_allowlist=non_name_allowlist,
        )
        stats.replaced_tokens += len(result.replacements)
        out.append(result.redacted_text + ("\n" if has_newline else ""))

    return out



def redact_transcript(
    input_path: Path,
    output_path: Path,
    redact_mode: str,
    non_name_allowlist: list[str],
) -> Stats:
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    source_lines = input_path.read_text(encoding="utf-8").splitlines(keepends=True)

    engine = PIIEngine()
    vault_by_thread: dict[str, PIIVault] = {}
    last_agent_message_by_thread: dict[str, str] = {}
    current_thread: str | None = None
    stats = Stats()

    out_lines: list[str] = []
    i = 0

    while i < len(source_lines):
        raw = source_lines[i]
        line = raw.rstrip("\n")

        thread_id = extract_thread_id(line)
        if thread_id:
            if thread_id not in vault_by_thread:
                vault_by_thread[thread_id] = PIIVault()
                stats.threads += 1
            current_thread = thread_id
            out_lines.append(raw)
            i += 1
            continue

        inline_role_match = ROLE_INLINE_RE.match(line)
        if inline_role_match and current_thread is not None:
            role = inline_role_match.group("role")
            prefix = inline_role_match.group("prefix")
            inline_message = inline_role_match.group("message")
            if role == "User":
                stats.user_blocks += 1
            else:
                stats.agent_blocks += 1

            should_redact = role == "User" or redact_mode == "both"
            previous_assistant_message = last_agent_message_by_thread.get(current_thread, "")
            if should_redact:
                result = engine.redact(
                    inline_message,
                    vault_by_thread[current_thread],
                    previous_assistant_message=previous_assistant_message,
                    non_name_allowlist=non_name_allowlist,
                )
                stats.processed_lines += 1
                stats.replaced_tokens += len(result.replacements)
                out_lines.append(f"{prefix} {result.redacted_text}" + ("\n" if raw.endswith("\n") else ""))
            else:
                out_lines.append(raw)
            i += 1

            block_start = i
            while i < len(source_lines) and not is_boundary_line(source_lines[i]):
                i += 1
            block_lines = source_lines[block_start:i]
            if should_redact:
                redacted_block = redact_message_block(
                    block_lines,
                    engine,
                    vault_by_thread[current_thread],
                    stats,
                    previous_assistant_message=previous_assistant_message,
                    non_name_allowlist=non_name_allowlist,
                )
                out_lines.extend(redacted_block)
            else:
                out_lines.extend(block_lines)

            if role == "Agent":
                inline_part = inline_message.strip()
                tail_part = "".join(block_lines).strip()
                if inline_part and tail_part:
                    last_agent_message_by_thread[current_thread] = f"{inline_part}\n{tail_part}"
                elif inline_part:
                    last_agent_message_by_thread[current_thread] = inline_part
                else:
                    last_agent_message_by_thread[current_thread] = tail_part
            continue

        role_match = ROLE_HEADER_RE.match(line)
        if role_match and current_thread is not None:
            role = role_match.group("role")
            if role == "User":
                stats.user_blocks += 1
            else:
                stats.agent_blocks += 1

            out_lines.append(raw)
            i += 1

            block_start = i
            while i < len(source_lines) and not is_boundary_line(source_lines[i]):
                i += 1

            block_lines = source_lines[block_start:i]
            should_redact = role == "User" or redact_mode == "both"
            previous_assistant_message = last_agent_message_by_thread.get(current_thread, "")
            if should_redact:
                redacted_block = redact_message_block(
                    block_lines,
                    engine,
                    vault_by_thread[current_thread],
                    stats,
                    previous_assistant_message=previous_assistant_message,
                    non_name_allowlist=non_name_allowlist,
                )
                out_lines.extend(redacted_block)
            else:
                out_lines.extend(block_lines)

            if role == "Agent":
                last_agent_message_by_thread[current_thread] = "".join(block_lines).strip()
            continue

        out_lines.append(raw)
        i += 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("".join(out_lines), encoding="utf-8")
    return stats



def main() -> None:
    args = parse_args()
    input_path = args.input_file
    output_path = derive_output_path(input_path, args.output, args.engine_tag.strip())
    non_name_allowlist = set(load_non_name_terms_from_json(args.community_tree))
    non_name_allowlist.update(load_floor_plan_name_terms(args.floor_plans))
    merged_allowlist = sorted(non_name_allowlist)

    stats = redact_transcript(input_path, output_path, args.mode, non_name_allowlist=merged_allowlist)

    print(f"Input: {input_path}")
    print(f"Output: {output_path}")
    print(f"Mode: {args.mode}")
    if args.engine_tag:
        print(f"Engine tag: {args.engine_tag}")
    if args.community_tree:
        print(f"Community tree: {args.community_tree}")
    if args.floor_plans:
        print(f"Floor plans: {args.floor_plans}")
    print(f"Non-name terms: {len(merged_allowlist)}")
    print(f"Threads: {stats.threads}")
    print(f"User blocks: {stats.user_blocks}")
    print(f"Agent blocks: {stats.agent_blocks}")
    print(f"Processed message lines: {stats.processed_lines}")
    print(f"Token replacements: {stats.replaced_tokens}")


if __name__ == "__main__":
    main()
