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

def _stage_regex(row: dict, text: str):
    """Extract specs from title/subtitle via regex."""
    t = text.lower()

    # Gbps: "40gbps", "40 gbps", "40gb/s"
    if row.get("gbps") is None:
        m = re.search(r'(\d+(?:\.\d+)?)\s*(?:gbps|gb/s)', t)
        if m:
            row["gbps"] = float(m.group(1))

    # Watts: "100w", "100 watt", "240w"
    if row.get("max_watts") is None:
        m = re.search(r'(\d+)\s*(?:w(?:att)?s?)\b', t)
        if m:
            row["max_watts"] = float(m.group(1))

    # PD wattage: "PD 100W", "PD3.1 240W"
    if row.get("max_watts") is None:
        m = re.search(r'pd\s*(?:\d+(?:\.\d+)?)?\s*(\d+)\s*w', t)
        if m:
            row["max_watts"] = float(m.group(1))

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


def _stage_spec_rows(row: dict, specs: dict):
    """Extract from the extractor's spec rows dict."""
    if not specs:
        return

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

    # Braided
    if row.get("braided") is None:
        val = specs.get("Braided", "").lower()
        if "yes" in val:
            row["braided"] = 1
        elif "no" in val:
            row["braided"] = 0

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

    # Try each standard, longest-match first (sorted by key length desc)
    for standard, values in sorted(TB_STANDARDS.items(), key=lambda x: -len(x[0])):
        if standard in t:
            if row.get("gbps") is None and values.get("gbps") is not None:
                row["gbps"] = values["gbps"]
            if row.get("max_watts") is None and values.get("max_watts") is not None:
                row["max_watts"] = values["max_watts"]
            break  # Use first (longest) match only


# ---------------------------------------------------------------------------
# Stage 4: LLM fallback (stub — implemented in step 3 of build)
# ---------------------------------------------------------------------------

async def _stage_llm_fallback(row: dict, category: str) -> bool:
    """
    Call gpt-4o-mini to extract remaining required fields.
    Returns True if the LLM was called, False if skipped.

    TODO: Implement in build step 3.
    """
    return False


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
    _stage_regex(row, f"{title} {subtitle}")

    # Stage 2: structured spec rows
    _stage_spec_rows(row, specs)

    # Stage 3: spec table lookup
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
