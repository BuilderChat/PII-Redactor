from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.clean_transcripts import build_non_name_allowlist  # noqa: E402
from scripts.redact_transcript_fixture import (  # noqa: E402
    Stats,
    extract_live_transcript_lines,
    redact_transcript_lines,
)
from src.pii_engine import PIIEngine  # noqa: E402


@dataclass(frozen=True)
class CleanedShadowLiveResult:
    input_file: Path
    output_file: Path
    stats: Stats
    extracted_lines: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Clean live transcript sections from PII shadow export files. "
            "The [SHADOW TRANSCRIPT] sections are intentionally omitted."
        )
    )
    parser.add_argument("input_files", nargs="+", type=Path, help="Shadow export transcript file(s) to clean")
    parser.add_argument(
        "--output-suffix",
        default="_live_cleaned",
        help="Suffix inserted before the file extension. Default: _live_cleaned",
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


def derive_live_cleaned_output_path(input_path: Path, output_suffix: str = "_live_cleaned") -> Path:
    return input_path.with_name(f"{input_path.stem}{output_suffix}{input_path.suffix}")


def clean_shadow_live_transcript_file(
    input_path: Path,
    *,
    output_suffix: str = "_live_cleaned",
    community_tree: Path | None = None,
    floor_plans: Path | None = None,
    non_name_allowlist: list[str] | None = None,
    engine: PIIEngine | None = None,
) -> CleanedShadowLiveResult:
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    source_lines = input_path.read_text(encoding="utf-8").splitlines(keepends=True)
    live_lines = extract_live_transcript_lines(source_lines)
    output_path = derive_live_cleaned_output_path(input_path, output_suffix)
    if non_name_allowlist is None:
        non_name_allowlist = build_non_name_allowlist(
            community_tree=community_tree,
            floor_plans=floor_plans,
        )
    stats = redact_transcript_lines(
        live_lines,
        output_path,
        "user-sourced-both",
        non_name_allowlist=non_name_allowlist,
        engine=engine,
    )
    return CleanedShadowLiveResult(
        input_file=input_path,
        output_file=output_path,
        stats=stats,
        extracted_lines=len(live_lines),
    )


def main() -> None:
    args = parse_args()
    print(f"clean_shadow_live_transcripts_start files={len(args.input_files)}")
    failures = 0
    non_name_allowlist = build_non_name_allowlist(
        community_tree=args.community_tree,
        floor_plans=args.floor_plans,
    )
    engine = PIIEngine()

    for input_path in args.input_files:
        if input_path.stem.endswith(args.output_suffix):
            print(f"clean_shadow_live_transcript_skip_existing_output input={input_path}")
            continue

        output_path = derive_live_cleaned_output_path(input_path, args.output_suffix)
        print(f"clean_shadow_live_transcript_start input={input_path} output={output_path}")
        try:
            result = clean_shadow_live_transcript_file(
                input_path,
                output_suffix=args.output_suffix,
                non_name_allowlist=non_name_allowlist,
                engine=engine,
            )
        except Exception as exc:
            failures += 1
            print(f"clean_shadow_live_transcript_failure input={input_path} error={exc}")
            continue

        stats = result.stats
        print(
            "clean_shadow_live_transcript_success "
            f"input={result.input_file} "
            f"output={result.output_file} "
            f"extracted_lines={result.extracted_lines} "
            f"threads={stats.threads} "
            f"user_blocks={stats.user_blocks} "
            f"assistant_blocks={stats.agent_blocks} "
            f"processed_lines={stats.processed_lines} "
            f"replacements={stats.replaced_tokens}"
        )

    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
