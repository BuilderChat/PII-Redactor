from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from scripts.clean_transcripts import clean_transcript_file, derive_cleaned_output_path


def test_clean_transcript_file_redacts_user_pii_on_both_sides(tmp_path: Path) -> None:
    input_path = tmp_path / "client_chat.txt"
    input_path.write_text(
        "\n".join(
            [
                "========================================================================================",
                "Started: 2026-05-01 10:00:00 CDT | Thread: thread_demo",
                "========================================================================================",
                "Conversation Transcript (Thread: thread_demo) - 2026-05-01 10:00:00 CDT",
                "========================================",
                "",
                "User (2026-05-01 10:00:00 CDT): ",
                "My name is Alice Jones and my email is alice@example.com.",
                "",
                "Agent (2026-05-01 10:00:05 CDT): ",
                "Thanks Alice. I will email alice@example.com. Call our office at 800-555-0000.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = clean_transcript_file(input_path)
    cleaned_text = result.output_file.read_text(encoding="utf-8")

    assert result.output_file == derive_cleaned_output_path(input_path)
    assert "My name is <fn_1> <ln_1> and my email is <em_1>." in cleaned_text
    assert "Thanks <fn_1>. I will email <em_1>. Call our office at 800-555-0000." in cleaned_text
    assert "<ph_1>" not in cleaned_text


def test_clean_transcript_file_supports_assistant_role_label(tmp_path: Path) -> None:
    input_path = tmp_path / "assistant_label.txt"
    input_path.write_text(
        "\n".join(
            [
                "========================================================================================",
                "Started: 2026-05-01 11:00:00 CDT | Thread: thread_assistant",
                "========================================================================================",
                "Conversation Transcript (Thread: thread_assistant) - 2026-05-01 11:00:00 CDT",
                "========================================",
                "",
                "User (2026-05-01 11:00:00 CDT): ",
                "You can text me at 555-123-4567.",
                "",
                "Assistant (2026-05-01 11:00:05 CDT): ",
                "Perfect, I have 5551234567 saved for the follow-up.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = clean_transcript_file(input_path)
    cleaned_text = result.output_file.read_text(encoding="utf-8")

    assert result.output_file.name == "assistant_label_cleaned.txt"
    assert "You can text me at <ph_1>." in cleaned_text
    assert "Perfect, I have <ph_1> saved for the follow-up." in cleaned_text


def test_clean_transcripts_script_supports_direct_execution() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/clean_transcripts.py", "--help"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Clean transcript files" in result.stdout
