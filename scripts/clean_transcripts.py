from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.redact_transcript_fixture import (  # noqa: E402
    Stats,
    load_floor_plan_name_terms,
    load_non_name_terms_from_json,
    redact_transcript,
)


@dataclass(frozen=True)
class CleanedTranscriptResult:
    input_file: Path
    output_file: Path
    stats: Stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Clean transcript files by redacting user-provided PII in both user "
            "and assistant messages."
        )
    )
    parser.add_argument("input_files", nargs="+", type=Path, help="Transcript file(s) to clean")
    parser.add_argument(
        "--output-suffix",
        default="_cleaned",
        help="Suffix inserted before the file extension. Default: _cleaned",
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


def derive_cleaned_output_path(input_path: Path, output_suffix: str = "_cleaned") -> Path:
    return input_path.with_name(f"{input_path.stem}{output_suffix}{input_path.suffix}")


def build_non_name_allowlist(
    *,
    community_tree: Path | None = None,
    floor_plans: Path | None = None,
) -> list[str]:
    non_name_allowlist = set(load_non_name_terms_from_json(community_tree))
    non_name_allowlist.update(load_floor_plan_name_terms(floor_plans))
    return sorted(non_name_allowlist)


def clean_transcript_file(
    input_path: Path,
    *,
    output_suffix: str = "_cleaned",
    community_tree: Path | None = None,
    floor_plans: Path | None = None,
) -> CleanedTranscriptResult:
    output_path = derive_cleaned_output_path(input_path, output_suffix)
    non_name_allowlist = build_non_name_allowlist(
        community_tree=community_tree,
        floor_plans=floor_plans,
    )
    stats = redact_transcript(
        input_path,
        output_path,
        "user-sourced-both",
        non_name_allowlist=non_name_allowlist,
    )
    return CleanedTranscriptResult(
        input_file=input_path,
        output_file=output_path,
        stats=stats,
    )


def clean_transcript_files(
    input_files: list[Path],
    *,
    output_suffix: str = "_cleaned",
    community_tree: Path | None = None,
    floor_plans: Path | None = None,
) -> list[CleanedTranscriptResult]:
    non_name_allowlist = build_non_name_allowlist(
        community_tree=community_tree,
        floor_plans=floor_plans,
    )
    results: list[CleanedTranscriptResult] = []
    for input_path in input_files:
        output_path = derive_cleaned_output_path(input_path, output_suffix)
        stats = redact_transcript(
            input_path,
            output_path,
            "user-sourced-both",
            non_name_allowlist=non_name_allowlist,
        )
        results.append(
            CleanedTranscriptResult(
                input_file=input_path,
                output_file=output_path,
                stats=stats,
            )
        )
    return results


def main() -> None:
    args = parse_args()
    print(f"clean_transcripts_start files={len(args.input_files)}")
    failures = 0

    for input_path in args.input_files:
        output_path = derive_cleaned_output_path(input_path, args.output_suffix)
        print(f"clean_transcript_start input={input_path} output={output_path}")
        try:
            result = clean_transcript_file(
                input_path,
                output_suffix=args.output_suffix,
                community_tree=args.community_tree,
                floor_plans=args.floor_plans,
            )
        except Exception as exc:
            failures += 1
            print(f"clean_transcript_failure input={input_path} error={exc}")
            continue

        stats = result.stats
        print(
            "clean_transcript_success "
            f"input={result.input_file} "
            f"output={result.output_file} "
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
