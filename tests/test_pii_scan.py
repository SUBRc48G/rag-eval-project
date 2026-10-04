from src.pii_scan import redact_pii


def test_redacts_email():
    text = "Contact me at priya.sharma@example.com for help."
    redacted, found = redact_pii(text)
    assert "priya.sharma@example.com" not in redacted
    assert "[redacted]" in redacted
    assert "EMAIL_ADDRESS" in found


def test_redacts_phone_number():
    text = "Call me at 9876543210 tomorrow."
    redacted, found = redact_pii(text)
    assert "9876543210" not in redacted
    assert "PHONE_NUMBER" in found


def test_leaves_normal_text_unchanged():
    text = "The water cycle is the continuous movement of water."
    redacted, found = redact_pii(text)
    assert redacted == text
    assert found == []


def test_year_range_is_not_treated_as_a_phone_number():
    # src/pii_scan.py deliberately excludes this pattern -- a lifespan/date range
    # like "(1643-1727)" would otherwise get wrongly stripped out of lesson text.
    text = "Isaac Newton lived from (1643-1727) and changed physics forever."
    redacted, found = redact_pii(text)
    assert redacted == text
    assert found == []

def test_empty_text_returns_unchanged_with_nothing_found():
    # redact_pii short-circuits on falsy input instead of running the matchers.
    assert redact_pii("") == ("", [])
    assert redact_pii(None) == (None, [])


def test_redacts_email_and_phone_together_in_one_text():
    # e.g. a filled-in worksheet page that has both on it.
    text = "Name: Riya. Email riya.k@example.com, phone 9876543210."
    redacted, found = redact_pii(text)
    assert "riya.k@example.com" not in redacted
    assert "9876543210" not in redacted
    assert redacted.count("[redacted]") == 2
    assert sorted(found) == ["EMAIL_ADDRESS", "PHONE_NUMBER"]
