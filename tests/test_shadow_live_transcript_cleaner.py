from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from scripts.clean_shadow_live_transcripts import (
    clean_shadow_live_transcript_file,
    derive_live_cleaned_output_path,
)


def test_clean_shadow_live_transcript_file_omits_shadow_sections(tmp_path: Path) -> None:
    input_path = tmp_path / "pii_shadow_export.txt"
    input_path.write_text(
        "\n".join(
            [
                "========================================================================================",
                "PII Shadow Export | Client: 1001 | Assistant: 1001-chat-001 | Thread: thread_demo",
                "========================================================================================",
                "",
                "[LIVE TRANSCRIPT]",
                "Conversation Transcript (Thread: thread_demo) - 2026-06-23 10:00:00 CDT",
                "========================================",
                "",
                "User (2026-06-23 10:00:00 CDT): ",
                "My name is Alice Jones and my email is alice@example.com.",
                "[Intent: handle_community_info]",
                "[Area: None / None / None]",
                "",
                "Agent (2026-06-23 10:00:05 CDT): ",
                "Thanks Alice. I will email alice@example.com. Call our office at 800-555-0000.",
                "",
                "[SHADOW TRANSCRIPT]",
                "PII Shadow Transcript (Thread: thread_demo) - 2026-06-23 10:00:10 CDT",
                "========================================",
                "Redacted User Message:",
                "My name is <fn_1> <ln_1> and my email is <em_1>.",
                "Shadow Reply Visible:",
                "This shadow-only text should not appear.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = clean_shadow_live_transcript_file(input_path)
    cleaned_text = result.output_file.read_text(encoding="utf-8")

    assert result.output_file == derive_live_cleaned_output_path(input_path)
    assert "PII Shadow Export | Client: 1001 | Assistant: 1001-chat-001 | Thread: thread_demo" in cleaned_text
    assert "[LIVE TRANSCRIPT]" not in cleaned_text
    assert "[SHADOW TRANSCRIPT]" not in cleaned_text
    assert "Shadow Reply Visible" not in cleaned_text
    assert "This shadow-only text should not appear." not in cleaned_text
    assert "My name is <fn_1> <ln_1> and my email is <em_1>." in cleaned_text
    assert "Thanks <fn_1>. I will email <em_1>. Call our office at 800-555-0000." in cleaned_text


def test_clean_shadow_live_transcripts_script_supports_direct_execution() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/clean_shadow_live_transcripts.py", "--help"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Clean live transcript sections" in result.stdout
