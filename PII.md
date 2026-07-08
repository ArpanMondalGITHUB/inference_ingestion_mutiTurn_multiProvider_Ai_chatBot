# PII Redaction — A Complete Beginner-Friendly Tutorial

This document explains **PII redaction** for this project from zero. It is written so a
**non-technical person** can understand *what it is and why it matters*, and so a developer
can copy the exact code changes needed. Nothing here is hard — the whole feature touches
essentially **one file** (`server/src/sdk/llm_event_tracker.py`) plus one small new helper file.

---

## Part 1 — The idea, in plain English

### What is "PII"?

**PII = Personally Identifiable Information.** It's any piece of data that can be used to
identify a real person. Think of the kind of thing you'd be uncomfortable seeing printed on
a public noticeboard next to someone's name:

- Email addresses — `rahul.sharma@gmail.com`
- Phone numbers — `+91 98765 43210`
- Credit/debit card numbers — `4111 1111 1111 1111`
- Government IDs — Aadhaar, PAN, SSN (`123-45-6789`)
- API keys / passwords / secret tokens — `sk-abc123...`
- Home addresses, IP addresses, etc.

### What is "redaction"?

**Redaction** = hiding or removing sensitive parts of a piece of text, while keeping the
rest readable. You've seen it in movies where a document has **black bars** over names. Same
idea, but instead of a black bar we replace the sensitive text with a **label**:

```
Before:  "Hi, my card is 4111 1111 1111 1111 and email is rahul@gmail.com"
After:   "Hi, my card is [REDACTED_CARD] and email is [REDACTED_EMAIL]"
```

The message still makes sense ("the user gave a card and an email"), but the **actual secret
values are gone**.

### Why does THIS project need it?

Your app is a chat app that also **logs telemetry** for every LLM call. Look at what your
tracker stores today (`server/src/sdk/llm_event_tracker.py`):

- `inputPreview` — the first ~300 characters of **what the user typed**
- `outputPreview` — the first ~300 characters of **what the AI replied**
- `errorMessage` — provider error text (can accidentally echo the user's input)

These previews get sent to `/llm-events` and saved in your SQLite database **forever**. So if
a user pastes "my card number is 4111 1111 1111 1111", that card number is now sitting in your
database in plain text. That is exactly the kind of data you do **not** want to keep.

> **Important nuance:** today the code only *truncates* previews (cuts them to 300 chars). Cutting
> text short is **not** redaction — a card number in the first 20 characters survives truncation
> perfectly. Redaction is about **masking the sensitive content**, not shortening it.

### The one-line summary

> **PII redaction = before we save any user/AI text into our logs, we scan it for things that
> look like emails, phone numbers, cards, IDs, and secrets, and replace them with safe labels
> so no real personal data ever reaches the database.**

---

## Part 2 — Where PII lives in your code (the "where to use it")

Every piece of free-form text that leaves the chat and enters the **logging** path is a risk.
In your codebase that is exactly three fields, and they all flow through the tracker:

| Field | Set where | Contains |
|---|---|---|
| `inputPreview` | `_preview(input_text)` | what the **user** typed |
| `outputPreview` | `_preview(...output...)` | what the **AI** replied |
| `errorMessage` | `str(error)` | provider error (may echo user text) |

The beautiful part: **`inputPreview` and `outputPreview` both go through one single function —
`_preview()`** — at the bottom of `llm_event_tracker.py`. That function is the perfect
**chokepoint**: if we redact inside `_preview`, both previews are covered automatically. We only
need to additionally handle `errorMessage`, which is a one-line change.

So the answer to *"where do I use it?"* is:

1. Inside `_preview()` (covers input + output previews) ✅ main change
2. On the `errorMessage` value before it's stored ✅ tiny change

That's it. The redaction happens **before `_send_soon()`**, so **no unredacted text is ever
transmitted or stored**. The chat response the user sees is untouched — we only clean the
**copy** that goes into logs.

---

## Part 3 — How it works (the mechanism)

We use **pattern matching** (called "regular expressions" or "regex"). A regex is just a rule
that describes the *shape* of something. For example:

- "some letters/numbers, then an `@`, then a domain, then a dot and 2+ letters" → that's the
  **shape of an email**, so anything matching it gets replaced with `[REDACTED_EMAIL]`.
- "13–16 digits, maybe separated by spaces or dashes" → **shape of a card number** →
  `[REDACTED_CARD]`.

We don't need to know *whose* email it is — we just recognize the shape and mask it. This is
fast, needs **zero external libraries**, and runs in microseconds.

### Two approaches (a decision for you)

| Approach | Pros | Cons |
|---|---|---|
| **Regex-only** (this tutorial) | Zero dependencies, instant, easy to read/test, good enough for the common cases (email, phone, card, SSN, IP, keys) | Won't catch *names* or unusual formats |
| **Library (Microsoft Presidio / scrubadub)** | Catches names, locations, better recall | Heavy dependency, slower, more setup |

**Recommendation:** start with **regex-only**. It covers the high-risk structured data (cards,
emails, phones, IDs, secrets) which is exactly what matters for an assessment. You can mention
Presidio as a "future improvement." This tutorial implements the regex approach.

---

## Part 4 — The code changes (step by step)

### Step 1 — Create a new file: `server/src/core/redaction.py`

This is the whole redaction engine. It's self-contained and has no dependencies beyond Python's
built-in `re` module.

```python
# server/src/core/redaction.py
"""
Lightweight, dependency-free PII redaction.

Scans free-form text for common personally identifiable information (email,
phone, card numbers, government IDs, IPs, secrets) and replaces each match with
a safe label, so raw PII never reaches the telemetry store.

Order matters: the most specific / greedy patterns (cards, keys) run before the
looser ones (phone) so a card number isn't half-eaten by the phone rule.
"""
import re

# Each entry: (compiled pattern, replacement label).
# Patterns are intentionally conservative — better to occasionally miss than to
# mangle normal text, but the common high-risk shapes are all covered.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Emails: user@domain.tld
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
     "[REDACTED_EMAIL]"),

    # Credit/debit cards: 13–16 digits, optionally split by spaces or dashes.
    (re.compile(r"\b(?:\d[ -]*?){13,16}\b"),
     "[REDACTED_CARD]"),

    # US SSN: 123-45-6789
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
     "[REDACTED_SSN]"),

    # Indian PAN: ABCDE1234F
    (re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"),
     "[REDACTED_PAN]"),

    # Aadhaar: 12 digits in 4-4-4 groups
    (re.compile(r"\b\d{4}\s\d{4}\s\d{4}\b"),
     "[REDACTED_AADHAAR]"),

    # Bearer / API keys / long secret tokens (sk-..., ghp_..., 20+ char blobs)
    (re.compile(r"\b(?:sk|pk|ghp|gho|xox[baprs])[-_][A-Za-z0-9]{16,}\b"),
     "[REDACTED_SECRET]"),

    # Phone numbers: optional +country, then 10–13 digits with spaces/dashes.
    # Runs LATE so it doesn't swallow card/SSN/aadhaar already handled above.
    (re.compile(r"(?<!\w)(?:\+?\d{1,3}[ -]?)?(?:\d[ -]?){9,12}\d(?!\w)"),
     "[REDACTED_PHONE]"),

    # IPv4 addresses
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
     "[REDACTED_IP]"),
]


def redact(text: str | None) -> str | None:
    """Return `text` with all recognized PII replaced by labels.

    Returns the input unchanged if it is None/empty. Never raises — redaction
    must never break the caller (logging is best-effort).
    """
    if not text:
        return text

    try:
        for pattern, label in _PATTERNS:
            text = pattern.sub(label, text)
    except Exception:
        # If anything ever goes wrong, prefer dropping the text over leaking it.
        return "[REDACTION_ERROR]"

    return text
```

### Step 2 — Use it inside the tracker

Open `server/src/sdk/llm_event_tracker.py` and make these small edits.

**2a. Add the import** near the top (with the other imports):

```python
from core.redaction import redact
```

**2b. Redact inside `_preview()`** — the single chokepoint that covers **both** `inputPreview`
and `outputPreview`. Change the bottom helper from:

```python
def _preview(value: str | None, max_length: int = 300) -> str | None:
    if not value:
        return None

    clean = " ".join(value.split())
    if len(clean) <= max_length:
        return clean

    return clean[: max_length - 3] + "..."
```

to (redact **first**, then collapse whitespace, then truncate):

```python
def _preview(value: str | None, max_length: int = 300) -> str | None:
    if not value:
        return None

    # Redact PII BEFORE truncation so secrets can't hide past the cut-off.
    clean = " ".join(redact(value).split())
    if len(clean) <= max_length:
        return clean

    return clean[: max_length - 3] + "..."
```

> Why redact before truncating? If you truncate first, a card number sitting at character 305
> would be cut off *by luck*, but one at character 5 would survive. Redacting first guarantees
> every occurrence is masked regardless of position.

**2c. Redact `errorMessage`** in the two `except` blocks. Change:

```python
                errorMessage=str(error),
```

to:

```python
                errorMessage=redact(str(error)),
```

(There are two spots — one in `track()` and one in `track_stream()`. Update both.)

That's the entire feature. **Three edits + one new file.** No other file changes.

### Step 3 (optional but recommended) — Make it toggleable

Good practice: let redaction be turned off via an env var (e.g. for local debugging). Your config
lives in `server/src/core/config.py`. Add a setting there (following the pattern of the existing
`LLM_LOGGING_ENABLED`):

```python
# in core/config.py, alongside the other settings
PII_REDACTION_ENABLED = os.getenv("PII_REDACTION_ENABLED", "true").lower() == "true"
```

Then guard the helper in `redaction.py`:

```python
from core.config import PII_REDACTION_ENABLED

def redact(text: str | None) -> str | None:
    if not text or not PII_REDACTION_ENABLED:
        return text
    ...
```

Document it in your README's environment table:

| Variable | Purpose | Default |
|---|---|---|
| `PII_REDACTION_ENABLED` | Mask emails/phones/cards/IDs/secrets in logged previews | `true` |

> **Default it to `true`.** Redaction should be on unless someone deliberately turns it off.

---

## Part 5 — How to test it (prove it works)

### Quick manual check (Python REPL / a scratch script)

```python
from core.redaction import redact

print(redact("email me at rahul.sharma@gmail.com"))
# -> "email me at [REDACTED_EMAIL]"

print(redact("card 4111 1111 1111 1111 pin 1234"))
# -> "card [REDACTED_CARD] pin 1234"

print(redact("call +91 98765 43210 tomorrow"))
# -> "call [REDACTED_PHONE] tomorrow"

print(redact("nothing sensitive here"))
# -> "nothing sensitive here"   (unchanged)
```

### Automated unit test (recommended for the assessment)

Create `server/tests/test_redaction.py`:

```python
from core.redaction import redact


def test_email_is_masked():
    assert "@" not in redact("reach me at a.b@example.com")
    assert "[REDACTED_EMAIL]" in redact("reach me at a.b@example.com")


def test_card_is_masked():
    out = redact("my card 4111 1111 1111 1111")
    assert "4111" not in out
    assert "[REDACTED_CARD]" in out


def test_plain_text_is_untouched():
    assert redact("hello world") == "hello world"


def test_none_and_empty_are_safe():
    assert redact(None) is None
    assert redact("") == ""
```

Run it:

```bash
cd server
poetry run pytest tests/test_redaction.py -v
```

### End-to-end check (the real proof)

1. Start the app (`docker compose up --build` or run locally).
2. In the chat, send a message containing a fake card: *"my test card is 4111 1111 1111 1111"*.
3. Query the stored event: `GET /llm-events` (or open the SQLite DB).
4. Confirm the `inputPreview` shows `[REDACTED_CARD]` and **not** the digits. ✅

---

## Part 6 — Recap / cheat-sheet

**What:** Mask personal data (emails, phones, cards, IDs, secrets) so it never lands in logs.

**Why:** Your tracker copies user + AI text into `inputPreview` / `outputPreview` /
`errorMessage`, which are stored in SQLite forever. Truncation ≠ protection.

**Where:** All three fields flow through the tracker. `_preview()` is the single chokepoint for
both previews; `errorMessage` gets one extra call.

**How:** A tiny `core/redaction.py` using regex pattern-matching. Zero dependencies.

**The changes (total):**
1. New file `server/src/core/redaction.py` (the `redact()` function).
2. `import` it in `llm_event_tracker.py`.
3. Wrap the value inside `_preview()` with `redact(...)`.
4. Wrap `errorMessage=str(error)` with `redact(...)` in both `except` blocks.
5. *(Optional)* `PII_REDACTION_ENABLED` env toggle + README note.
6. *(Optional)* unit tests in `server/tests/test_redaction.py`.

**Time to implement:** ~30–45 minutes including tests. This is the smallest of your remaining
deliverables and is fully self-contained — nothing else in the app needs to change.

---

## Part 7 — Honest limitations (good to mention in your writeup)

- **Regex misses names and free-form addresses.** "My name is Rahul and I live in Bandra" won't
  be caught. A library like **Microsoft Presidio** would, at the cost of a heavy dependency — a
  reasonable "future improvement" line.
- **Over-redaction is possible.** A long order number could match the card/phone shape and get
  masked. For a telemetry preview that's an acceptable trade (safer to over-mask than to leak).
- **Redaction is one-way.** The original text is gone from logs by design — that's the point.
  The user still sees their real, unredacted chat; only the *logged copy* is masked.
