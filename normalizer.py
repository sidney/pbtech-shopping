"""
4-stage normalizer for PB Tech product data.

Stage 1: Regex on title + subtitle for common patterns (40Gbps, 100W, 1.5m)
Stage 2: Structured spec rows from raw_specs dict
Stage 3: Spec table lookup (Thunderbolt/USB standards → gbps, max_watts)
Stage 4: LLM fallback via gpt-4o-mini (only for stragglers, step 3 of build)

Each stage only fills fields left null by previous stages.
"""

import json
import re
from typing import Optional

# ---------------------------------------------------------------------------
# Standard spec lookup tables
# ---------------------------------------------------------------------------

TB_STANDARDS: dict[str, dict] = {
    "thunderbolt 5":      {"gbps": 80,  "max_watts": 240},
    "thunderbolt 4":      {"gbps": 40,  "max_watts": 100},
    "thunderbolt 3":      {"gbps": 40,  "max_watts": 100},
    "usb4 gen 4":         {"gbps": 80,  "max_watts": 240},
    "usb4 gen 3":         {"gbps": 40,  "max_watts": 240},
    "usb4":               {"gbps": 40,  "max_watts": 100},
    "usb 3.2 gen 2x2":    {"gbps": 20,  "max_watts": 100},
    "usb 3.2 gen 2":      {"gbps": 10,  "max_watts": 100},
    "usb 3.2 gen 1":      {"gbps": 5,   "max_watts": None},
    "usb 3.1 gen 2":      {"gbps": 10,  "max_watts": 100},
    "usb 3.1 gen 1":      {"gbps": 5,   "max_watts": None},
    "usb 3.0":            {"gbps": 5,   "max_watts": None},
    "usb 2.0":            {"gbps": 0.48, "max_watts": None},
    "displayport 2.1":    {"gbps": 80,  "max_watts": None},
    "displayport 2.0":    {"gbps": 80,  "max_watts": None},
    "displayport 1.4":    {"gbps": 32.4, "max_watts": None},
    "displayport 1.2":    {"gbps": 21.6, "max_watts": None},
    "hdmi 2.1":           {"gbps": 48,  "max_watts": None},
    "hdmi 2.0":           {"gbps": 18,  "max_watts": None},
}

# Required fields per category — controls LLM fallback trigger
REQUIRED_FIELDS: dict[str, list[str]] = {
    "cables":   ["gbps", "max_watts", "length_m"],
    "monitors": ["resolution_w", "refresh_hz", "screen_inches"],
}

# Category detection from URL path segments
CATEGORY_PATTERNS: list[tuple[str, str]] = [
    ("cable",    "cables"),
    ("adapter",  "cables"),
    ("monitor",  "monitors"),
    ("display",  "monitors"),
]


def detect_category(url: str) -> str:
    """Infer product category from the PB Tech URL path."""
    path = url.lower()
    for pattern, cat in CATEGORY_PATTERNS:
        if pattern in path:
            return cat
    return "other"


# ---------------------------------------------------------------------------
# Stage 1: Regex on title + subtitle
# ---------------------------------------------------------------------------

def _stage_regex(row: dict, text: str, category: str):
    """Extract specs from title/subtitle via regex. Category-gated so that
    monitor-only patterns (Hz, resolution, inches) don't leak into cable rows
    when a cable's marketing copy mentions e.g. "8K 60Hz"."""
    t = text.lower()

    # Gbps: "40gbps", "40 gbps", "40gb/s" — meaningful for both categories
    if row.get("gbps") is None:
        m = re.search(r'(\d+(?:\.\d+)?)\s*(?:gbps|gb/s)', t)
        if m:
            row["gbps"] = float(m.group(1))

    # Mbps: "480mbps" → 0.48 Gbps. UGREEN 50997 advertises 480Mbps in its
    # subtitle (a USB 2.0 speed). Only fires if Gbps regex didn't match.
    if row.get("gbps") is None:
        m = re.search(r'(\d+(?:\.\d+)?)\s*(?:mbps|mb/s)', t)
        if m:
            row["gbps"] = float(m.group(1)) / 1000

    # Watts: "100w", "100 watt", "240w" — meaningful for both (cable PD rating
    # and monitor USB-C PD output are both legitimate uses of max_watts)
    if row.get("max_watts") is None:
        m = re.search(r'(\d+)\s*(?:w(?:att)?s?)\b', t)
        if m:
            row["max_watts"] = float(m.group(1))

    # PD wattage: "PD 100W", "PD3.1 240W"
    if row.get("max_watts") is None:
        m = re.search(r'pd\s*(?:\d+(?:\.\d+)?)?\s*(\d+)\s*w', t)
        if m:
            row["max_watts"] = float(m.group(1))

    if category == "cables":
        # Length: "1m", "1.5m", "2 metre", "0.5 meter"
        if row.get("length_m") is None:
            m = re.search(r'(\d+(?:\.\d+)?)\s*(?:m(?:etre|eter)?s?)\b', t)
            if m:
                val = float(m.group(1))
                if val < 50:  # sanity: avoid matching model numbers
                    row["length_m"] = val

        # Length in cm: "50cm"
        if row.get("length_m") is None:
            m = re.search(r'(\d+)\s*cm\b', t)
            if m:
                row["length_m"] = float(m.group(1)) / 100

    if category == "monitors":
        # Resolution: "3840x2160", "2560 x 1440", "5120x2880"
        if row.get("resolution_w") is None:
            m = re.search(r'(\d{3,5})\s*x\s*(\d{3,5})', t)
            if m:
                row["resolution_w"] = int(m.group(1))
                row["resolution_h"] = int(m.group(2))

        # Refresh rate: "165hz", "144 hz", "60hz"
        if row.get("refresh_hz") is None:
            m = re.search(r'(\d+)\s*hz', t)
            if m:
                row["refresh_hz"] = int(m.group(1))

        # Screen size: '27"', "27 inch", '31.5"'
        if row.get("screen_inches") is None:
            m = re.search(r'(\d+(?:\.\d+)?)\s*(?:"|inch|")', t)
            if m:
                row["screen_inches"] = float(m.group(1))


# ---------------------------------------------------------------------------
# Stage 2: Structured spec rows from raw_specs
# ---------------------------------------------------------------------------

_LENGTH_RE = re.compile(r'(\d+(?:\.\d+)?)\s*(?:m(?:etre|eter)?s?)\b', re.I)
_LENGTH_CM_RE = re.compile(r'(\d+)\s*cm\b', re.I)
_RES_RE = re.compile(r'(\d{3,5})\s*x\s*(\d{3,5})')
_HZ_RE = re.compile(r'(\d+)\s*(?:hz)', re.I)
_INCHES_RE = re.compile(r'(\d+(?:\.\d+)?)')


def _stage_spec_rows(row: dict, specs: dict, category: str):
    """Extract from the extractor's spec rows dict. Category-gated."""
    if not specs:
        return

    if category == "cables":
        # Cable Length
        if row.get("length_m") is None:
            for key in ("Cable Length", "Length", "Cable length"):
                val = specs.get(key, "")
                if val:
                    m = _LENGTH_RE.search(val)
                    if m:
                        v = float(m.group(1))
                        if v < 50:
                            row["length_m"] = v
                        break
                    m = _LENGTH_CM_RE.search(val)
                    if m:
                        row["length_m"] = float(m.group(1)) / 100
                        break

        # Connectors
        if row.get("conn1") is None:
            val = specs.get("Connector 1", specs.get("Connector Type 1", ""))
            if val:
                row["conn1"] = val.strip()

        if row.get("conn2") is None:
            val = specs.get("Connector 2", specs.get("Connector Type 2", ""))
            if val:
                row["conn2"] = val.strip()

        # Braided — exact-word match. Previously "no" in val caught "Not Specified"
        # as braided=0 because "no" is a substring of "not". Fix: compare the
        # trimmed lowercase value against the literal tokens yes/no.
        if row.get("braided") is None:
            val = specs.get("Braided", "").strip().lower()
            if val == "yes":
                row["braided"] = 1
            elif val == "no":
                row["braided"] = 0
            # Any other value (including "Not Specified", "") leaves braided as None.

    if category == "monitors":
        # Monitor: Screen Size
        if row.get("screen_inches") is None:
            val = specs.get("Screen Size", specs.get("Display Size", ""))
            if val:
                m = _INCHES_RE.search(val)
                if m:
                    v = float(m.group(1))
                    if 10 <= v <= 100:
                        row["screen_inches"] = v

        # Monitor: Resolution
        if row.get("resolution_w") is None:
            val = specs.get("Resolution", specs.get("Native Resolution", ""))
            if val:
                m = _RES_RE.search(val)
                if m:
                    row["resolution_w"] = int(m.group(1))
                    row["resolution_h"] = int(m.group(2))

        # Monitor: Refresh Rate
        if row.get("refresh_hz") is None:
            val = specs.get("Refresh Rate", specs.get("Max Refresh Rate", ""))
            if val:
                m = _HZ_RE.search(val)
                if m:
                    row["refresh_hz"] = int(m.group(1))

        # Monitor: Panel Type
        if row.get("panel_type") is None:
            val = specs.get("Panel Type", specs.get("Panel", ""))
            if val:
                row["panel_type"] = val.strip()


# ---------------------------------------------------------------------------
# Stage 3: Spec table lookup
# ---------------------------------------------------------------------------

def _stage_spec_table(row: dict, text: str):
    """Look up known standards in title/subtitle/spec text to fill gaps."""
    t = text.lower()

    # Normalize "gen2" / "gen  2" to "gen 2" so UGREEN's "USB 3.2 Gen2"
    # matches the TB_STANDARDS key "usb 3.2 gen 2".
    t = re.sub(r'gen\s*(\d)', r'gen \1', t)

    # Try each standard, longest-match first (sorted by key length desc)
    for standard, values in sorted(TB_STANDARDS.items(), key=lambda x: -len(x[0])):
        if standard in t:
            if row.get("gbps") is None and values.get("gbps") is not None:
                row["gbps"] = values["gbps"]
            if row.get("max_watts") is None and values.get("max_watts") is not None:
                row["max_watts"] = values["max_watts"]
            break  # Use first (longest) match only


# ---------------------------------------------------------------------------
# Stage 4: LLM fallback (gpt-4o-mini via OpenRouter)
# ---------------------------------------------------------------------------
#
# Only called for rows still missing required fields after stages 1-3.
# Uses urllib (stdlib, no new dependency) to POST to OpenRouter.
#
# Keep the HTTP transport pluggable so tests can monkeypatch `_call_openrouter`
# and so a different provider could be swapped in without touching callers.
#

import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "openai/gpt-4o-mini"
LLM_TIMEOUT_SEC = 15


# Field meanings for the prompt. Kept terse because gpt-4o-mini follows short
# constraints well and token economy matters (~$0.0001/row budget per the design).
_FIELD_HELP = {
    "gbps": (
        "USB/TB data rate in Gbps (float). Reference: USB 2.0=0.48, "
        "USB 3.0/3.1 Gen1=5, USB 3.1/3.2 Gen2=10, USB 3.2 Gen2x2=20, "
        "USB4/TB3/TB4=40, TB5=80, DisplayPort 1.4=32.4."
    ),
    "max_watts": "Max PD wattage (float).",
    "length_m": "Cable length in metres (float).",
    "resolution_w": "Monitor horizontal pixel count (int).",
    "resolution_h": "Monitor vertical pixel count (int).",
    "refresh_hz": "Monitor refresh rate in Hz (int).",
    "screen_inches": "Monitor diagonal screen size in inches (float).",
}


def _build_llm_prompt(row: dict, category: str, missing: list[str]) -> str:
    """Build the gpt-4o-mini prompt for a single straggler row."""
    title = row.get("title") or ""
    subtitle = row.get("subtitle") or ""
    specs = row.get("raw_specs") or "{}"
    field_help = "\n".join(f"  {f}: {_FIELD_HELP.get(f, '')}" for f in missing)
    return (
        "Extract missing PB Tech product specs. Respond with JSON only, no prose.\n\n"
        f"Category: {category}\n"
        f"Title: {title}\n"
        f"Subtitle: {subtitle}\n"
        f"Specs: {specs}\n\n"
        f"Fields to extract: {json.dumps(missing)}\n"
        f"{field_help}\n\n"
        "Heuristic for gbps on USB-C cables: a cable advertised purely for "
        'charging ("fast charge", "PD", high-W rating) with no data rate or '
        "USB version mentioned is conventionally USB 2.0 (gbps=0.48). "
        "Otherwise, return null for any field you cannot determine confidently. "
        "Do not guess data rates that are not supported by the text."
    )


def _call_openrouter(api_key: str, prompt: str) -> str:
    """POST to OpenRouter chat/completions, return the assistant message text.

    Tests should monkeypatch this with a fake that returns a canned JSON string.
    """
    body = json.dumps({
        "model": OPENROUTER_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    req = urllib.request.Request(
        OPENROUTER_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=LLM_TIMEOUT_SEC) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload["choices"][0]["message"]["content"]


def _coerce(field: str, val):
    """Coerce a raw LLM value to the type this field expects. Returns None on
    any mismatch so bad output gets silently dropped rather than corrupting
    the DB row."""
    if val is None:
        return None
    try:
        if field in ("resolution_w", "resolution_h", "refresh_hz"):
            return int(val)
        # Every other numeric field is a float
        return float(val)
    except (TypeError, ValueError):
        return None


def _stage_llm_fallback(row: dict, category: str) -> bool:
    """Fill missing required fields for this row via gpt-4o-mini.

    Returns True iff the LLM was called (row may or may not have changed).
    Returns False if: no missing fields, no API key set, or the call failed.
    The row is mutated in place; callers do not need a return value for that.
    """
    required = REQUIRED_FIELDS.get(category, [])
    missing = [f for f in required if row.get(f) is None]
    if not missing:
        return False

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return False

    prompt = _build_llm_prompt(row, category, missing)
    try:
        content = _call_openrouter(api_key, prompt)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        log.warning("Stage 4 HTTP failure for %s: %s", row.get("part"), e)
        return True  # We tried; don't mark as success but do count the call
    except Exception as e:  # noqa: BLE001  — any unexpected failure is non-fatal
        log.warning("Stage 4 unexpected error for %s: %s", row.get("part"), e)
        return True

    try:
        extracted = json.loads(content)
    except json.JSONDecodeError:
        log.warning("Stage 4 returned non-JSON for %s: %r", row.get("part"),
                    content[:200])
        return True

    if not isinstance(extracted, dict):
        return True

    # Apply only the requested missing fields, coerced to the right type.
    # A null / absent / un-coercible value leaves the field alone.
    for field in missing:
        val = _coerce(field, extracted.get(field))
        if val is not None:
            row[field] = val

    row["llm_normalized"] = 1
    return True


def apply_llm_fallback(rows: list[dict], category: str) -> dict:
    """Run stage 4 on every straggler in `rows`. Returns summary stats:
        { "attempted": N, "filled": M }
    where attempted = rows where the LLM was actually called (so if
    OPENROUTER_API_KEY is missing, attempted will be 0), and filled = rows
    that have ZERO missing fields after the LLM call."""
    required = REQUIRED_FIELDS.get(category, [])
    if not required:
        return {"attempted": 0, "filled": 0}

    # If there are stragglers but no API key, log once (not once per row).
    if not os.environ.get("OPENROUTER_API_KEY"):
        any_missing = any(
            any(r.get(f) is None for f in required) for r in rows
        )
        if any_missing:
            log.warning(
                "OPENROUTER_API_KEY not set — stage 4 skipped for this batch."
            )

    attempted = 0
    filled = 0
    for row in rows:
        missing_before = [f for f in required if row.get(f) is None]
        if not missing_before:
            continue
        if _stage_llm_fallback(row, category):
            attempted += 1
        missing_after = [f for f in required if row.get(f) is None]
        if not missing_after:
            filled += 1
    return {"attempted": attempted, "filled": filled}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize_product(product: dict, category_url: str) -> dict:
    """
    Run all normalization stages on a single extractor product dict.
    Returns a flat dict ready for db.upsert_product().
    """
    category = detect_category(category_url)
    specs = product.get("specs", {})

    # Build the text blob for regex/spec-table stages
    title = product.get("title") or ""
    subtitle = product.get("subtitle") or ""
    spec_text = " ".join(f"{k} {v}" for k, v in specs.items())
    combined_text = f"{title} {subtitle} {spec_text}"

    row = {
        "part": product.get("part"),
        "category": category,
        "title": title,
        "subtitle": subtitle,
        "url": product.get("url"),
        "price": product.get("price_nzd_inc_gst"),
        "raw_specs": json.dumps(specs) if specs else None,
        # Normalized fields start null
        "gbps": None,
        "max_watts": None,
        "length_m": None,
        "conn1": None,
        "conn2": None,
        "braided": None,
        "resolution_w": None,
        "resolution_h": None,
        "refresh_hz": None,
        "panel_type": None,
        "screen_inches": None,
        "llm_normalized": 0,
    }

    # Stage 1: regex on title + subtitle
    _stage_regex(row, f"{title} {subtitle}", category)

    # Stage 2: structured spec rows
    _stage_spec_rows(row, specs, category)

    # Stage 3: spec table lookup (category-agnostic — gbps/W from standards
    # apply to both cable capability and monitor USB-C port capability)
    _stage_spec_table(row, combined_text)

    return row


def spec_coverage(rows: list[dict], category: str) -> dict:
    """
    Report how many rows have each required field populated.
    Returns e.g. {"gbps": "18/20", "max_watts": "15/20", ...}
    """
    required = REQUIRED_FIELDS.get(category, [])
    total = len(rows)
    if total == 0:
        return {}
    coverage = {}
    for field in required:
        filled = sum(1 for r in rows if r.get(field) is not None)
        coverage[field] = f"{filled}/{total}"
    return coverage


def needs_llm(rows: list[dict], category: str) -> list[dict]:
    """Return rows that are missing required fields for their category."""
    required = REQUIRED_FIELDS.get(category, [])
    if not required:
        return []
    return [r for r in rows if any(r.get(f) is None for f in required)]
