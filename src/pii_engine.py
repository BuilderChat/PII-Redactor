from __future__ import annotations

import re
from dataclasses import dataclass

from .config import NAME_ENTITY_KEYS
from .pii_vault import PIIVault


EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PHONE_RE = re.compile(
    r"(?<!\w)(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]?\d{4}(?!\w)"
)
NAME_INTRO_RE = re.compile(
    r"\b(?:my\s+name\s+is|i\s+am|i'm|this\s+is)\s+"
    r"([A-Za-z][A-Za-z'\-]*(?:\s+[A-Za-z][A-Za-z'\-]*){0,4})",
    re.IGNORECASE,
)
NAME_REPLY_RE = re.compile(r"^\s*[A-Z][A-Za-z'\-]*(?:\s+[A-Z][A-Za-z'\-]*){0,4}[.!?]?\s*$")
TOKEN_RE = re.compile(r"<([^<>]+)>")
NAME_NOISE_WORDS = {
    "and",
    "or",
    "but",
    "my",
    "email",
    "mail",
    "phone",
    "number",
    "contact",
    "is",
    "at",
}

ALIAS_TO_ENTITY = {
    "first": "fn",
    "first_name": "fn",
    "firstname": "fn",
    "fn": "fn",
    "middle_name_1": "mn1",
    "middle1": "mn1",
    "mn1": "mn1",
    "middle_name_2": "mn2",
    "middle2": "mn2",
    "mn2": "mn2",
    "last": "ln",
    "last_name": "ln",
    "lastname": "ln",
    "ln": "ln",
    "email": "em",
    "e-mail": "em",
    "em": "em",
    "phone": "ph",
    "phone_number": "ph",
    "mobile": "ph",
    "ph": "ph",
}
NAME_ALIASES = {"name", "full_name", "person", "customer_name"}


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    entity_key: str
    value: str


@dataclass(frozen=True)
class RedactionResult:
    redacted_text: str
    replacements: dict[str, str]
    active_profile: int


@dataclass(frozen=True)
class RehydrationResult:
    clean_text: str
    repaired_text: str
    repaired_placeholders: bool


class PIIEngine:
    """PII detection/redaction with deterministic placeholder tokens.

    Current bootstrap implementation uses:
    - Regex for email/phone
    - Heuristic detection for names

    Presidio + GLiNER integration can be plugged in behind `_detect_name_spans`
    without changing the public API.
    """

    def redact(self, text: str, vault: PIIVault) -> RedactionResult:
        spans = self._collect_spans(text)
        if not spans:
            return RedactionResult(
                redacted_text=text,
                replacements={},
                active_profile=vault.current_profile,
            )

        ordered_spans = self._non_overlapping_spans(spans, len(text))
        chunks: list[str] = []
        replacements: dict[str, str] = {}
        cursor = 0

        for span in ordered_spans:
            chunks.append(text[cursor : span.start])
            replacement, found_values = self._placeholder_for_span(span, vault)
            chunks.append(replacement)
            replacements.update(found_values)
            cursor = span.end

        chunks.append(text[cursor:])

        return RedactionResult(
            redacted_text="".join(chunks),
            replacements=replacements,
            active_profile=vault.current_profile,
        )

    def rehydrate(self, text: str, vault: PIIVault) -> RehydrationResult:
        repaired_text = self.repair_placeholders(text, vault)
        clean_text = repaired_text

        for token, value in sorted(vault.items().items(), key=lambda pair: len(pair[0]), reverse=True):
            clean_text = clean_text.replace(token, value)

        return RehydrationResult(
            clean_text=clean_text,
            repaired_text=repaired_text,
            repaired_placeholders=(repaired_text != text),
        )

    def repair_placeholders(self, text: str, vault: PIIVault) -> str:
        def _replace(match: re.Match[str]) -> str:
            raw_content = match.group(1).strip().lower()
            original_token = f"<{match.group(1)}>"

            if vault.has_token(original_token):
                return original_token

            compact_match = re.fullmatch(r"(fn|mn1|mn2|ln|em|ph)(\d+)", raw_content)
            if compact_match:
                candidate = f"<{compact_match.group(1)}_{compact_match.group(2)}>"
                if vault.has_token(candidate):
                    return candidate

            indexed_match = re.fullmatch(r"([a-z_\-]+)_?(\d+)", raw_content)
            if indexed_match:
                entity = ALIAS_TO_ENTITY.get(indexed_match.group(1))
                if entity:
                    candidate = f"<{entity}_{indexed_match.group(2)}>"
                    if vault.has_token(candidate):
                        return candidate

            if raw_content in NAME_ALIASES:
                fn_token = vault.latest_token_for_entity("fn")
                ln_token = vault.latest_token_for_entity("ln")
                if fn_token and ln_token:
                    return f"{fn_token} {ln_token}"
                if fn_token:
                    return fn_token
                return original_token

            entity = ALIAS_TO_ENTITY.get(raw_content)
            if entity:
                latest = vault.latest_token_for_entity(entity)
                if latest:
                    return latest

            return original_token

        return TOKEN_RE.sub(_replace, text)

    def _collect_spans(self, text: str) -> list[Span]:
        spans: list[Span] = []

        for match in EMAIL_RE.finditer(text):
            spans.append(Span(match.start(), match.end(), "em", match.group(0)))

        for match in PHONE_RE.finditer(text):
            spans.append(Span(match.start(), match.end(), "ph", match.group(0)))

        spans.extend(self._detect_name_spans(text))
        return spans

    def _detect_name_spans(self, text: str) -> list[Span]:
        spans: list[Span] = []

        for match in NAME_INTRO_RE.finditer(text):
            value, keep_chars = self._trim_trailing_name_noise(match.group(1))
            if self._looks_like_name(value):
                spans.append(Span(match.start(1), match.start(1) + keep_chars, "name", value))

        if not spans and NAME_REPLY_RE.match(text):
            clean = text.strip().rstrip(".!?")
            if self._looks_like_name(clean):
                start = text.find(clean)
                end = start + len(clean)
                spans.append(Span(start, end, "name", clean))

        return spans

    @staticmethod
    def _looks_like_name(value: str) -> bool:
        words = re.findall(r"[A-Za-z][A-Za-z'\-]*", value)
        if not words:
            return False
        if len(words) > 5:
            return False
        return all(len(w) > 1 for w in words)

    @staticmethod
    def _trim_trailing_name_noise(value: str) -> tuple[str, int]:
        token_matches = list(re.finditer(r"[A-Za-z][A-Za-z'\-]*", value))
        if not token_matches:
            return "", 0

        keep_index = len(token_matches) - 1
        while keep_index >= 0 and token_matches[keep_index].group(0).lower() in NAME_NOISE_WORDS:
            keep_index -= 1
        if keep_index < 0:
            return "", 0

        keep_end = token_matches[keep_index].end()
        return value[:keep_end].strip(), keep_end

    def _placeholder_for_span(self, span: Span, vault: PIIVault) -> tuple[str, dict[str, str]]:
        if span.entity_key == "name":
            name_parts = self._split_name_parts(span.value)
            if not name_parts:
                return span.value, {}

            ordered_tokens: list[str] = []
            replacements: dict[str, str] = {}

            for entity in NAME_ENTITY_KEYS:
                value = name_parts.get(entity)
                if value:
                    token = vault.register(entity, value)
                    ordered_tokens.append(token)
                    replacements[token] = value

            return " ".join(ordered_tokens), replacements

        token = vault.register(span.entity_key, span.value)
        return token, {token: span.value}

    @staticmethod
    def _split_name_parts(value: str) -> dict[str, str]:
        value = value.strip()
        value = re.sub(r"^(mr|mrs|ms|dr|prof)\.?\s+", "", value, flags=re.IGNORECASE)
        words = re.findall(r"[A-Za-z][A-Za-z'\-]*", value)

        if not words:
            return {}
        if len(words) == 1:
            return {"fn": words[0]}
        if len(words) == 2:
            return {"fn": words[0], "ln": words[1]}
        if len(words) == 3:
            return {"fn": words[0], "mn1": words[1], "ln": words[2]}

        # Four or more words: capture up to two middle names and merge the rest into last name.
        return {
            "fn": words[0],
            "mn1": words[1],
            "mn2": words[2],
            "ln": " ".join(words[3:]),
        }

    @staticmethod
    def _non_overlapping_spans(spans: list[Span], text_length: int) -> list[Span]:
        occupied = [False] * text_length
        selected: list[Span] = []

        for span in sorted(spans, key=lambda s: (s.start, -(s.end - s.start))):
            if any(occupied[i] for i in range(span.start, span.end)):
                continue
            selected.append(span)
            for i in range(span.start, span.end):
                occupied[i] = True

        return sorted(selected, key=lambda s: s.start)
