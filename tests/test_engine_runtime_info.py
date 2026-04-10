from src.pii_engine import PIIEngine
from src.pii_vault import PIIVault


def test_runtime_info_reports_heuristic_mode_when_detectors_disabled() -> None:
    engine = PIIEngine(use_presidio=False, use_gliner=False)
    info = engine.runtime_info

    assert info["presidio_enabled"] is False
    assert info["gliner_enabled"] is False
    assert info["name_detection_mode"] == "heuristic"


def test_heuristic_mode_still_redacts_expected_pii() -> None:
    engine = PIIEngine(use_presidio=False, use_gliner=False)
    vault = PIIVault()

    redacted = engine.redact("My name is Alice Smith and my email is alice@example.com", vault)
    assert "<fn_1> <ln_1>" in redacted.redacted_text
    assert "<em_1>" in redacted.redacted_text
