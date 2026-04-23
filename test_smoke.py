"""Smoke test: normalizer + DB + query pipeline with mock extractor data."""

import json
import sys
import os

# Ensure we can import from the project
sys.path.insert(0, os.path.dirname(__file__))

from db import get_connection, upsert_product, run_query, format_query_result, reset_db, session_stats
from normalizer import normalize_product, spec_coverage, needs_llm, detect_category

# --- Mock extractor output (thunderbolt cables) ---

MOCK_CABLES = {
    "url": "https://www.pbtech.co.nz/category/cables-and-connectors/cables/thunderbolt-cables",
    "title": "Thunderbolt Cables - PB Tech",
    "count": 5,
    "total": 5,
    "page": 1,
    "pages": 1,
    "spec_fields_seen": ["Cable Length", "Colour", "Connector 1", "Connector 2"],
    "products": [
        {
            "part": "CABCUX9901",
            "title": "Cruxtec Thunderbolt 4 USB-C Cable 40Gbps 100W PD",
            "subtitle": "1m Cable, USB-C Male to USB-C Male, Black",
            "url": "https://www.pbtech.co.nz/product/CABCUX9901",
            "price_nzd_inc_gst": 39.99,
            "specs": {
                "Cable Length": "1m",
                "Connector 1": "USB-C Male",
                "Connector 2": "USB-C Male",
                "Colour": "Black"
            }
        },
        {
            "part": "CABBLK0042",
            "title": "Belkin Connect USB4 Cable 240W",
            "subtitle": "2m, Thunderbolt 4 Compatible, 40Gbps Data Transfer",
            "url": "https://www.pbtech.co.nz/product/CABBLK0042",
            "price_nzd_inc_gst": 69.00,
            "specs": {
                "Cable Length": "2m",
                "Connector 1": "USB Type-C Male",
                "Connector 2": "USB Type-C Male"
            }
        },
        {
            "part": "CABCUX0125",
            "title": "Cruxtec USB-C to DisplayPort 1.4 Cable",
            "subtitle": "1m, Supports 8K@60Hz 4K@144Hz",
            "url": "https://www.pbtech.co.nz/product/CABCUX0125",
            "price_nzd_inc_gst": 29.99,
            "specs": {
                "Cable Length": "1m",
                "Connector 1": "USB-C Male",
                "Connector 2": "DisplayPort Male"
            }
        },
        {
            "part": "CABANC0080",
            "title": "Anker Thunderbolt 5 Cable 80Gbps 240W",
            "subtitle": "1m USB-C to USB-C, Active Cable",
            "url": "https://www.pbtech.co.nz/product/CABANC0080",
            "price_nzd_inc_gst": 89.99,
            "specs": {
                "Cable Length": "1m",
                "Connector 1": "USB-C Male",
                "Connector 2": "USB-C Male"
            }
        },
        {
            "part": "CABNONAME1",
            "title": "Generic USB Type-C Cable",
            "subtitle": "0.5m braided charging cable",
            "url": "https://www.pbtech.co.nz/product/CABNONAME1",
            "price_nzd_inc_gst": 12.99,
            "specs": {
                "Cable Length": "50cm",
                "Connector 1": "USB-C Male",
                "Connector 2": "USB-C Male",
                "Braided": "Yes"
            }
        },
    ],
}

# --- Mock extractor output (monitors) ---

MOCK_MONITORS = {
    "url": "https://www.pbtech.co.nz/category/peripherals/monitors/professional-monitors",
    "title": "Professional Monitors - PB Tech",
    "count": 3,
    "total": 3,
    "page": 1,
    "pages": 1,
    "spec_fields_seen": ["Screen Size", "Resolution", "Refresh Rate", "Panel Type"],
    "products": [
        {
            "part": "MONASU0270",
            "title": "ASUS ProArt PA27JCV 27\" 5K Monitor",
            "subtitle": "5120x2880 60Hz IPS, USB-C 96W PD, HDR10",
            "url": "https://www.pbtech.co.nz/product/MONASU0270",
            "price_nzd_inc_gst": 999.00,
            "specs": {
                "Screen Size": "27\"",
                "Resolution": "5120 x 2880",
                "Refresh Rate": "60Hz",
                "Panel Type": "IPS"
            }
        },
        {
            "part": "MONAOC0165",
            "title": "AOC Q27G2S/D 27\" QHD Gaming Monitor",
            "subtitle": "2560x1440 165Hz IPS, 1ms Response, FreeSync",
            "url": "https://www.pbtech.co.nz/product/MONAOC0165",
            "price_nzd_inc_gst": 349.00,
            "specs": {
                "Screen Size": "27\"",
                "Resolution": "2560 x 1440",
                "Refresh Rate": "165Hz",
                "Panel Type": "IPS"
            }
        },
        {
            "part": "MONDEL3222",
            "title": "Dell UltraSharp U3223QE 31.5\" 4K Monitor",
            "subtitle": "3840x2160 60Hz IPS Black, USB-C Hub",
            "url": "https://www.pbtech.co.nz/product/MONDEL3222",
            "price_nzd_inc_gst": 1199.00,
            "specs": {
                "Screen Size": "31.5\"",
                "Resolution": "3840 x 2160",
                "Refresh Rate": "60Hz",
                "Panel Type": "IPS Black"
            }
        },
    ],
}


def test_category_detection():
    print("=== Category Detection ===")
    tests = [
        ("https://www.pbtech.co.nz/category/cables-and-connectors/cables/thunderbolt-cables", "cables"),
        ("https://www.pbtech.co.nz/category/peripherals/monitors/professional-monitors", "monitors"),
        ("https://www.pbtech.co.nz/category/peripherals/monitors/gaming-monitors", "monitors"),
        ("https://www.pbtech.co.nz/category/cables-and-connectors/adapters/usb-c", "cables"),
        ("https://www.pbtech.co.nz/category/other/stuff", "other"),
    ]
    for url, expected in tests:
        result = detect_category(url)
        status = "OK" if result == expected else f"FAIL (got {result})"
        print(f"  {status}: {url.split('/')[-1]} → {result}")
    print()


def test_normalizer():
    print("=== Normalizer (Cables) ===")
    url = MOCK_CABLES["url"]
    for p in MOCK_CABLES["products"]:
        row = normalize_product(p, url)
        print(f"  {row['part']:12s}  gbps={row['gbps']!s:5s}  W={row['max_watts']!s:5s}  "
              f"m={row['length_m']!s:5s}  conn1={row['conn1'] or '-':20s}  "
              f"braided={row['braided']!s:5s}")

    print()
    print("=== Normalizer (Monitors) ===")
    url = MOCK_MONITORS["url"]
    for p in MOCK_MONITORS["products"]:
        row = normalize_product(p, url)
        print(f"  {row['part']:12s}  res={row['resolution_w']}x{row['resolution_h']}  "
              f"hz={row['refresh_hz']!s:4s}  in={row['screen_inches']!s:5s}  "
              f"panel={row['panel_type'] or '-'}")
    print()


def test_mb_per_second_not_matched_as_mbps():
    """Regression test: SSD subtitles using MB/s / Mb/s (storage throughput,
    megabytes per second) must not be misread by the stage-1 regex as Mbps
    (signalling rate, megabits per second) and stored as decimal Gbps.

    Before the fix on 2026-04-24, the pattern '(?:mbps|mb/s)' matched both
    forms after .lower(), so '1050MB/s' became gbps=1.05 and '2000Mb/s'
    became gbps=2.0. See step-5 finding for the observed misfires.

    Also spot-checks that unambiguous Gbps / Mbps signalling-rate patterns
    still match correctly.
    """
    print("=== MB/s-is-not-Mbps regression ===")
    # Use an SSD-like URL so category detection maps to 'other'. The gbps/Mbps
    # regex branches are category-agnostic, so detection doesn't affect them.
    ssd_url = "https://www.pbtech.co.nz/category/peripherals/hdd-external/portable-ssd"

    # Cases that should NOT match as a signalling rate. Each is taken from
    # or inspired by the 2026-04-24 portable-ssd scrape.
    should_not_match = [
        ("SSD001", "Up to 1050MB/s",                              "Samsung T7 canonical MB/s"),
        ("SSD002", "2000MB/s",                                    "Bare MB/s, no 'up to'"),
        ("SSD003", "2000Mb/s , IP68 Water / Dust Proof",          "ADATA vendor typo: Mb/s meaning MB/s"),
        ("SSD004", "Read / Write Speeds up to 2000MB/s",          "Samsung T9 style"),
        ("SSD005", "Up to 6700MB/s",                              "LaCie TB5 MB/s"),
        ("SSD006", "Up to 800MB/s",                               "SanDisk E30 slower MB/s"),
        ("SSD007", "1000Mb/s - Built-in USB-C Cable",             "ADATA SC740 vendor typo"),
    ]
    for part, subtitle, desc in should_not_match:
        product = {
            "part": part,
            "title": f"Test SSD {part}",
            "subtitle": subtitle,
            "url": f"https://example.com/{part}",
            "price_nzd_inc_gst": 100.0,
            "specs": {},
        }
        row = normalize_product(product, ssd_url)
        status = "OK" if row["gbps"] is None else f"FAIL (got gbps={row['gbps']})"
        print(f"  {status}: {desc} — {subtitle!r}")
        assert row["gbps"] is None, (
            f"Regression: {subtitle!r} produced gbps={row['gbps']} — should stay None"
        )

    # Cases that SHOULD still match — confirms the fix doesn't over-reach
    # and that unambiguous signalling-rate tokens are still picked up.
    should_match = [
        ("CAB001", "USB 3.2 Gen2 (10Gbps) - Read up to 1050MB/s",  10.0,  "Samsung T7: 10Gbps wins, MB/s ignored"),
        ("CAB002", "Transcend ESD410C 1TB USB-C 20Gbps",            20.0,  "Explicit 20Gbps"),
        ("CAB003", "Cable 40Gbps 100W PD",                          40.0,  "Cable 40Gbps"),
        ("CAB004", "USB 2.0 480Mbps",                               0.48,  "USB 2.0 legit Mbps"),
    ]
    for part, text, expected, desc in should_match:
        product = {
            "part": part,
            "title": f"Test {part}",
            "subtitle": text,
            "url": f"https://example.com/{part}",
            "price_nzd_inc_gst": 100.0,
            "specs": {},
        }
        row = normalize_product(product, ssd_url)
        status = "OK" if row["gbps"] == expected else f"FAIL (got {row['gbps']}, wanted {expected})"
        print(f"  {status}: {desc} — {text!r} → gbps={row['gbps']}")
        assert row["gbps"] == expected, (
            f"Regression: {text!r} should match as gbps={expected}, got {row['gbps']}"
        )
    print()


def test_db_pipeline():
    print("=== DB Pipeline ===")
    reset_db()
    conn = get_connection()

    # Ingest cables
    cable_rows = []
    for p in MOCK_CABLES["products"]:
        row = normalize_product(p, MOCK_CABLES["url"])
        cable_rows.append(row)
        upsert_product(conn, row)

    # Ingest monitors
    for p in MOCK_MONITORS["products"]:
        row = normalize_product(p, MOCK_MONITORS["url"])
        upsert_product(conn, row)

    conn.commit()

    stats = session_stats(conn)
    print(f"  Total products: {stats['total_products']}")
    print(f"  By category: {stats['by_category']}")

    # Spec coverage
    cov = spec_coverage(cable_rows, "cables")
    print(f"  Cable spec coverage: {cov}")

    # LLM stragglers
    strag = needs_llm(cable_rows, "cables")
    print(f"  Cable LLM stragglers: {len(strag)} rows")
    if strag:
        for s in strag:
            missing = [f for f in ["gbps", "max_watts", "length_m"] if s.get(f) is None]
            print(f"    {s['part']}: missing {missing}")
    print()

    # Query tests
    print("=== Query: cables >=40Gbps sorted by price ===")
    result = run_query(conn, "SELECT part, title, gbps, max_watts, length_m, price FROM products WHERE category='cables' AND gbps >= 40 ORDER BY price")
    print(format_query_result(result))
    print()

    print("=== Query: monitors >=120Hz ===")
    result = run_query(conn, "SELECT part, title, resolution_w, resolution_h, refresh_hz, screen_inches, price FROM products WHERE category='monitors' AND refresh_hz >= 120 ORDER BY price")
    print(format_query_result(result))
    print()

    print("=== Query: all products under $50 ===")
    result = run_query(conn, "SELECT part, category, title, price FROM products WHERE price < 50 ORDER BY price")
    print(format_query_result(result))
    print()

    print("=== Query: USB-C connectors ===")
    result = run_query(conn, "SELECT part, title, conn1, conn2, gbps, price FROM products WHERE conn1 LIKE '%USB-C%' ORDER BY gbps DESC")
    print(format_query_result(result))
    print()

    # Reject non-SELECT
    print("=== Query: reject non-SELECT ===")
    result = run_query(conn, "DELETE FROM products")
    print(format_query_result(result))
    print()

    conn.close()
    reset_db()
    print("Done. DB cleaned up.")


if __name__ == "__main__":
    test_category_detection()
    test_normalizer()
    test_mb_per_second_not_matched_as_mbps()
    test_db_pipeline()
