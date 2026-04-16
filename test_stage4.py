"""
Test the stage 4 LLM fallback with a fake OpenRouter client.
Exercises the full pipeline (normalize → stage 4 → DB insert) without a
real API key or network call. Run locally before trusting stage 4 on a
paid session.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

import normalizer
from normalizer import (
    normalize_product,
    apply_llm_fallback,
    spec_coverage,
    needs_llm,
)


# Canned responses keyed on product part number — one per known straggler in
# the USB-C fixture. Values reflect what gpt-4o-mini _should_ return given
# the prompt's charging-only-cable heuristic and USB standard table.
FAKE_LLM_ANSWERS = {
    # "Fast charging" / "PD" cables with no data rate mentioned: USB 2.0 = 0.48 Gbps
    "CABUGR50125":   {"gbps": 0.48},
    "CABUGR70429":   {"gbps": 0.48, "max_watts": 60},  # plausible guess
    "CABAPP5922292": {"gbps": 0.48},
    "CABCUK000005":  {"gbps": 0.48},
    "CABUGR70645":   {"gbps": 0.48},
    "CABCUK000008":  {"gbps": 0.48},
    "CABBEL6593822": {"gbps": 0.48},
    "CABUGR10306":   {"max_watts": 60},  # USB 2.0 typical
    "CABAPP6314062": {"gbps": 0.48},
    "CABMOX00042":   {"gbps": 0.48},
    "CABBEL6593831": {"gbps": 0.48},
    "CABBTS1090":    {"gbps": 0.48, "max_watts": 60},
    "CABBAS0003":    {"gbps": 0.48},
    "CABMOX00044":   {"gbps": 0.48},
    "CABMOX00041":   {"gbps": 0.48},
    # CABUNI0082 has gbps=10 already; only max_watts missing
    "CABUNI0082":    {"max_watts": 100},
    # The following were RESOLVED by stage 1-3 improvements, LLM won't see them:
    #   CABUGR50997 (480Mbps → 0.48), ADPUGR10387 & ADPUNI1081 (Gen2)
}


def fake_call_openrouter(api_key, prompt):
    """Pull the product part out of the prompt and look up the canned answer."""
    # Prompt includes "Title: <title>" and each title starts with brand; we
    # key on part number, which actually appears in the Specs block via "Part #".
    import re
    m = re.search(r'"Part #":\s*"([^"]+)"', prompt)
    if not m:
        return json.dumps({})
    part = m.group(1)
    return json.dumps(FAKE_LLM_ANSWERS.get(part, {}))


def main():
    dump = json.loads(Path("fixture_usbc_cables_2026-04-16.json").read_text())
    url = dump["url"]
    rows = [normalize_product(p, url) for p in dump["products"]]

    cov_before = spec_coverage(rows, "cables")
    stragglers_before = needs_llm(rows, "cables")
    print(f"Before stage 4:")
    print(f"  Coverage: {cov_before}")
    print(f"  Stragglers: {len(stragglers_before)}/{len(rows)}")
    print()

    # Inject fake API key so _stage_llm_fallback doesn't short-circuit on env
    with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-fake"}), \
         patch.object(normalizer, "_call_openrouter", fake_call_openrouter):
        stats = apply_llm_fallback(rows, "cables")

    cov_after = spec_coverage(rows, "cables")
    stragglers_after = needs_llm(rows, "cables")
    print(f"After stage 4:")
    print(f"  LLM attempts: {stats['attempted']}, fully filled: {stats['filled']}")
    print(f"  Coverage: {cov_after}")
    print(f"  Stragglers: {len(stragglers_after)}/{len(rows)}")
    print()

    if stragglers_after:
        print("Remaining stragglers (expected: none given canned answers):")
        for r in stragglers_after:
            missing = [f for f in ["gbps", "max_watts", "length_m"] if r.get(f) is None]
            print(f"  [{r['part']}] missing {missing}")
    else:
        print("All required fields populated. Stage 4 plumbing works end-to-end.")

    # Also verify the no-API-key path
    print()
    print("Re-running without OPENROUTER_API_KEY (expected: stage 4 is a no-op):")
    rows2 = [normalize_product(p, url) for p in dump["products"]]
    env = {k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"}
    with patch.dict(os.environ, env, clear=True):
        stats2 = apply_llm_fallback(rows2, "cables")
    print(f"  LLM attempts: {stats2['attempted']}, filled: {stats2['filled']}  "
          f"(both should be 0)")


if __name__ == "__main__":
    main()
