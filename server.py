"""
pbtech-shopping: session-scoped scrape-normalize-query MCP server.

Three tools for filtering PB Tech's catalog on cross-spec criteria
their own search doesn't support (e.g. "cables >=40Gbps AND >=100W under $80").

Architecture B: orchestration in chat. Claude drives the browser (via Playwright
MCP or similar), passes extractor JSON to pbtech_scrape for normalization and
storage, then queries via pbtech_query with arbitrary SQL.

Run: python server.py (stdio transport for Claude Desktop)
"""

import json
import logging
from mcp.server.fastmcp import FastMCP

from db import get_connection, upsert_product, run_query, format_query_result
from db import reset_db, session_stats
from normalizer import normalize_product, spec_coverage, needs_llm, apply_llm_fallback

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pbtech-shopping")

mcp = FastMCP(
    "pbtech-shopping",
    version="0.1.0",
)


@mcp.tool()
def pbtech_scrape(category_url: str, extractor_json: str) -> str:
    """Normalize and store PB Tech product data from the JS extractor output.

    Workflow: (1) navigate to category_url in Playwright MCP, (2) run the
    extractor JS via browser_run_code or browser_evaluate, (3) pass the
    resulting JSON string here. This tool normalizes specs (regex, spec rows,
    standards lookup, and LLM fallback for stragglers) and inserts into the
    session SQLite database.

    Args:
        category_url: The PB Tech category URL that was scraped (used for
            category detection: cables, monitors, etc.)
        extractor_json: Raw JSON string output from pbtech-extract-listing.js.
            Expected shape: {url, count, products: [{part, title, subtitle,
            url, price_nzd_inc_gst, specs: {}}], spec_fields_seen: []}
    """
    try:
        data = json.loads(extractor_json)
    except json.JSONDecodeError as e:
        return f"ERROR: Invalid JSON — {e}"

    if "error" in data:
        return f"Extractor error: {data['error']}"

    products = data.get("products", [])
    if not products:
        return "No products in extractor output."

    # Normalize all products (stages 1-3)
    normalized = []
    for p in products:
        row = normalize_product(p, category_url)
        normalized.append(row)

    # Stage 4: LLM fallback for any rows still missing required fields.
    # No-op if OPENROUTER_API_KEY isn't set (logged, not raised).
    from normalizer import detect_category
    category = detect_category(category_url)
    llm_stats = apply_llm_fallback(normalized, category)

    # Insert into DB
    conn = get_connection()
    try:
        for row in normalized:
            upsert_product(conn, row)
        conn.commit()
    finally:
        conn.close()

    # Post-stage-4 coverage and remaining stragglers
    coverage = spec_coverage(normalized, category)
    stragglers = needs_llm(normalized, category)

    conn = get_connection()
    try:
        stats = session_stats(conn)
    finally:
        conn.close()

    # Pagination info from extractor
    pag = ""
    if data.get("pages") and data["pages"] > 1:
        pag = f"  page {data.get('page')}/{data['pages']} ({data.get('total')} total products)"

    lines = [
        f"Ingested {len(normalized)} products ({category}).{pag}",
        f"Spec coverage: {json.dumps(coverage)}" if coverage else "No required fields for this category.",
    ]
    if llm_stats["attempted"] > 0:
        lines.append(
            f"LLM fallback: called on {llm_stats['attempted']} row(s), "
            f"fully resolved {llm_stats['filled']}."
        )
    if stragglers:
        lines.append(
            f"Still missing required fields: {len(stragglers)} row(s). "
            f"These rows have NULLs in the DB."
        )
    else:
        lines.append("All required fields populated.")
    lines.append(
        f"Session DB total: {stats['total_products']} products across "
        f"{len(stats['by_category'])} categories."
    )

    # List spec fields seen (helps Claude know what to filter on)
    fields = data.get("spec_fields_seen", [])
    if fields:
        lines.append(f"Spec fields seen: {', '.join(fields)}")

    return "\n".join(lines)


@mcp.tool()
def pbtech_query(sql: str, limit: int = 20) -> str:
    """Run a read-only SQL query against the session product database.

    The products table has these columns:
        part, category, title, subtitle, url, price (NZD inc GST),
        gbps, max_watts, length_m, conn1, conn2, braided,
        resolution_w, resolution_h, refresh_hz, panel_type, screen_inches,
        raw_specs (JSON), llm_normalized, scraped_at

    Only SELECT queries are allowed. Results are returned as a compact
    pipe-delimited table with abbreviated headers. Hard cap: 100 rows.

    Args:
        sql: A SELECT query. Use LIKE for connector matching (not normalized
            to enums). Example: WHERE conn1 LIKE '%USB-C%' AND gbps >= 40
        limit: Max rows to return (default 20, hard cap 100).
    """
    conn = get_connection()
    try:
        result = run_query(conn, sql, limit)
        return format_query_result(result)
    finally:
        conn.close()


@mcp.tool()
def pbtech_session_reset() -> str:
    """Drop and recreate the session database. No confirmation needed.

    Use between shopping tasks or when switching to a different product
    search. The DB is session-scoped and ephemeral by design.
    """
    reset_db()
    return "Session database reset. Ready for new scrapes."


if __name__ == "__main__":
    mcp.run(transport="stdio")
