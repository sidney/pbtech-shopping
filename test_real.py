"""
Run the stage 1-3 normalizer against a real PB Tech extractor JSON dump
and report coverage + any stragglers that would need stage 4.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from normalizer import (
    normalize_product,
    spec_coverage,
    needs_llm,
    detect_category,
    REQUIRED_FIELDS,
)


def main():
    dump = json.loads(Path("fixture_tb_cables_2026-04-16.json").read_text())
    category_url = dump["url"]
    category = detect_category(category_url)

    print(f"Source:   {category_url}")
    print(f"Category: {category}")
    print(f"Products: {len(dump['products'])}")
    print()

    rows = [normalize_product(p, category_url) for p in dump["products"]]

    # Required field coverage
    print("=" * 72)
    print("Required-field coverage")
    print("=" * 72)
    cov = spec_coverage(rows, category)
    for field, ratio in cov.items():
        print(f"  {field:15s} {ratio}")
    print()

    # Per-product detail table
    print("=" * 72)
    print("Per-product normalized values")
    print("=" * 72)
    required = REQUIRED_FIELDS.get(category, [])
    header_cols = ["part"] + required + ["conn1", "braided"]
    print("  " + " | ".join(f"{h:>14s}" for h in header_cols))
    print("  " + "-+-".join("-" * 14 for _ in header_cols))
    for r in rows:
        def fmt(v):
            if v is None:
                return "NULL"
            if isinstance(v, float):
                return f"{v:g}"
            return str(v)
        vals = [fmt(r.get(h)) for h in header_cols]
        print("  " + " | ".join(f"{v:>14s}" for v in vals))
    print()

    # Stragglers
    print("=" * 72)
    print("Stragglers (would invoke stage-4 LLM fallback)")
    print("=" * 72)
    stragglers = needs_llm(rows, category)
    if not stragglers:
        print("  None. Stages 1-3 covered every required field.")
    else:
        print(f"  {len(stragglers)}/{len(rows)} need LLM fallback")
        print()
        for r in stragglers:
            missing = [f for f in required if r.get(f) is None]
            print(f"  [{r['part']}] missing: {missing}")
            print(f"    title: {r['title']}")
            print(f"    subtitle: {r['subtitle']}")
            print(f"    specs: {r['raw_specs']}")
            print()


if __name__ == "__main__":
    main()
