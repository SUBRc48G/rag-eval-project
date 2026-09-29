"""Redact unambiguous PII (email address, phone number) from text before it is indexed.

A lightweight equivalent of Tuitor's pii_scan.redact_pii, which runs Presidio (+ a spaCy model) but
only ever uses two of its recognizers. Same contract -- redact_pii(text) -> (redacted_text, found) --
same "[redacted]" marker, same two entities, same 7-digit floor on phone numbers, so an accidentally
left-in phone number or email in a PDF (e.g. a filled-in worksheet) never reaches the vector store.

- PHONE_NUMBER: Presidio's PhoneRecognizer is a thin wrapper over `phonenumbers.PhoneNumberMatcher`;
  this calls the same matcher with the same default regions and strictness (VALID).
- EMAIL_ADDRESS: a regex that must end on a real TLD-shaped suffix (so a sentence-final "." isn't
  swallowed). Presidio also checks the suffix against the public-suffix list; that extra check is
  skipped here.

Difference from Tuitor: a bare year range such as "(1643—1727" is not treated as a phone number.
Checked against Tuitor's redact_pii on all 741 chunks of the indexed library: identical output
except that one chunk.

Like Tuitor's, this does NOT catch names, addresses or dates of birth -- only text that is
unambiguously an email or a phone number.
"""
import re

import phonenumbers

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_PHONE_REGIONS = ("US", "GB", "DE", "FR", "IL", "IN", "CA", "BR")   # Presidio's PhoneRecognizer defaults
MIN_PHONE_DIGITS = 7   # a bare 4-digit year can look like a phone number to the matcher
# "(1643—1727" is 8 digits, so it slips past the floor above and Tuitor's redact_pii would strip a
# lifespan/date range out of lesson text. The one deliberate difference from Tuitor: skip these.
_YEAR_RANGE = re.compile(r"\(?\s*\d{4}\s*[-‐-―]\s*\d{4}\s*\)?")


def _spans(text):
    spans = [(m.start(), m.end(), "EMAIL_ADDRESS") for m in _EMAIL.finditer(text)]
    for region in _PHONE_REGIONS:
        for m in phonenumbers.PhoneNumberMatcher(text, region, leniency=phonenumbers.Leniency.VALID):
            matched = text[m.start:m.end]
            if len(re.sub(r"\D", "", matched)) >= MIN_PHONE_DIGITS and not _YEAR_RANGE.fullmatch(matched):
                spans.append((m.start, m.end, "PHONE_NUMBER"))
    return spans


def redact_pii(text):
    """Returns (redacted_text, found_entity_types). Each match is replaced with [redacted]."""
    if not text:
        return text, []

    # the same number is often matched under several regions -> keep the first/longest of any overlap
    kept, last_end = [], -1
    for start, end, entity in sorted(_spans(text), key=lambda s: (s[0], -s[1])):
        if start >= last_end:
            kept.append((start, end, entity))
            last_end = end
    if not kept:
        return text, []

    for start, end, _ in sorted(kept, reverse=True):
        text = text[:start] + "[redacted]" + text[end:]
    return text, [entity for _, _, entity in sorted(kept, reverse=True)]
