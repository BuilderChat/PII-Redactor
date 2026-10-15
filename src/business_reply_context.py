"""Conservative business-answer ranges, not a general business/person classifier.

Only the latest assistant request supplies context. Ranges describe recognizable
business text; they never authorize an entire reply to bypass PII detection.
"""

from __future__ import annotations

import re
from typing import Literal


BusinessField = Literal["company", "website", "role", "subject"]
_REQUEST = re.compile(r"\b(?:what|which|where|share|provide|tell\s+me)\b", re.I)
_EXAMPLE = re.compile(r"^\s*(?:for\s+example\b|e\.g\.|such\s+as\b)", re.I)
_PERSON_NAME = re.compile(
    r"\b(?:(?:first|last|full|given|family)\s*name|your\s+name)\b", re.I
)
_WEBSITE = re.compile(r"\b(?:website|web\s+site|url|domain)\b", re.I)
_COMPANY = re.compile(
    r"\b(?:company|builder|business|organization)(?:['’]s)?(?:\s+name)?\b", re.I
)
_ROLE = re.compile(r"\b(?:role|job\s+title|position)\b", re.I)
_SUBJECT = re.compile(r"\b(?:assessment|meeting|reach[ -]?out|focus|discuss)\b", re.I)

_ROLE_VALUE = re.compile(
    r"^\s*(?:(?:my\s+(?:role|title|position)\s+is|i(?:\s+am|['’]m)\s+(?:the\s+)?|role\s*:)\s*)?"
    r"(?P<value>(?:(?:assistant|senior|regional)\s+)?"
    r"(?:(?:general|sales|operations|marketing|construction|project|office|account|business\s+development)\s+manager|"
    r"(?:sales|marketing|operations)\s+director|director\s+of\s+(?:operations|sales|marketing)|"
    r"head\s+of\s+(?:operations|sales|marketing)|chief\s+(?:executive|operating|financial|technology)\s+officer|"
    r"(?:vice\s+)?president|owner|founder|ceo|coo|cfo|cto|manager))\b",
    re.I,
)
_COMPANY_WORD = r"(?!(?:my|i|am|is|name|from|with|at|email|phone)\b)[^\W_][\w'’&-]*"
_COMPANY_VALUE = re.compile(
    r"^\s*(?:(?:our\s+)?(?:company|business)(?:\s+name)?\s*(?:is|:|['’]s)\s*)?"
    rf"(?P<value>(?:{_COMPANY_WORD}\s+){{1,7}}"
    r"(?:homes|builders|construction|software|technologies|solutions|realty|properties|consulting|design|development)"
    r"(?:\s+(?:llc|inc\.?|ltd\.?|limited|corporation))?)\b",
    re.I | re.UNICODE,
)
_WEBSITE_VALUE = re.compile(
    r"(?<![\w@])(?:https?://|www\.)?[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*\.[a-z]{2,}"
    r"(?::\d+)?(?:[/?#][^\s,;]*)?",
    re.I,
)
_WEBSITE_PREFIX = re.compile(
    r"^\s*(?:(?:(?:my|our|the)\s+)?(?:company(?:['’]s)?\s+)?"
    r"(?:website|url|domain)\s*(?:is|:)\s*)?",
    re.I,
)
_TOPIC = (
    r"online\s+sales|lead[ -]+follow[ -]+up|self[ -]+guided\s+tours|"
    r"build\s+communication|customer\s+(?:care|service)|gap\s+assessment|"
    r"website(?:\s+(?:improvements?|development|design))?"
)
_SUBJECT_VALUE = re.compile(
    r"^\s*(?:(?:we|i)\s+(?:want|would\s+like)\s+to\s+(?:improve|discuss|focus\s+on)\s+)?"
    rf"(?P<value>(?:{_TOPIC})(?:\s*(?:,|and|&)\s*(?:{_TOPIC}))*)\b",
    re.I,
)


def requested_business_field(
    previous_assistant_message: str | None,
) -> BusinessField | None:
    clauses = re.split(r"(?<=[.!?])\s+|\n+", previous_assistant_message or "")
    requests = [
        clause
        for clause in clauses
        if ("?" in clause or _REQUEST.search(clause))
        and not (_EXAMPLE.search(clause) and not _REQUEST.search(clause))
    ]
    if not requests:
        return None
    question = requests[-1]
    request = _REQUEST.search(question)
    if request:
        # An acknowledgment can mention a previously collected first name in
        # the same sentence. Interpret the request, not that earlier mention.
        question = question[request.start() :]
    if _PERSON_NAME.search(question):
        return None
    if _WEBSITE.search(question):
        return "website"
    if _ROLE.search(question):
        return "role"
    if _COMPANY.search(question):
        return "company"
    if _SUBJECT.search(question):
        return "subject"
    return None


def business_answer_ranges(
    text: str, field: BusinessField | None
) -> tuple[tuple[int, int], ...]:
    if field is None:
        return ()
    if field == "website":
        prefix = _WEBSITE_PREFIX.match(text)
        if prefix is None:
            return ()
        match = _WEBSITE_VALUE.match(text, prefix.end())
        return ((match.start(), match.end()),) if match else ()
    pattern = {
        "role": _ROLE_VALUE,
        "company": _COMPANY_VALUE,
        "subject": _SUBJECT_VALUE,
    }[field]
    match = pattern.match(text)
    return ((match.start("value"), match.end("value")),) if match else ()
