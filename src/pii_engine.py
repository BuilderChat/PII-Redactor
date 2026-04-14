from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .config import ENTITY_KEYS, NAME_ENTITY_KEYS, get_settings
from .pii_vault import PIIVault


LOGGER = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PHONE_RE = re.compile(
    r"(?<!\w)(?:\+?\d{1,3}[\s.\-+]*)?(?:\(?\d{3}\)?[\s.\-+]*)\d{3}[\s.\-+]*\d{4}(?!\w)"
)
NAME_WORD_PATTERN = r"[^\W\d_]+(?:['\-][^\W\d_]+)*"
NAME_INTRO_RE = re.compile(
    r"\b(?P<cue>my\s+n(?:ame|ae|me)\s+is|i\s+am|i'm|this\s+is)\s+"
    r"(?P<candidate>[A-Za-z][A-Za-z'\-]*(?:\s+[A-Za-z][A-Za-z'\-]*){0,4})",
    re.IGNORECASE,
)
NAME_REPLY_RE = re.compile(r"^\s*[A-Z][A-Za-z'\-]*(?:\s+[A-Z][A-Za-z'\-]*){0,4}[.!?]?\s*$")
COORDINATED_NAME_RE = re.compile(
    r"\b([A-Z][A-Za-z'\-]*)\s+(?:and|&)\s+([A-Za-z][A-Za-z'\-]*)\s+([A-Z][A-Za-z'\-]*)\b"
)
COORDINATED_FULL_NAMES_RE = re.compile(
    r"\b(?P<first1>[A-Z][A-Za-z'\-]*)\s+(?P<last1>[A-Z][A-Za-z'\-]*)\s+"
    r"(?:and|&)\s+"
    r"(?P<first2>[A-Z][A-Za-z'\-]*)\s+(?P<last2>[A-Z][A-Za-z'\-]*)\b"
)
THIS_IS_NAME_WITH_CONTEXT_RE = re.compile(
    r"\bthis\s+is\s+(?P<first>[A-Za-z][A-Za-z'\-]*)\s+"
    r"(?P<last>[A-Za-z][A-Za-z'\-]*)\s+(?:with|from|at)\b",
    re.IGNORECASE,
)
TOKEN_RE = re.compile(r"<([^<>]+)>")
VAULT_TOKEN_RE = re.compile(r"^<(?P<entity>[a-z0-9]+)_\d+>$")
NAME_LABELLED_REPLY_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z][A-Za-z'\-]*)\s+"
    r"(?P<label>last\s+name|surname|family\s+name|first\s+name)\b",
    re.IGNORECASE,
)
NAME_LABELLED_REPLY_ALT_RE = re.compile(
    r"^\s*(?P<label>last\s+name|surname|family\s+name|first\s+name)\s*[:\-]\s*"
    r"(?P<name>[A-Za-z][A-Za-z'\-]*)\b",
    re.IGNORECASE,
)
LAST_NAME_LABELLED_REPLY_SPACE_RE = re.compile(
    r"^\s*(?P<label>last\s+name|surname|family\s+name)\s+(?P<name>[A-Za-z][A-Za-z'\-]*)\b",
    re.IGNORECASE,
)
INLINE_LAST_NAME_IS_RE = re.compile(
    rf"\b(?:last\s+name|surname|family\s+name)\s+is\s+(?P<name>{NAME_WORD_PATTERN})\b",
    re.IGNORECASE | re.UNICODE,
)
KEYED_NAME_VALUE_RE = re.compile(
    r"\b(?P<label>(?:your\s+)?name(?:\s*\(\s*first\s*(?:and|&)\s*last\s*\))?|names|first\s+name|last\s+name)\s*[:\-]\s*"
    r"(?P<value>[A-Za-z][A-Za-z'\-]*(?:\s+[A-Za-z][A-Za-z'\-]*)?)",
    re.IGNORECASE,
)
REALTOR_PAIR_INTRO_RE = re.compile(
    rf"^\s*(?P<f1>{NAME_WORD_PATTERN})\s+(?P<l1>{NAME_WORD_PATTERN})\s*,\s*"
    rf"(?P<f2>{NAME_WORD_PATTERN})\s+(?P<l2>{NAME_WORD_PATTERN})\s*,\s*"
    r"(?P<cue>realtor(?:s)?|agent(?:s)?|broker(?:s)?)\b",
    re.IGNORECASE | re.UNICODE,
)
SIGNATURE_TAIL_NAME_RE = re.compile(
    rf"[?.!]\s+(?P<first>{NAME_WORD_PATTERN})\s+(?P<last>{NAME_WORD_PATTERN})"
    r"(?P<tail>(?:\s+[A-Za-z0-9][A-Za-z0-9'\-]*){1,6})\s*$",
    re.IGNORECASE | re.UNICODE,
)
SIGNATURE_BROKERAGE_CUES = {
    "realtor",
    "realtors",
    "realty",
    "broker",
    "brokers",
    "brokerage",
    "c21",
    "century",
    "keller",
    "williams",
    "kw",
    "judge",
    "fite",
}
ASSISTANT_NAME_GREETINGS = {"hello", "hi", "hey"}
DEFAULT_ASSISTANT_NAME_WORDS = {"mia", "ava", "aurora", "blossom"}
HARDCODED_NON_NAME_PHRASES = {
    "blossom ai",
    "move in ready",
}
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
    "with",
    "from",
    "via",
}
NAME_CONTEXT_CUES = (
    "my name is",
    "my nae is",
    "my nme is",
    "name is",
    "first name",
    "last name",
    "i am",
    "i'm",
    "this is",
    "call me",
)
ASSISTANT_NAME_REQUEST_CUES = (
    "what is your name",
    "what's your name",
    "your first name",
    "first name",
    "your last name",
    "last name",
    "full name",
    "may i have your name",
    "can i have your name",
    "share your name",
    "grab your name",
    "get your name",
    "have your name",
    "name please",
    "who am i speaking with",
    "what should i call you",
)
ASSISTANT_FIRST_NAME_REQUEST_CUES = (
    "first name",
    "your first name",
    "what is your first name",
    "what's your first name",
    "first and last name",
    "first & last name",
)
ASSISTANT_LAST_NAME_REQUEST_CUES = (
    "last name",
    "your last name",
    "what is your last name",
    "what's your last name",
    "surname",
    "family name",
)
ASSISTANT_FULL_NAME_REQUEST_CUES = (
    "full name",
    "your full name",
    "what is your full name",
    "what's your full name",
    "first and last name",
    "first & last name",
)
ASSISTANT_CONTACT_REQUEST_CUES = (
    "your contact info",
    "your contact information",
    "grab your contact info",
    "can i grab your contact info",
    "can i get your contact info",
    "share your contact info",
    "what can i use to reach you",
    "what should i use to reach you",
    "best way to reach you",
    "email or phone",
    "email or phone number",
    "name and phone",
    "name and best contact",
    "name and best contact info",
    "what's your name and best contact info",
    "what is your name and best contact info",
)
ASSISTANT_FIRST_ALREADY_CAPTURED_CUES = (
    "have your first name",
    "got your first name",
    "i have your first name",
)
ASSISTANT_PLAN_CONTEXT_CUES = (
    "floor plan",
    "floor plans",
    "plan interests",
    "what floor plan",
    "which floor plan",
    "specific plan",
    "specific plans",
    "compare plans",
    "compare specific plans",
    "explore other plans",
    "available plans",
)
ASSISTANT_LOCATION_CONTEXT_CUES = (
    "which community",
    "what community",
    "which area",
    "what area",
    "which city",
    "what city",
    "location",
    "communities",
    "community",
    "relocating",
    "nearby",
    "where are you looking",
    "show you what we're building nearby",
)
FLOOR_PLAN_CODE_TOKENS = {
    "fe",
    "se",
    "ce",
    "i",
    "ii",
    "iii",
    "iv",
    "v",
    "vi",
    "vii",
    "viii",
    "ix",
    "x",
    "xi",
    "xii",
    "io",
}
COORDINATED_NAME_CONTEXT_CUES = (
    "client",
    "clients",
    "buyer",
    "buyers",
    "customer",
    "customers",
    "spouse",
    "spouses",
    "partner",
    "partners",
)
NON_USER_CONTACT_LOOKUP_PREFIXES = (
    "email address for ",
    "e-mail address for ",
    "email for ",
    "phone number for ",
    "contact info for ",
    "contact information for ",
    "contact for ",
)
NAME_PREFIX_EXCLUSIONS = {
    "in",
    "on",
    "at",
    "from",
    "to",
    "for",
    "with",
    "by",
    "of",
    "the",
    "a",
    "an",
    "near",
    "around",
    "close",
    "outside",
    "under",
}
NON_NAME_SINGLE_WORDS = {
    "yes",
    "no",
    "we",
    "you",
    "i",
    "me",
    "my",
    "our",
    "ours",
    "us",
    "your",
    "yours",
    "their",
    "them",
    "they",
    "it",
    "its",
    "this",
    "that",
    "these",
    "those",
    "hello",
    "hi",
    "hey",
    "thanks",
    "thank",
    "and",
    "ok",
    "okay",
    "sure",
    "please",
    "either",
    "good",
    "list",
    "realtor",
    "agent",
    "office",
    "sale",
    "sales",
    "incentive",
    "incentives",
    "warranty",
    "prices",
    "pricing",
    "tour",
    "homes",
    "home",
    "email",
    "e-mail",
    "phone",
    "text",
    "call",
    "near",
    "around",
    "close",
    "outside",
    "under",
    "for",
    "already",
    "local",
    "ready",
    "talking",
    "working",
    "service",
    "property",
    "taxes",
    "homeowners",
    "insurance",
    "insureance",
    "condo",
    "condos",
    "townhome",
    "townhomes",
    "house",
    "houses",
    "here",
    "is",
    "can",
    "why",
    "if",
    "just",
    "great",
    "va",
    "veteran",
    "vet",
    "military",
    "senior",
    "teacher",
    "doctor",
    "nurse",
}
NON_NAME_MULTIWORD_COMPONENTS = {
    "agent",
    "realtor",
    "office",
    "homes",
    "home",
    "hills",
    "heights",
    "creek",
    "city",
    "village",
    "community",
    "communities",
    "street",
    "terrace",
    "st",
    "road",
    "rd",
    "avenue",
    "ave",
    "drive",
    "dr",
    "way",
    "park",
    "lot",
    "plan",
    "sale",
    "sales",
    "pricing",
    "tours",
    "tour",
    "availability",
    "hoa",
    "property",
    "tax",
    "taxes",
    "homeowner",
    "homeowners",
    "insurance",
    "insureance",
    "elevation",
    "bedroom",
    "bathroom",
    "county",
}
NON_NAME_PHRASE_HINTS = {
    "local",
    "provide",
    "provides",
    "providing",
    "service",
    "services",
    "work",
    "works",
    "working",
    "offer",
    "offering",
    "complimentary",
    "cleaning",
    "community",
    "communities",
    "bid",
    "talking",
    "already",
    "local",
    "property",
    "taxes",
    "homeowners",
    "insurance",
    "insureance",
}
WEAK_NAME_INTRO_CUES = {"i am", "i'm", "this is"}
WEAK_NAME_INTRO_NON_NAME_STARTS = {
    "a",
    "an",
    "the",
    "just",
    "already",
    "currently",
    "still",
    "talking",
    "working",
    "looking",
    "searching",
    "interested",
    "local",
    "here",
    "calling",
    "texting",
    "emailing",
    "wondering",
    "checking",
    "trying",
    "doing",
    "limiting",
    "available",
    "asking",
    "following",
    "for",
    "with",
    "about",
    "told",
    "not",
    "sure",
}
GEO_DIRECTION_WORDS = {
    "north",
    "northern",
    "south",
    "southern",
    "east",
    "eastern",
    "west",
    "western",
    "northeast",
    "northwest",
    "southeast",
    "southwest",
}
LOCATION_CUE_PHRASES = (
    "near",
    "around",
    "close to",
    "just outside",
    "outside",
    "north of",
    "south of",
    "east of",
    "west of",
    "northeast of",
    "northwest of",
    "southeast of",
    "southwest of",
)
_US_STATE_ABBRS = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC",
}
_CA_PROV_ABBRS = {
    "BC", "AB", "SK", "MN", "MB", "ON", "QC", "NL", "NB", "NS", "PE",
    "PEI", "YK", "NU", "NT", "NWT"
}
GEO_REGION_ABBREVIATIONS = {abbr.lower() for abbr in _US_STATE_ABBRS | _CA_PROV_ABBRS}
GEO_REGION_PHRASES = {
    "alabama",
    "alaska",
    "alberta",
    "arizona",
    "arkansas",
    "british columbia",
    "california",
    "colorado",
    "connecticut",
    "delaware",
    "district of columbia",
    "florida",
    "georgia",
    "hawaii",
    "idaho",
    "illinois",
    "indiana",
    "iowa",
    "kansas",
    "kentucky",
    "louisiana",
    "maine",
    "manitoba",
    "maryland",
    "massachusetts",
    "michigan",
    "minnesota",
    "mississippi",
    "missouri",
    "montana",
    "nebraska",
    "nevada",
    "new brunswick",
    "new hampshire",
    "new jersey",
    "jersey",
    "new mexico",
    "new york",
    "newfoundland",
    "newfoundland and labrador",
    "north carolina",
    "the carolinas",
    "north dakota",
    "northwest territories",
    "nova scotia",
    "nunavut",
    "ohio",
    "oklahoma",
    "ontario",
    "oregon",
    "pennsylvania",
    "penn state",
    "prince edward island",
    "quebec",
    "rhode island",
    "saskatchewan",
    "south carolina",
    "south dakota",
    "the dakotas",
    "tennessee",
    "texas",
    "utah",
    "vermont",
    "virginia",
    "washington",
    "west virginia",
    "wisconsin",
    "wyoming",
    "yukon",
}
BUSINESS_NAME_KEYWORDS = {
    "company",
    "companies",
    "corporation",
    "corporations",
    "corporate",
    "corp",
    "co",
    "inc",
    "incorporated",
    "llc",
    "limited",
    "ltd",
    "plc",
    "group",
    "team",
    "crew",
    "services",
    "service",
    "janitorial",
    "sanitation",
    "bins",
    "cleaning",
    "framing",
    "siding",
    "roofing",
    "contracting",
    "contractors",
    "mechanical",
    "plumbing",
    "heating",
    "cooling",
    "appliances",
    "landscaping",
    "construction",
    "builders",
    "homes",
    "profile",
    "profiles",
}
PRESIDIO_ENTITY_MAP = {
    "EMAIL_ADDRESS": "em",
    "PHONE_NUMBER": "ph",
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
    prefer_latest: bool = False


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

    Detection order:
    1) Presidio for regex-style entities (email/phone) when available.
    2) GLiNER for names when available.
    3) Built-in regex + heuristics as resilient fallback.
    """

    def __init__(
        self,
        use_presidio: bool | None = None,
        use_gliner: bool | None = None,
        gliner_model: str | None = None,
        gliner_threshold: float | None = None,
        gliner_labels: tuple[str, ...] | None = None,
    ) -> None:
        settings = get_settings()

        self._use_presidio = settings.use_presidio if use_presidio is None else use_presidio
        self._presidio_minimal_recognizers = settings.presidio_minimal_recognizers
        self._use_gliner = settings.use_gliner if use_gliner is None else use_gliner
        self._gliner_allow_remote_download = settings.gliner_allow_remote_download
        self._gliner_model_name = settings.gliner_model if gliner_model is None else gliner_model
        self._gliner_threshold = settings.gliner_threshold if gliner_threshold is None else gliner_threshold
        self._gliner_labels = settings.gliner_labels if gliner_labels is None else gliner_labels
        self._configured_non_name_terms = self._load_non_name_terms(
            csv_terms=settings.non_name_terms,
            json_path=settings.non_name_terms_json_path,
        )

        self._presidio_analyzer: Any | None = None
        self._gliner_model_handle: Any | None = None
        self._presidio_load_error: str | None = None
        self._gliner_load_error: str | None = None

        self._init_external_detectors()

    @property
    def runtime_info(self) -> dict[str, Any]:
        return {
            "presidio_enabled": self._presidio_analyzer is not None,
            "gliner_enabled": self._gliner_model_handle is not None,
            "name_detection_mode": "gliner" if self._gliner_model_handle is not None else "heuristic",
            "gliner_model": self._gliner_model_name,
            "gliner_threshold": self._gliner_threshold,
            "gliner_allow_remote_download": self._gliner_allow_remote_download,
            "presidio_load_error": self._presidio_load_error,
            "gliner_load_error": self._gliner_load_error,
        }

    def redact(
        self,
        text: str,
        vault: PIIVault,
        previous_assistant_message: str | None = None,
        non_name_allowlist: list[str] | tuple[str, ...] | None = None,
    ) -> RedactionResult:
        spans = self._collect_spans(
            text,
            vault=vault,
            previous_assistant_message=previous_assistant_message,
            non_name_allowlist=non_name_allowlist,
        )
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

    def _init_external_detectors(self) -> None:
        if self._use_presidio:
            self._init_presidio()
        if self._use_gliner:
            self._init_gliner()

    def _init_presidio(self) -> None:
        try:
            from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
            from presidio_analyzer.nlp_engine import NlpEngineProvider
            from presidio_analyzer.predefined_recognizers import EmailRecognizer, PhoneRecognizer

            configuration = {
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
            }
            provider = NlpEngineProvider(nlp_configuration=configuration)
            nlp_engine = provider.create_engine()

            registry = RecognizerRegistry(supported_languages=["en"])
            if self._presidio_minimal_recognizers:
                # Strict air-gap: register only entities we actually need (email/phone),
                # avoiding URL/tldextract refresh behavior from broader recognizer sets.
                registry.add_recognizer(EmailRecognizer(supported_language="en"))
                registry.add_recognizer(PhoneRecognizer(supported_language="en"))
            else:
                registry.load_predefined_recognizers()

            self._presidio_analyzer = AnalyzerEngine(
                nlp_engine=nlp_engine,
                registry=registry,
                supported_languages=["en"],
            )
        except Exception as exc:  # pragma: no cover - depends on runtime deps
            self._presidio_analyzer = None
            self._presidio_load_error = str(exc)
            LOGGER.info("Presidio unavailable; using regex fallback: %s", exc)

    def _init_gliner(self) -> None:
        try:
            from gliner import GLiNER

            self._gliner_model_handle = GLiNER.from_pretrained(
                self._gliner_model_name,
                local_files_only=not self._gliner_allow_remote_download,
            )
        except Exception as exc:  # pragma: no cover - depends on runtime deps
            self._gliner_model_handle = None
            self._gliner_load_error = str(exc)
            LOGGER.info("GLiNER unavailable; using heuristic name detection: %s", exc)

    def _collect_spans(
        self,
        text: str,
        vault: PIIVault | None = None,
        previous_assistant_message: str | None = None,
        non_name_allowlist: list[str] | tuple[str, ...] | None = None,
    ) -> list[Span]:
        spans: list[Span] = []
        runtime_non_name_terms = set(self._configured_non_name_terms)
        runtime_non_name_terms.update(self._normalize_non_name_terms(non_name_allowlist))
        assistant_name_request_type = self._assistant_name_request_type(previous_assistant_message)
        assistant_requests_name = self._assistant_has_explicit_name_request(previous_assistant_message)
        suppress_name_detection = self._should_suppress_name_detection(
            text=text,
            previous_assistant_message=previous_assistant_message,
            assistant_name_request_type=assistant_name_request_type,
            non_name_terms=runtime_non_name_terms,
        )

        if vault is not None:
            spans.extend(
                self._detect_repeat_value_spans(
                    text,
                    vault,
                    non_name_terms=runtime_non_name_terms,
                    suppress_name_entities=suppress_name_detection,
                )
            )
        spans.extend(self._detect_email_phone_spans_presidio(text))
        spans.extend(self._detect_email_phone_spans_regex(text))

        if not suppress_name_detection:
            spans.extend(self._detect_leading_name_with_contact_spans(text, non_name_terms=runtime_non_name_terms))
            spans.extend(self._detect_contact_then_name_spans(text, non_name_terms=runtime_non_name_terms))
            spans.extend(self._detect_contact_with_parenthetical_name_spans(text, non_name_terms=runtime_non_name_terms))
            spans.extend(self._detect_signature_tail_name_spans(text, non_name_terms=runtime_non_name_terms))
            spans.extend(self._detect_realtor_pair_intro_spans(text, non_name_terms=runtime_non_name_terms))
            spans.extend(self._detect_affirmative_name_contact_spans(text, non_name_terms=runtime_non_name_terms))
            if vault is not None:
                spans.extend(self._detect_name_correction_spans(text, vault, non_name_terms=runtime_non_name_terms))
            spans.extend(
                self._detect_prompted_name_reply_spans(
                    text,
                    vault=vault,
                    request_type=assistant_name_request_type,
                    non_name_terms=runtime_non_name_terms,
                )
            )
            spans.extend(self._detect_keyed_name_value_spans(text, non_name_terms=runtime_non_name_terms))
            spans.extend(self._detect_name_labelled_reply_spans(text, non_name_terms=runtime_non_name_terms))

            name_spans = self._detect_name_spans_gliner(
                text,
                assistant_requests_name=assistant_requests_name,
                non_name_terms=runtime_non_name_terms,
            )
            if name_spans:
                spans.extend(name_spans)
            else:
                spans.extend(
                    self._detect_name_spans_heuristic(
                        text,
                        assistant_requests_name=assistant_requests_name,
                        non_name_terms=runtime_non_name_terms,
                    )
                )

        return spans

    def _detect_repeat_value_spans(
        self,
        text: str,
        vault: PIIVault,
        non_name_terms: set[str],
        suppress_name_entities: bool = False,
    ) -> list[Span]:
        spans: list[Span] = []
        if not text:
            return spans

        for token, stored_value in sorted(vault.items().items(), key=lambda pair: len(pair[1]), reverse=True):
            token_match = VAULT_TOKEN_RE.match(token)
            if not token_match:
                continue

            entity_key = token_match.group("entity")
            if entity_key not in ENTITY_KEYS:
                continue

            value = stored_value.strip()
            if not value:
                continue

            if entity_key in {"fn", "mn1", "mn2", "ln"}:
                if suppress_name_entities:
                    continue
                variants = self._name_repeat_variants(value)
                for variant in variants:
                    letters_only = re.sub(r"[^A-Za-z]", "", variant)
                    normalized = variant.lower()
                    if len(letters_only) < 3:
                        continue
                    if normalized in NON_NAME_SINGLE_WORDS:
                        continue
                    if normalized in GEO_REGION_PHRASES or normalized in GEO_REGION_ABBREVIATIONS:
                        continue
                    if normalized in non_name_terms:
                        continue
                    pattern = re.compile(rf"(?<![A-Za-z]){re.escape(variant)}(?![A-Za-z])", re.IGNORECASE)
                    for match in pattern.finditer(text):
                        spans.append(
                            Span(
                                start=match.start(),
                                end=match.end(),
                                entity_key=entity_key,
                                value=text[match.start() : match.end()],
                            )
                        )
                continue
            elif entity_key == "em":
                pattern = re.compile(re.escape(value), re.IGNORECASE)
            elif entity_key == "ph":
                digits = re.sub(r"\D", "", value)
                if len(digits) >= 10:
                    local = digits[-10:]
                    pattern = re.compile(
                        rf"(?<!\d)(?:\+?1[\s.\-+]*)?"
                        rf"(?:\(?{local[:3]}\)?[\s.\-+]*){local[3:6]}[\s.\-+]*{local[6:]}(?!\d)"
                    )
                else:
                    pattern = re.compile(re.escape(value))
            else:
                pattern = re.compile(re.escape(value), re.IGNORECASE)

            for match in pattern.finditer(text):
                spans.append(
                    Span(
                        start=match.start(),
                        end=match.end(),
                        entity_key=entity_key,
                        value=text[match.start() : match.end()],
                    )
                )

        return spans

    @staticmethod
    def _name_repeat_variants(value: str) -> set[str]:
        variants: set[str] = set()
        base = value.strip()
        if not base:
            return variants
        variants.add(base)

        collapsed = re.sub(r"\s+", "", base)
        if collapsed and collapsed != base:
            variants.add(collapsed)

        # Convert CamelCase single-token names to spaced variant (AgentDan -> Agent Dan).
        camel_spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", collapsed if collapsed else base).strip()
        if camel_spaced and camel_spaced != base:
            variants.add(camel_spaced)

        # Also keep a normalized single-space variant for values with extra whitespace.
        single_spaced = re.sub(r"\s+", " ", base).strip()
        if single_spaced and single_spaced != base:
            variants.add(single_spaced)

        return variants

    def _detect_prompted_name_reply_spans(
        self,
        text: str,
        vault: PIIVault | None,
        request_type: str | None,
        non_name_terms: set[str],
    ) -> list[Span]:
        if request_type not in {"first", "last", "full"}:
            return []

        match = re.match(rf"^\s*({NAME_WORD_PATTERN})\b", text, flags=re.UNICODE)
        if not match:
            return []

        value = match.group(1)
        normalized = value.lower()
        words = re.findall(NAME_WORD_PATTERN, text, flags=re.UNICODE)
        if normalized in NON_NAME_SINGLE_WORDS:
            return []
        if normalized in NAME_PREFIX_EXCLUSIONS:
            return []
        if normalized in NON_NAME_MULTIWORD_COMPONENTS:
            return []
        if normalized in NON_NAME_PHRASE_HINTS:
            return []
        if normalized in GEO_DIRECTION_WORDS:
            return []
        if normalized in GEO_REGION_ABBREVIATIONS:
            return []
        if self._contains_phrase([normalized], GEO_REGION_PHRASES):
            return []
        first_word_in_non_name_terms = self._normalize_text_phrase(value) in non_name_terms
        if first_word_in_non_name_terms and request_type != "full":
            return []

        # Support "name + contact" replies under prompted name collection flows:
        # - first-name prompt: "Lynn 925.963.1940"
        # - full-name prompt: "Angela wbarno2010@gmail.com"
        tail_candidate = re.sub(r"^[\s,;:\-]+", "", text[match.end(1) :]).strip()
        if tail_candidate:
            trimmed_tail = tail_candidate.rstrip(".,!?;)")
            has_contact_tail = bool(EMAIL_RE.fullmatch(trimmed_tail) or PHONE_RE.fullmatch(trimmed_tail))
            if has_contact_tail:
                if request_type in {"first", "full"}:
                    return [Span(match.start(1), match.end(1), "fn", value)]
                if request_type == "last":
                    return [Span(match.start(1), match.end(1), "ln", value)]

        # First-name asks often receive "Name, email phone" in one line.
        # Keep this deterministic: redact only the leading token when contact appears later.
        if request_type == "first":
            tail_text_for_first = text[match.end(1) :]
            if tail_text_for_first and (EMAIL_RE.search(tail_text_for_first) or PHONE_RE.search(tail_text_for_first)):
                return [Span(match.start(1), match.end(1), "fn", value)]

        if request_type == "full":
            initial_full_match = re.match(rf"^\s*({NAME_WORD_PATTERN})\s+([^\W\d_])\b", text, flags=re.UNICODE)
            if initial_full_match:
                tail_after_initial = text[initial_full_match.end(2) :]
                if not tail_after_initial.strip() or tail_after_initial.lstrip()[:1] in {",", ".", "!", "?", ";", ":"}:
                    full_value = f"{initial_full_match.group(1)} {initial_full_match.group(2)}"
                    return [Span(initial_full_match.start(1), initial_full_match.end(2), "name", full_value)]

            csv_match = re.match(
                rf"^\s*({NAME_WORD_PATTERN})\s*,\s*({NAME_WORD_PATTERN})\s*,\s*(.+?)\s*$",
                text,
                flags=re.UNICODE,
            )
            if csv_match:
                first = csv_match.group(1)
                last = csv_match.group(2)
                contact = csv_match.group(3).rstrip(".,!?;)")
                if EMAIL_RE.fullmatch(contact) or PHONE_RE.fullmatch(contact):
                    full_value = f"{first} {last}"
                    return [Span(csv_match.start(1), csv_match.end(2), "name", full_value)]

            dash_match = re.match(
                rf"^\s*({NAME_WORD_PATTERN})\s+({NAME_WORD_PATTERN})\s*-\s*(.+?)\s*$",
                text,
                flags=re.UNICODE,
            )
            if dash_match:
                first = dash_match.group(1)
                last = dash_match.group(2)
                contact = dash_match.group(3).rstrip(".,!?;)")
                if EMAIL_RE.fullmatch(contact) or PHONE_RE.fullmatch(contact):
                    full_value = f"{first} {last}"
                    return [Span(dash_match.start(1), dash_match.end(2), "name", full_value)]

            three_part_match = re.match(
                rf"^\s*({NAME_WORD_PATTERN})\s+({NAME_WORD_PATTERN})\s+({NAME_WORD_PATTERN})\b",
                text,
                flags=re.UNICODE,
            )
            if three_part_match:
                first = three_part_match.group(1)
                middle = three_part_match.group(2)
                last = three_part_match.group(3)
                token_norms = [first.lower(), middle.lower(), last.lower()]
                if not any(
                    token in NON_NAME_SINGLE_WORDS
                    or token in NAME_PREFIX_EXCLUSIONS
                    or token in NON_NAME_MULTIWORD_COMPONENTS
                    or token in NON_NAME_PHRASE_HINTS
                    or token in GEO_DIRECTION_WORDS
                    or token in GEO_REGION_ABBREVIATIONS
                    or self._contains_phrase([token], GEO_REGION_PHRASES)
                    for token in token_norms
                ):
                    full_value = f"{first} {middle} {last}"
                    full_normalized = self._normalize_text_phrase(full_value)
                    tail_after_three = text[three_part_match.end(3) :]
                    tail_after_three_stripped = re.sub(r"^[\s,;:\-]+", "", tail_after_three)
                    has_contact_tail = bool(
                        tail_after_three_stripped
                        and (EMAIL_RE.match(tail_after_three_stripped) or PHONE_RE.match(tail_after_three_stripped))
                    )
                    if (
                        full_normalized not in non_name_terms
                        and (
                            not tail_after_three_stripped
                            or tail_after_three_stripped[0] in {",", ".", "!", "?", ";", ":"}
                            or has_contact_tail
                        )
                    ):
                        return [Span(three_part_match.start(1), three_part_match.end(3), "name", full_value)]

        if request_type == "last" and vault is not None:
            # If first name is already captured and user replies "First Last <contact>",
            # assign the second token as ln so ln is not lost to overlapping fn spans.
            fn_token = vault.latest_token_for_entity("fn")
            known_first = vault.get(fn_token) if fn_token else ""
            two_part_match = re.match(
                rf"^\s*({NAME_WORD_PATTERN})\s+({NAME_WORD_PATTERN})\b",
                text,
                flags=re.UNICODE,
            )
            if known_first and two_part_match:
                first = two_part_match.group(1)
                second = two_part_match.group(2)
                first_normalized = self._normalize_text_phrase(first)
                known_first_normalized = self._normalize_text_phrase(known_first)
                second_normalized = second.lower()
                first_matches_known = first_normalized and first_normalized == known_first_normalized
                first_similar_known = (
                    bool(first_normalized)
                    and bool(known_first_normalized)
                    and self._is_similar_name_token(first_normalized, known_first_normalized)
                )
                if (
                    (first_matches_known or first_similar_known)
                    and second_normalized not in NON_NAME_SINGLE_WORDS
                    and second_normalized not in NAME_PREFIX_EXCLUSIONS
                    and second_normalized not in NON_NAME_MULTIWORD_COMPONENTS
                    and second_normalized not in NON_NAME_PHRASE_HINTS
                    and second_normalized not in GEO_DIRECTION_WORDS
                    and second_normalized not in GEO_REGION_ABBREVIATIONS
                    and not self._contains_phrase([second_normalized], GEO_REGION_PHRASES)
                    and self._normalize_text_phrase(second) not in non_name_terms
                ):
                    tail_after_second = text[two_part_match.end(2) :]
                    tail_after_second_normalized = tail_after_second.strip().lower()
                    has_contact_after_second = bool(
                        EMAIL_RE.search(tail_after_second) or PHONE_RE.search(tail_after_second)
                    )
                    if has_contact_after_second:
                        return [Span(two_part_match.start(2), two_part_match.end(2), "ln", second)]
                    if tail_after_second_normalized.startswith(("my full name", "full name", "my name")):
                        full_value = f"{first} {second}"
                        return [
                            Span(
                                two_part_match.start(1),
                                two_part_match.end(2),
                                "name",
                                full_value,
                                prefer_latest=True,
                            )
                        ]

            if known_first:
                known_first_pattern = re.compile(
                    rf"\b({re.escape(known_first)})\b\s+({NAME_WORD_PATTERN})\b",
                    flags=re.IGNORECASE | re.UNICODE,
                )
                follow_match = known_first_pattern.search(text)
                if follow_match:
                    candidate_last = follow_match.group(2)
                    candidate_normalized = candidate_last.lower()
                    if (
                        candidate_normalized not in NON_NAME_SINGLE_WORDS
                        and candidate_normalized not in NAME_PREFIX_EXCLUSIONS
                        and candidate_normalized not in NON_NAME_MULTIWORD_COMPONENTS
                        and candidate_normalized not in NON_NAME_PHRASE_HINTS
                        and candidate_normalized not in GEO_DIRECTION_WORDS
                        and candidate_normalized not in GEO_REGION_ABBREVIATIONS
                        and not self._contains_phrase([candidate_normalized], GEO_REGION_PHRASES)
                        and self._normalize_text_phrase(candidate_last) not in non_name_terms
                    ):
                        return [Span(follow_match.start(2), follow_match.end(2), "ln", candidate_last)]

        if len(words) >= 2:
            second = words[1]
            second_normalized = second.lower()
            if second_normalized in NON_NAME_MULTIWORD_COMPONENTS or second_normalized in NON_NAME_PHRASE_HINTS:
                return []
            if second_normalized in GEO_REGION_ABBREVIATIONS:
                return []
            if self._contains_phrase([second_normalized], GEO_REGION_PHRASES):
                return []
            if self._contains_phrase([normalized, second_normalized], GEO_REGION_PHRASES):
                return []

            # If the user supplied a clean two-token name after a name prompt,
            # treat it as a full name to avoid redacting only the first token.
            full_match = re.match(
                rf"^\s*({NAME_WORD_PATTERN})\s+({NAME_WORD_PATTERN})\b",
                text,
                flags=re.UNICODE,
            )
            if full_match:
                full_value = f"{full_match.group(1)} {full_match.group(2)}"
                full_normalized = self._normalize_text_phrase(full_value)
                tail_after_full = text[full_match.end(2) :]
                tail_after_full_stripped = re.sub(r"^[\s,;:\-]+", "", tail_after_full)
                has_contact_tail = bool(
                    tail_after_full_stripped
                    and (EMAIL_RE.match(tail_after_full_stripped) or PHONE_RE.match(tail_after_full_stripped))
                )
                has_contact_anywhere = bool(EMAIL_RE.search(tail_after_full) or PHONE_RE.search(tail_after_full))
                allow_lower_second = request_type == "full" and (
                    has_contact_anywhere
                    or (len(words) == 2 and not tail_after_full_stripped and not first_word_in_non_name_terms)
                )
                if (
                    (value[0].isupper() or request_type == "full")
                    and (second[0].isupper() or has_contact_tail or allow_lower_second)
                    and second_normalized not in NON_NAME_SINGLE_WORDS
                    and second_normalized not in NON_NAME_MULTIWORD_COMPONENTS
                    and second_normalized not in NAME_PREFIX_EXCLUSIONS
                    and full_normalized not in non_name_terms
                    and (
                        not tail_after_full_stripped
                        or tail_after_full_stripped[0] in {",", ".", "!", "?", ";", ":"}
                        or has_contact_tail
                        or has_contact_anywhere
                    )
                ):
                    return [
                        Span(
                            full_match.start(1),
                            full_match.end(2),
                            "name",
                            full_value,
                        )
                    ]

                if request_type == "first" and len(words) == 2 and not tail_after_full_stripped:
                    has_contact_in_vault = bool(
                        vault
                        and (vault.latest_token_for_entity("em") or vault.latest_token_for_entity("ph"))
                    )
                    if (
                        has_contact_in_vault
                        and second_normalized not in NON_NAME_SINGLE_WORDS
                        and second_normalized not in NON_NAME_MULTIWORD_COMPONENTS
                        and second_normalized not in NAME_PREFIX_EXCLUSIONS
                        and full_normalized not in non_name_terms
                    ):
                        return [Span(full_match.start(1), full_match.end(2), "name", full_value)]

            # First-name prompts should not redact arbitrary sentence starters.
            # If we did not accept a clean name pattern above, treat multi-word
            # replies as non-name content.
            if request_type == "first":
                return []

        if request_type == "full":
            return []

        tail_text = text[match.end(1) :]
        if re.match(r"\s+\d", tail_text):
            return []

        if request_type == "last":
            stripped_tail = tail_text.strip()
            if stripped_tail:
                lstripped_tail = tail_text.lstrip()
                if lstripped_tail.startswith(","):
                    pass
                elif EMAIL_RE.match(stripped_tail) or PHONE_RE.match(stripped_tail):
                    pass
                elif (
                    value[0].isupper()
                    and lstripped_tail.lower().startswith(("could ", "can ", "please ", "just ", "i ", "and "))
                ):
                    pass
                else:
                    return []

        next_char = tail_text[:1]
        if next_char and next_char not in {",", ".", "!", "?", ";", ":", " ", "\t"}:
            return []

        if not value[0].isalpha():
            return []

        entity_key = "fn" if request_type == "first" else "ln"
        return [Span(match.start(1), match.end(1), entity_key, value)]

    def _detect_affirmative_name_contact_spans(self, text: str, non_name_terms: set[str]) -> list[Span]:
        match = re.match(
            rf"^\s*(?:yes|yeah|yep|yup|sure|ok|okay)\s+({NAME_WORD_PATTERN})\s+({NAME_WORD_PATTERN})\b",
            text,
            flags=re.IGNORECASE | re.UNICODE,
        )
        if not match:
            return []

        first = match.group(1)
        second = match.group(2)
        first_normalized = first.lower()
        second_normalized = second.lower()
        if first_normalized in NON_NAME_SINGLE_WORDS or second_normalized in NON_NAME_SINGLE_WORDS:
            return []
        if second_normalized in {"name", "email", "phone", "number", "contact"}:
            return []
        if first_normalized in NAME_PREFIX_EXCLUSIONS or second_normalized in NAME_PREFIX_EXCLUSIONS:
            return []
        if second_normalized in GEO_DIRECTION_WORDS or second_normalized in GEO_REGION_ABBREVIATIONS:
            return []
        if self._contains_phrase([second_normalized], GEO_REGION_PHRASES):
            return []
        full_normalized = self._normalize_text_phrase(f"{first} {second}")
        if full_normalized in non_name_terms:
            return []

        tail_after_second = text[match.end(2) :]
        if not (EMAIL_RE.search(tail_after_second) or PHONE_RE.search(tail_after_second)):
            return []

        return [
            Span(match.start(1), match.end(1), "fn", first),
            Span(match.start(2), match.end(2), "ln", second),
        ]

    def _detect_leading_name_with_contact_spans(self, text: str, non_name_terms: set[str]) -> list[Span]:
        match = re.match(rf"^\s*({NAME_WORD_PATTERN})\s+({NAME_WORD_PATTERN})\b", text, flags=re.UNICODE)
        if not match:
            return []
        first = match.group(1)
        second = match.group(2)
        first_normalized = first.lower()
        second_normalized = second.lower()
        if first_normalized in NON_NAME_SINGLE_WORDS or second_normalized in NON_NAME_SINGLE_WORDS:
            return []
        if first_normalized in NAME_PREFIX_EXCLUSIONS or second_normalized in NAME_PREFIX_EXCLUSIONS:
            return []
        if first_normalized in NON_NAME_MULTIWORD_COMPONENTS or second_normalized in NON_NAME_MULTIWORD_COMPONENTS:
            return []
        if first_normalized in GEO_DIRECTION_WORDS or second_normalized in GEO_DIRECTION_WORDS:
            return []
        if first_normalized in GEO_REGION_ABBREVIATIONS or second_normalized in GEO_REGION_ABBREVIATIONS:
            return []
        if self._contains_phrase([first_normalized], GEO_REGION_PHRASES):
            return []
        if self._contains_phrase([second_normalized], GEO_REGION_PHRASES):
            return []
        full_normalized = self._normalize_text_phrase(f"{first} {second}")
        if full_normalized in non_name_terms:
            return []

        tail_after_second = text[match.end(2) :]
        has_contact_tail = bool(EMAIL_RE.search(tail_after_second) or PHONE_RE.search(tail_after_second))
        if not has_contact_tail:
            return []
        return [
            Span(match.start(1), match.end(1), "fn", first),
            Span(match.start(2), match.end(2), "ln", second),
        ]

    def _detect_contact_then_name_spans(self, text: str, non_name_terms: set[str]) -> list[Span]:
        contact_match = EMAIL_RE.search(text) or PHONE_RE.search(text)
        if not contact_match:
            return []

        tail = text[contact_match.end() :]
        tail = re.sub(r"^[\s,;:\-@]+", "", tail)
        if not tail:
            return []

        match = re.match(rf"^\s*({NAME_WORD_PATTERN})\s+({NAME_WORD_PATTERN})\b", tail, flags=re.UNICODE)
        if not match:
            return []

        first = match.group(1)
        second = match.group(2)
        first_normalized = first.lower()
        second_normalized = second.lower()
        if first_normalized in NON_NAME_SINGLE_WORDS or second_normalized in NON_NAME_SINGLE_WORDS:
            return []
        if first_normalized in NAME_PREFIX_EXCLUSIONS or second_normalized in NAME_PREFIX_EXCLUSIONS:
            return []
        if first_normalized in NON_NAME_MULTIWORD_COMPONENTS or second_normalized in NON_NAME_MULTIWORD_COMPONENTS:
            return []
        if first_normalized in GEO_DIRECTION_WORDS or second_normalized in GEO_DIRECTION_WORDS:
            return []
        if first_normalized in GEO_REGION_ABBREVIATIONS or second_normalized in GEO_REGION_ABBREVIATIONS:
            return []
        if self._contains_phrase([first_normalized], GEO_REGION_PHRASES):
            return []
        if self._contains_phrase([second_normalized], GEO_REGION_PHRASES):
            return []

        full_normalized = self._normalize_text_phrase(f"{first} {second}")
        if full_normalized in non_name_terms:
            return []

        trailing_after_name = tail[match.end(2) :].strip()
        if trailing_after_name and not re.fullmatch(r"[.,!?;:)\]}\"']*", trailing_after_name):
            return []

        base_offset = contact_match.end() + (len(text[contact_match.end() :]) - len(text[contact_match.end() :].lstrip(" ,;:-@")))
        start_first = base_offset + match.start(1)
        end_first = base_offset + match.end(1)
        start_second = base_offset + match.start(2)
        end_second = base_offset + match.end(2)
        return [
            Span(start_first, end_first, "fn", first),
            Span(start_second, end_second, "ln", second),
        ]

    def _detect_contact_with_parenthetical_name_spans(self, text: str, non_name_terms: set[str]) -> list[Span]:
        if not (EMAIL_RE.search(text) or PHONE_RE.search(text)):
            return []
        match = re.search(rf"\((?P<name>{NAME_WORD_PATTERN})\)", text, flags=re.UNICODE)
        if not match:
            return []
        value = match.group("name")
        normalized = value.lower()
        if normalized in NON_NAME_SINGLE_WORDS:
            return []
        if normalized in NAME_PREFIX_EXCLUSIONS:
            return []
        if normalized in NON_NAME_MULTIWORD_COMPONENTS or normalized in NON_NAME_PHRASE_HINTS:
            return []
        if normalized in GEO_DIRECTION_WORDS or normalized in GEO_REGION_ABBREVIATIONS:
            return []
        if self._contains_phrase([normalized], GEO_REGION_PHRASES):
            return []
        if self._normalize_text_phrase(value) in non_name_terms:
            return []
        return [Span(match.start("name"), match.end("name"), "fn", value)]

    def _detect_signature_tail_name_spans(self, text: str, non_name_terms: set[str]) -> list[Span]:
        match = SIGNATURE_TAIL_NAME_RE.search(text)
        if not match:
            return []

        first = match.group("first")
        last = match.group("last")
        first_normalized = first.lower()
        last_normalized = last.lower()
        if first_normalized in NON_NAME_SINGLE_WORDS or last_normalized in NON_NAME_SINGLE_WORDS:
            return []
        if first_normalized in NAME_PREFIX_EXCLUSIONS or last_normalized in NAME_PREFIX_EXCLUSIONS:
            return []
        if first_normalized in NON_NAME_MULTIWORD_COMPONENTS or last_normalized in NON_NAME_MULTIWORD_COMPONENTS:
            return []
        if first_normalized in GEO_DIRECTION_WORDS or last_normalized in GEO_DIRECTION_WORDS:
            return []
        if first_normalized in GEO_REGION_ABBREVIATIONS or last_normalized in GEO_REGION_ABBREVIATIONS:
            return []
        if self._contains_phrase([first_normalized], GEO_REGION_PHRASES):
            return []
        if self._contains_phrase([last_normalized], GEO_REGION_PHRASES):
            return []

        full_normalized = self._normalize_text_phrase(f"{first} {last}")
        if full_normalized in non_name_terms:
            return []

        tail_tokens = [token.lower() for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]*", match.group("tail"))]
        has_brokerage_tail = any(
            token in SIGNATURE_BROKERAGE_CUES or re.fullmatch(r"c\d{1,3}", token) for token in tail_tokens
        )
        if not has_brokerage_tail:
            return []

        return [Span(match.start("first"), match.end("last"), "name", f"{first} {last}")]

    def _detect_realtor_pair_intro_spans(self, text: str, non_name_terms: set[str]) -> list[Span]:
        match = REALTOR_PAIR_INTRO_RE.match(text)
        if not match:
            return []

        f1 = match.group("f1")
        l1 = match.group("l1")
        f2 = match.group("f2")
        l2 = match.group("l2")
        tokens = [f1, l1, f2, l2]
        lower_tokens = [token.lower() for token in tokens]
        if any(token in NON_NAME_SINGLE_WORDS for token in lower_tokens):
            return []
        if any(token in NAME_PREFIX_EXCLUSIONS for token in lower_tokens):
            return []
        if any(token in NON_NAME_MULTIWORD_COMPONENTS for token in lower_tokens):
            return []
        if any(token in GEO_DIRECTION_WORDS for token in lower_tokens):
            return []
        if any(token in GEO_REGION_ABBREVIATIONS for token in lower_tokens):
            return []
        if any(self._contains_phrase([token], GEO_REGION_PHRASES) for token in lower_tokens):
            return []

        if any(self._normalize_text_phrase(token) in non_name_terms for token in tokens):
            return []

        return [
            Span(match.start("f1"), match.end("f1"), "fn", f1),
            Span(match.start("l1"), match.end("l1"), "ln", l1),
            Span(match.start("f2"), match.end("f2"), "mn1", f2),
            Span(match.start("l2"), match.end("l2"), "mn2", l2),
        ]

    def _detect_name_correction_spans(self, text: str, vault: PIIVault, non_name_terms: set[str]) -> list[Span]:
        fn_token = vault.latest_token_for_entity("fn")
        ln_token = vault.latest_token_for_entity("ln")
        known_first = vault.get(fn_token) if fn_token else ""
        known_last = vault.get(ln_token) if ln_token else ""
        if not known_first or not known_last:
            return []

        one_word_match = re.match(rf"^\s*({NAME_WORD_PATTERN})\s*$", text, flags=re.UNICODE)
        if one_word_match:
            candidate = one_word_match.group(1)
            candidate_normalized = candidate.lower()
            if (
                candidate_normalized not in NON_NAME_SINGLE_WORDS
                and candidate_normalized not in NAME_PREFIX_EXCLUSIONS
                and candidate_normalized not in NON_NAME_MULTIWORD_COMPONENTS
                and candidate_normalized not in NON_NAME_PHRASE_HINTS
                and candidate_normalized not in GEO_DIRECTION_WORDS
                and candidate_normalized not in GEO_REGION_ABBREVIATIONS
                and not self._contains_phrase([candidate_normalized], GEO_REGION_PHRASES)
                and self._normalize_text_phrase(candidate) not in non_name_terms
                and self._is_similar_name_token(self._normalize_text_phrase(candidate), self._normalize_text_phrase(known_last))
            ):
                return [Span(one_word_match.start(1), one_word_match.end(1), "ln", candidate, prefer_latest=True)]

        match = re.match(rf"^\s*({NAME_WORD_PATTERN})\s+({NAME_WORD_PATTERN})\b", text, flags=re.UNICODE)
        if not match:
            return []

        first = match.group(1)
        second = match.group(2)
        first_normalized = self._normalize_text_phrase(first)
        known_first_normalized = self._normalize_text_phrase(known_first)
        second_normalized = second.lower()
        if not first_normalized or first_normalized != known_first_normalized:
            return []
        if second_normalized in NON_NAME_SINGLE_WORDS or second_normalized in NAME_PREFIX_EXCLUSIONS:
            return []
        if second_normalized in NON_NAME_MULTIWORD_COMPONENTS or second_normalized in NON_NAME_PHRASE_HINTS:
            return []
        if second_normalized in GEO_DIRECTION_WORDS or second_normalized in GEO_REGION_ABBREVIATIONS:
            return []
        if self._contains_phrase([second_normalized], GEO_REGION_PHRASES):
            return []
        if self._normalize_text_phrase(second) in non_name_terms:
            return []

        known_last_normalized = self._normalize_text_phrase(known_last)
        if not self._is_similar_name_token(self._normalize_text_phrase(second), known_last_normalized):
            return []

        tail_after_second = text[match.end(2) :]
        tail_after_second_stripped = tail_after_second.strip()
        if tail_after_second_stripped and not (
            tail_after_second_stripped[0] in {",", ".", "!", "?", ";", ":"}
            or EMAIL_RE.match(tail_after_second_stripped)
            or PHONE_RE.match(tail_after_second_stripped)
        ):
            return []

        return [Span(match.start(2), match.end(2), "ln", second, prefer_latest=True)]

    def _detect_name_labelled_reply_spans(self, text: str, non_name_terms: set[str]) -> list[Span]:
        for pattern in (NAME_LABELLED_REPLY_RE, NAME_LABELLED_REPLY_ALT_RE, LAST_NAME_LABELLED_REPLY_SPACE_RE):
            match = pattern.match(text)
            if not match:
                continue

            value = match.group("name")
            normalized = value.lower()
            if normalized in NON_NAME_SINGLE_WORDS:
                continue
            if normalized in NAME_PREFIX_EXCLUSIONS:
                continue
            if normalized in NON_NAME_MULTIWORD_COMPONENTS:
                continue
            if normalized in NON_NAME_PHRASE_HINTS:
                continue
            if normalized in GEO_REGION_ABBREVIATIONS:
                continue
            if self._contains_phrase([normalized], GEO_REGION_PHRASES):
                continue
            if self._normalize_text_phrase(value) in non_name_terms:
                continue

            label = re.sub(r"\s+", " ", match.group("label").strip().lower())
            entity_key = "fn" if "first" in label else "ln"
            return [Span(match.start("name"), match.end("name"), entity_key, value)]

        inline_last_match = INLINE_LAST_NAME_IS_RE.search(text)
        if inline_last_match:
            value = inline_last_match.group("name")
            normalized = value.lower()
            if normalized in NON_NAME_SINGLE_WORDS:
                return []
            if normalized in NAME_PREFIX_EXCLUSIONS:
                return []
            if normalized in NON_NAME_MULTIWORD_COMPONENTS:
                return []
            if normalized in NON_NAME_PHRASE_HINTS:
                return []
            if normalized in GEO_REGION_ABBREVIATIONS:
                return []
            if self._contains_phrase([normalized], GEO_REGION_PHRASES):
                return []
            if self._normalize_text_phrase(value) in non_name_terms:
                return []
            return [Span(inline_last_match.start("name"), inline_last_match.end("name"), "ln", value)]

        return []

    def _detect_keyed_name_value_spans(self, text: str, non_name_terms: set[str]) -> list[Span]:
        spans: list[Span] = []
        for match in KEYED_NAME_VALUE_RE.finditer(text):
            label = re.sub(r"\s+", " ", match.group("label").strip().lower())
            value_text = match.group("value")
            token_matches = list(re.finditer(r"[A-Za-z][A-Za-z'\-]*", value_text))
            if not token_matches:
                continue

            has_first = "first" in label
            has_last = "last" in label

            if has_first and not has_last:
                token = token_matches[0].group(0)
                normalized = token.lower()
                if normalized in NON_NAME_SINGLE_WORDS or normalized in non_name_terms:
                    continue
                spans.append(
                    Span(
                        match.start("value") + token_matches[0].start(),
                        match.start("value") + token_matches[0].end(),
                        "fn",
                        token,
                    )
                )
                continue

            if has_last and not has_first:
                token = token_matches[0].group(0)
                normalized = token.lower()
                if normalized in NON_NAME_SINGLE_WORDS or normalized in non_name_terms:
                    continue
                spans.append(
                    Span(
                        match.start("value") + token_matches[0].start(),
                        match.start("value") + token_matches[0].end(),
                        "ln",
                        token,
                    )
                )
                continue

            # Generic "name/names" keys: capture first+last when present, including
            # one-letter last initials (common CRM entry pattern).
            first = token_matches[0].group(0)
            first_normalized = first.lower()
            if first_normalized in NON_NAME_SINGLE_WORDS or first_normalized in non_name_terms:
                continue

            if len(token_matches) >= 2:
                second = token_matches[1].group(0)
                second_normalized = second.lower()
                if second_normalized in NON_NAME_SINGLE_WORDS:
                    continue
                if second_normalized in non_name_terms and len(second) > 1:
                    continue
                spans.append(
                    Span(
                        match.start("value") + token_matches[0].start(),
                        match.start("value") + token_matches[0].end(),
                        "fn",
                        first,
                    )
                )
                spans.append(
                    Span(
                        match.start("value") + token_matches[1].start(),
                        match.start("value") + token_matches[1].end(),
                        "ln",
                        second,
                    )
                )
            else:
                spans.append(
                    Span(
                        match.start("value") + token_matches[0].start(),
                        match.start("value") + token_matches[0].end(),
                        "fn",
                        first,
                    )
                )
        return spans

    def _detect_email_phone_spans_regex(self, text: str) -> list[Span]:
        spans: list[Span] = []

        for match in EMAIL_RE.finditer(text):
            spans.append(Span(match.start(), match.end(), "em", match.group(0)))

        for match in PHONE_RE.finditer(text):
            spans.append(Span(match.start(), match.end(), "ph", match.group(0)))

        return spans

    def _detect_email_phone_spans_presidio(self, text: str) -> list[Span]:
        if self._presidio_analyzer is None:
            return []

        try:
            results = self._presidio_analyzer.analyze(
                text=text,
                entities=tuple(PRESIDIO_ENTITY_MAP.keys()),
                language="en",
            )
        except Exception as exc:  # pragma: no cover - depends on runtime deps
            LOGGER.info("Presidio analyze failed; regex fallback remains active: %s", exc)
            return []

        spans: list[Span] = []
        for result in results:
            entity_type = getattr(result, "entity_type", "")
            entity_key = PRESIDIO_ENTITY_MAP.get(entity_type)
            if not entity_key:
                continue

            start = int(getattr(result, "start", -1))
            end = int(getattr(result, "end", -1))
            if start < 0 or end <= start or end > len(text):
                continue

            spans.append(Span(start=start, end=end, entity_key=entity_key, value=text[start:end]))

        return spans

    def _detect_name_spans_gliner(
        self,
        text: str,
        assistant_requests_name: bool,
        non_name_terms: set[str],
    ) -> list[Span]:
        if self._gliner_model_handle is None:
            return []

        try:
            predictions = self._gliner_model_handle.predict_entities(
                text,
                labels=list(self._gliner_labels),
                threshold=self._gliner_threshold,
            )
        except TypeError:
            predictions = self._gliner_model_handle.predict_entities(text, labels=list(self._gliner_labels))
        except Exception as exc:  # pragma: no cover - depends on runtime deps
            LOGGER.info("GLiNER predict failed; heuristic fallback remains active: %s", exc)
            return []

        spans: list[Span] = []
        for prediction in predictions or []:
            label = str(self._prediction_field(prediction, "label") or "").lower()
            if not self._is_name_label(label):
                continue

            start = self._prediction_field(prediction, "start")
            end = self._prediction_field(prediction, "end")

            if start is None or end is None:
                text_chunk = str(self._prediction_field(prediction, "text") or "")
                located = self._locate_text_span(text, text_chunk, spans)
                if not located:
                    continue
                start, end = located

            try:
                start_i = int(start)
                end_i = int(end)
            except (TypeError, ValueError):
                continue

            if start_i < 0 or end_i <= start_i or end_i > len(text):
                continue

            value, keep_chars = self._trim_trailing_name_noise(text[start_i:end_i])
            if self._is_plausible_name_span(
                value,
                text,
                start_i,
                start_i + keep_chars,
                assistant_requests_name=assistant_requests_name,
                non_name_terms=non_name_terms,
            ):
                spans.append(Span(start_i, start_i + keep_chars, "name", value))

        return spans

    def _detect_name_spans_heuristic(
        self,
        text: str,
        assistant_requests_name: bool,
        non_name_terms: set[str],
    ) -> list[Span]:
        spans: list[Span] = []

        for match in THIS_IS_NAME_WITH_CONTEXT_RE.finditer(text):
            full_value = f"{match.group('first')} {match.group('last')}"
            if self._is_plausible_name_span(
                full_value,
                text,
                match.start("first"),
                match.end("last"),
                assistant_requests_name=assistant_requests_name,
                non_name_terms=non_name_terms,
            ):
                spans.append(Span(match.start("first"), match.end("last"), "name", full_value))

        for match in NAME_INTRO_RE.finditer(text):
            value, keep_chars = self._trim_trailing_name_noise(match.group("candidate"))
            cue = (match.group("cue") or "").lower()
            if cue.startswith("my n"):
                intro_value, intro_chars = self._extract_name_intro_candidate(match.group("candidate"))
                if intro_value:
                    value, keep_chars = intro_value, intro_chars
            if self._is_plausible_name_span(
                value,
                text,
                match.start("candidate"),
                match.start("candidate") + keep_chars,
                assistant_requests_name=assistant_requests_name,
                non_name_terms=non_name_terms,
                ):
                    spans.append(
                        Span(
                            match.start("candidate"),
                            match.start("candidate") + keep_chars,
                        "name",
                        value,
                    )
                )

        for match in COORDINATED_FULL_NAMES_RE.finditer(text):
            context_before = text[max(0, match.start() - 70) : match.start()].lower()
            if not any(cue in context_before for cue in COORDINATED_NAME_CONTEXT_CUES):
                continue

            first_pair = f"{match.group('first1')} {match.group('last1')}"
            second_pair = f"{match.group('first2')} {match.group('last2')}"

            if self._is_plausible_name_span(
                first_pair,
                text,
                match.start("first1"),
                match.end("last1"),
                assistant_requests_name=assistant_requests_name,
                non_name_terms=non_name_terms,
            ):
                spans.append(Span(match.start("first1"), match.end("last1"), "name", first_pair))

            if self._is_plausible_name_span(
                second_pair,
                text,
                match.start("first2"),
                match.end("last2"),
                assistant_requests_name=assistant_requests_name,
                non_name_terms=non_name_terms,
            ):
                spans.append(Span(match.start("first2"), match.end("last2"), "name", second_pair))

        for match in COORDINATED_NAME_RE.finditer(text):
            context_before = text[max(0, match.start() - 50) : match.start()].lower()
            if not any(cue in context_before for cue in COORDINATED_NAME_CONTEXT_CUES):
                continue
            value, keep_chars = self._trim_trailing_name_noise(match.group(0))
            if self._is_plausible_name_span(
                value,
                text,
                match.start(),
                match.start() + keep_chars,
                assistant_requests_name=assistant_requests_name,
                non_name_terms=non_name_terms,
            ):
                spans.append(Span(match.start(), match.start() + keep_chars, "name", value))

        if not spans and NAME_REPLY_RE.match(text):
            clean = text.strip().rstrip(".!?")
            start = text.find(clean)
            end = start + len(clean)
            if self._is_plausible_name_span(
                clean,
                text,
                start,
                end,
                assistant_requests_name=assistant_requests_name,
                non_name_terms=non_name_terms,
            ):
                spans.append(Span(start, end, "name", clean))

        return spans

    @staticmethod
    def _prediction_field(prediction: Any, key: str) -> Any:
        if isinstance(prediction, dict):
            return prediction.get(key)
        return getattr(prediction, key, None)

    @staticmethod
    def _is_name_label(label: str) -> bool:
        if not label:
            return False
        return "name" in label or "person" in label

    @staticmethod
    def _locate_text_span(text: str, chunk: str, existing_spans: list[Span]) -> tuple[int, int] | None:
        if not chunk:
            return None

        search_start = 0
        while True:
            idx = text.find(chunk, search_start)
            if idx < 0:
                return None

            end = idx + len(chunk)
            has_overlap = any(max(idx, span.start) < min(end, span.end) for span in existing_spans)
            if not has_overlap:
                return idx, end

            search_start = idx + 1

    @staticmethod
    def _is_plausible_name_span(
        value: str,
        source_text: str,
        start: int,
        end: int,
        assistant_requests_name: bool,
        non_name_terms: set[str],
    ) -> bool:
        words = re.findall(r"[A-Za-z][A-Za-z'\-]*", value)
        if not words:
            return False
        if len(words) > 5:
            return False
        if any(len(w) <= 1 for w in words):
            return False

        lower_words = [w.lower() for w in words]
        if lower_words[0] in NAME_PREFIX_EXCLUSIONS:
            return False
        if len(words) >= 2 and lower_words[0] in {"yes", "no"}:
            return False
        if len(words) >= 2 and lower_words[0] in GEO_DIRECTION_WORDS:
            return False

        if PIIEngine._looks_like_location_non_name_phrase(lower_words, non_name_terms):
            return False
        if PIIEngine._looks_like_geo_non_name_phrase(lower_words, non_name_terms):
            return False
        if PIIEngine._looks_like_company_non_name_phrase(lower_words):
            return False

        if len(words) > 1 and any(w in NON_NAME_MULTIWORD_COMPONENTS for w in lower_words):
            return False

        source_words = re.findall(r"[A-Za-z][A-Za-z'\-]*", source_text)
        context_before = source_text[max(0, start - 40) : start].lower()
        context_after = source_text[end : min(len(source_text), end + 40)].lower()
        has_name_cue = any(cue in context_before for cue in NAME_CONTEXT_CUES) or any(
            cue in context_after for cue in NAME_CONTEXT_CUES
        )
        if len(words) >= 2 and lower_words[0] in NON_NAME_SINGLE_WORDS and not (has_name_cue or assistant_requests_name):
            return False
        if (
            len(words) == 2
            and lower_words[1] in {"east", "west", "north", "south"}
            and len(lower_words[0]) >= 5
            and not (has_name_cue or assistant_requests_name)
        ):
            return False

        weak_intro_cue_match = re.search(r"(?:^|[\s(])(?P<cue>i am|i'm|this is)\s*$", context_before)
        weak_intro_cue = weak_intro_cue_match.group("cue") if weak_intro_cue_match else None
        if weak_intro_cue in WEAK_NAME_INTRO_CUES:
            first = lower_words[0]
            if first in WEAK_NAME_INTRO_NON_NAME_STARTS:
                return False
            if first in NON_NAME_SINGLE_WORDS:
                return False
            if not assistant_requests_name:
                if len(words) == 1 and not words[0][0].isupper():
                    return False
                if not words[0][0].isupper() and len(source_words) > 3:
                    return False
                if len(words) >= 2 and not all(w[0].isupper() for w in words[:2]):
                    return False
                if len(words) >= 3 and not all(w[0].isupper() for w in words[:3]):
                    return False
            if len(words) >= 2 and value.isupper():
                return False
            if len(words) >= 3 and any(word in NON_NAME_PHRASE_HINTS for word in lower_words[:3]):
                return False

        if len(words) >= 3:
            non_name_hint_hits = sum(1 for word in lower_words if word in NON_NAME_PHRASE_HINTS)
            if non_name_hint_hits >= 3:
                return False
            if non_name_hint_hits >= 2 and not (has_name_cue or assistant_requests_name):
                return False

        normalized_span = PIIEngine._normalize_text_phrase(value)
        if normalized_span in HARDCODED_NON_NAME_PHRASES:
            return False
        if normalized_span and normalized_span in non_name_terms and not (has_name_cue or assistant_requests_name):
            return False

        prev_word_match = re.search(r"([a-z][a-z'\-]*)\s*$", context_before)
        if prev_word_match and prev_word_match.group(1) in NAME_PREFIX_EXCLUSIONS:
            return False

        if len(words) == 1:
            single = lower_words[0]
            if single in NON_NAME_SINGLE_WORDS:
                return False

            if single in non_name_terms and not (has_name_cue or assistant_requests_name):
                return False

            # Single-token names are high-risk false positives. Accept them when:
            # - explicit name cues exist in nearby user text, or
            # - previous assistant prompt asked for a name.
            # Otherwise, only accept if the whole user text is exactly one word and
            # the assistant explicitly asked for name.
            if has_name_cue:
                return True
            if assistant_requests_name and len(source_words) == 1:
                return True
            return False

        # For multi-word spans, require at least one token with uppercase initial unless
        # explicit name cues are present.
        if not any(w[0].isupper() for w in words):
            if not (has_name_cue or assistant_requests_name):
                return False

        return True

    @staticmethod
    def _contains_phrase(words: list[str], phrases: set[str], max_words: int = 4) -> bool:
        if not words:
            return False
        upper = min(max_words, len(words))
        for width in range(upper, 0, -1):
            for idx in range(0, len(words) - width + 1):
                if " ".join(words[idx : idx + width]) in phrases:
                    return True
        return False

    @staticmethod
    def _looks_like_geo_non_name_phrase(lower_words: list[str], non_name_terms: set[str]) -> bool:
        if not lower_words:
            return False
        normalized_phrase = " ".join(lower_words)
        if normalized_phrase in GEO_REGION_PHRASES and len(lower_words) >= 2:
            return True

        has_allowlisted_geo_term = PIIEngine._contains_phrase(lower_words, non_name_terms)

        has_region = PIIEngine._contains_phrase(lower_words, GEO_REGION_PHRASES) or any(
            word in GEO_REGION_ABBREVIATIONS for word in lower_words
        )
        has_direction = any(word in GEO_DIRECTION_WORDS for word in lower_words)
        has_county = "county" in lower_words
        if has_allowlisted_geo_term and (has_region or has_direction):
            return True
        if has_county and has_region:
            return True
        if has_direction and has_region:
            return True
        return False

    @staticmethod
    def _count_matching_phrases(words: list[str], phrases: set[str], max_words: int = 4) -> int:
        if not words:
            return 0
        seen: set[str] = set()
        upper = min(max_words, len(words))
        for width in range(upper, 0, -1):
            for idx in range(0, len(words) - width + 1):
                candidate = " ".join(words[idx : idx + width])
                if candidate in phrases:
                    seen.add(candidate)
        return len(seen)

    @staticmethod
    def _looks_like_location_non_name_phrase(lower_words: list[str], non_name_terms: set[str]) -> bool:
        if not lower_words or not non_name_terms:
            return False
        normalized_text = " ".join(lower_words)
        has_allowlisted_geo_term = PIIEngine._contains_phrase(lower_words, non_name_terms)
        if not has_allowlisted_geo_term:
            return False

        if any(normalized_text.startswith(f"{cue} ") for cue in LOCATION_CUE_PHRASES):
            return True
        if any(f" {cue} " in normalized_text for cue in LOCATION_CUE_PHRASES):
            return True
        if PIIEngine._count_matching_phrases(lower_words, non_name_terms) >= 2:
            return True
        return False

    @staticmethod
    def _looks_like_company_non_name_phrase(lower_words: list[str]) -> bool:
        if len(lower_words) < 2:
            return False
        keyword_hits = sum(1 for word in lower_words if word in BUSINESS_NAME_KEYWORDS)
        if keyword_hits >= 1:
            return True
        return False

    @staticmethod
    def _normalize_text_phrase(value: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9'\-\s]", " ", value).strip().lower())

    def _should_suppress_name_detection(
        self,
        text: str,
        previous_assistant_message: str | None,
        assistant_name_request_type: str | None,
        non_name_terms: set[str],
    ) -> bool:
        if self._looks_like_assistant_greeting(text, non_name_terms):
            return True

        if assistant_name_request_type is not None:
            return False

        normalized_text = self._normalize_text_phrase(text)
        if not normalized_text:
            return False
        if normalized_text in HARDCODED_NON_NAME_PHRASES:
            return True

        if self._looks_like_non_user_contact_lookup(text):
            return True

        # Keep explicit name self-identification redaction active.
        if any(cue in normalized_text for cue in NAME_CONTEXT_CUES):
            return False
        if KEYED_NAME_VALUE_RE.search(text) or NAME_LABELLED_REPLY_RE.match(text) or NAME_LABELLED_REPLY_ALT_RE.match(text):
            return False

        if EMAIL_RE.search(text) or PHONE_RE.search(text):
            return False

        is_plan_context, is_location_context = self._assistant_non_name_context(previous_assistant_message)
        if not (is_plan_context or is_location_context):
            return False

        words = re.findall(NAME_WORD_PATTERN, text, flags=re.UNICODE)
        if not words or len(words) > 5:
            return False

        if normalized_text in non_name_terms:
            return True

        if self._matches_fuzzy_non_name_term(normalized_text, non_name_terms):
            return True

        lower_words = [word.lower() for word in words]
        if is_plan_context:
            if any(word in FLOOR_PLAN_CODE_TOKENS for word in lower_words):
                return True

        if is_location_context:
            # In location prompts, short 2-4 word replies are usually place names.
            if 2 <= len(words) <= 4:
                return True
            if len(words) == 1 and (lower_words[0] in GEO_REGION_ABBREVIATIONS or lower_words[0] in GEO_REGION_PHRASES):
                return True

        return False

    @staticmethod
    def _looks_like_assistant_greeting(text: str, non_name_terms: set[str]) -> bool:
        words = re.findall(NAME_WORD_PATTERN, text, flags=re.UNICODE)
        if len(words) != 2:
            return False
        greeting = words[0].lower()
        target = words[1].lower()
        if greeting not in ASSISTANT_NAME_GREETINGS:
            return False
        if target in DEFAULT_ASSISTANT_NAME_WORDS:
            return True
        # Allow runtime injection of assistant names through non-name terms.
        if target in non_name_terms:
            return True
        return False

    @staticmethod
    def _assistant_non_name_context(previous_assistant_message: str | None) -> tuple[bool, bool]:
        if not previous_assistant_message:
            return False, False
        normalized = re.sub(r"\s+", " ", previous_assistant_message.strip().lower())
        is_plan_context = any(cue in normalized for cue in ASSISTANT_PLAN_CONTEXT_CUES)
        is_location_context = any(cue in normalized for cue in ASSISTANT_LOCATION_CONTEXT_CUES)
        return is_plan_context, is_location_context

    def _matches_fuzzy_non_name_term(self, normalized_text: str, non_name_terms: set[str]) -> bool:
        words = normalized_text.split()
        if not words or len(words) > 4:
            return False
        if len(words) == 1 and len(words[0]) < 5:
            return False

        threshold = 0.90 if len(words) >= 2 else 0.95
        target_len = len(normalized_text)
        first_char = words[0][0]
        target_word_count = len(words)
        max_len_delta = 3 if target_word_count >= 2 else 2

        for candidate in non_name_terms:
            if not candidate:
                continue
            candidate_words = candidate.split()
            if abs(len(candidate_words) - target_word_count) > 1:
                continue
            if candidate[0] != first_char:
                continue
            if abs(len(candidate) - target_len) > max_len_delta:
                continue
            if SequenceMatcher(None, normalized_text, candidate).ratio() >= threshold:
                return True
        return False

    @staticmethod
    def _is_similar_name_token(candidate: str, reference: str) -> bool:
        if not candidate or not reference:
            return False
        if candidate == reference:
            return True
        if abs(len(candidate) - len(reference)) > 2:
            return False
        return SequenceMatcher(None, candidate, reference).ratio() >= 0.75

    @staticmethod
    def _looks_like_non_user_contact_lookup(text: str) -> bool:
        normalized = re.sub(r"\s+", " ", text.strip().lower())
        if not normalized:
            return False
        return any(normalized.startswith(prefix) for prefix in NON_USER_CONTACT_LOOKUP_PREFIXES)

    def _normalize_non_name_terms(self, terms: list[str] | tuple[str, ...] | None) -> set[str]:
        normalized: set[str] = set()
        for term in terms or ():
            clean = self._normalize_text_phrase(str(term))
            if clean:
                normalized.add(clean)
        return normalized

    def _load_non_name_terms(self, csv_terms: tuple[str, ...], json_path: str) -> set[str]:
        combined = self._normalize_non_name_terms(csv_terms)
        if not json_path:
            return combined

        path = Path(json_path)
        if not path.exists():
            LOGGER.warning("Configured non-name terms JSON path not found: %s", path)
            return combined

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            LOGGER.warning("Failed loading non-name terms JSON %s: %s", path, exc)
            return combined

        def _walk(value: object) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if isinstance(key, str):
                        normalized_key = self._normalize_text_phrase(key)
                        if normalized_key:
                            combined.add(normalized_key)
                    _walk(child)
                return
            if isinstance(value, list):
                for item in value:
                    _walk(item)
                return
            if isinstance(value, str):
                normalized_value = self._normalize_text_phrase(value)
                if normalized_value:
                    combined.add(normalized_value)

        _walk(payload)
        return combined

    @staticmethod
    def _assistant_name_request_type(previous_assistant_message: str | None) -> str | None:
        if not previous_assistant_message:
            return None
        normalized = re.sub(r"\s+", " ", previous_assistant_message.strip().lower())
        has_first = any(cue in normalized for cue in ASSISTANT_FIRST_NAME_REQUEST_CUES)
        has_last = any(cue in normalized for cue in ASSISTANT_LAST_NAME_REQUEST_CUES)
        has_full = any(cue in normalized for cue in ASSISTANT_FULL_NAME_REQUEST_CUES)
        has_first_already = any(cue in normalized for cue in ASSISTANT_FIRST_ALREADY_CAPTURED_CUES)

        if has_last and has_first_already:
            return "last"

        if has_full or (has_first and has_last):
            return "full"
        if has_last:
            return "last"
        if has_first:
            return "first"
        if any(cue in normalized for cue in ASSISTANT_CONTACT_REQUEST_CUES):
            # Contact-only asks ("email or phone?") should not trigger name parsing.
            if "name" in normalized:
                return "full"
            return None
        if any(cue in normalized for cue in ASSISTANT_NAME_REQUEST_CUES):
            return "first"
        return None

    @staticmethod
    def _assistant_has_explicit_name_request(previous_assistant_message: str | None) -> bool:
        if not previous_assistant_message:
            return False
        normalized = re.sub(r"\s+", " ", previous_assistant_message.strip().lower())
        return (
            any(cue in normalized for cue in ASSISTANT_NAME_REQUEST_CUES)
            or any(cue in normalized for cue in ASSISTANT_FIRST_NAME_REQUEST_CUES)
            or any(cue in normalized for cue in ASSISTANT_LAST_NAME_REQUEST_CUES)
            or any(cue in normalized for cue in ASSISTANT_FULL_NAME_REQUEST_CUES)
        )

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

    @staticmethod
    def _extract_name_intro_candidate(value: str) -> tuple[str, int]:
        token_matches = list(re.finditer(r"[A-Za-z][A-Za-z'\-]*", value))
        if not token_matches:
            return "", 0

        prose_break_words = {
            "i",
            "im",
            "i'm",
            "we",
            "my",
            "me",
            "you",
            "he",
            "she",
            "they",
            "work",
            "works",
            "working",
            "at",
            "for",
            "in",
            "on",
            "and",
            "or",
            "but",
            "who",
            "what",
            "where",
            "when",
            "why",
            "how",
            "can",
            "could",
            "would",
            "do",
            "does",
            "did",
            "is",
            "are",
            "was",
            "were",
            "have",
            "has",
            "had",
            "need",
            "just",
        }

        keep_index = -1
        for idx, token_match in enumerate(token_matches):
            token = token_match.group(0).lower()
            if idx > 0 and (token in prose_break_words or token in NON_NAME_SINGLE_WORDS):
                break
            keep_index = idx
            if keep_index >= 2:
                break

        if keep_index < 0:
            return "", 0

        keep_end = token_matches[keep_index].end()
        candidate = value[:keep_end].strip()
        if not candidate:
            return "", 0
        return candidate, keep_end

    def _placeholder_for_span(self, span: Span, vault: PIIVault) -> tuple[str, dict[str, str]]:
        if span.entity_key == "name":
            coordinated_match = re.fullmatch(
                r"\s*([A-Za-z][A-Za-z'\-]*)\s+(?:and|&)\s+([A-Za-z][A-Za-z'\-]*)\s+([A-Za-z][A-Za-z'\-]*)\s*",
                span.value,
            )
            if coordinated_match:
                first_name = coordinated_match.group(1)
                second_name = coordinated_match.group(2)
                shared_last_name = coordinated_match.group(3)
                fn_token = vault.register("fn", first_name)
                mn1_token = vault.register("mn1", second_name)
                ln_token = vault.register("ln", shared_last_name)
                replacements = {
                    fn_token: first_name,
                    mn1_token: second_name,
                    ln_token: shared_last_name,
                }
                return f"{fn_token} and {mn1_token} {ln_token}", replacements

            name_parts = self._split_name_parts(span.value)
            if not name_parts:
                return span.value, {}

            ordered_tokens: list[str] = []
            replacements: dict[str, str] = {}

            for entity in NAME_ENTITY_KEYS:
                value = name_parts.get(entity)
                if value:
                    token = vault.register(entity, value, prefer_latest=span.prefer_latest)
                    ordered_tokens.append(token)
                    replacements[token] = value

            return " ".join(ordered_tokens), replacements

        token = vault.register(span.entity_key, span.value, prefer_latest=span.prefer_latest)
        return token, {token: span.value}

    @staticmethod
    def _split_name_parts(value: str) -> dict[str, str]:
        value = value.strip()
        value = re.sub(r"^(mr|mrs|ms|dr|prof)\.?\s+", "", value, flags=re.IGNORECASE)
        words = [w for w in re.findall(NAME_WORD_PATTERN, value, flags=re.UNICODE) if w.lower() != "and"]

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

        for span in sorted(
            spans,
            key=lambda s: (s.start, 0 if s.entity_key in ENTITY_KEYS else 1, -(s.end - s.start)),
        ):
            if any(occupied[i] for i in range(span.start, span.end)):
                continue
            selected.append(span)
            for i in range(span.start, span.end):
                occupied[i] = True

        return sorted(selected, key=lambda s: s.start)
