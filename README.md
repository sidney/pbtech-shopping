# pbtech-shopping

Session-scoped scrape-normalize-query toolkit for filtering PB Tech's catalog
on cross-spec criteria their own search doesn't support (e.g. "cables ≥40Gbps
AND ≥100W PD under $80").

## Architecture

Three thin MCP tools, orchestration in chat (Architecture B):

1. **`pbtech_scrape(category_url, extractor_json)`** — normalizes and stores
   extractor output in session SQLite. Claude drives the browser (Playwright
   MCP, Claude for Chrome, etc.) and passes the result here. Returns count,
   spec coverage stats, straggler count, and session DB total.

2. **`pbtech_query(sql, limit=20)`** — arbitrary SELECT against session DB.
   Compact pipe-delimited output with abbreviated column names. Hard cap 100
   rows. Non-SELECT rejected.

3. **`pbtech_session_reset()`** — drops DB. No confirmation needed.

### Products table columns

```
part, category, title, subtitle, url, price (NZD inc GST),
gbps, max_watts, length_m, conn1, conn2, braided,
resolution_w, resolution_h, refresh_hz, panel_type, screen_inches,
raw_specs (JSON), llm_normalized, scraped_at
```

### Normalizer stages

1. Regex on title + subtitle (`40Gbps`, `100W`, `1.5m`, `2560x1440`, `165Hz`)
2. Structured spec rows from extractor (`Cable Length`, `Connector 1/2`, etc.)
3. Spec table lookup (`Thunderbolt 5` → gbps=80 max_watts=240, etc.)
4. gpt-4o-mini fallback for stragglers (not yet implemented — build step 3)

## Setup

```bash
cd ~/pbtech-shopping
pip install -r requirements.txt
```

### Claude Desktop config

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "pbtech-shopping": {
      "command": "python3",
      "args": ["/Users/sidney/pbtech-shopping/server.py"]
    }
  }
}
```

Restart Claude Desktop after editing.

## Usage pattern (in Claude Desktop chat)

Typical session — Claude orchestrates the browser and tools:

1. Navigate to a PB Tech category URL via Playwright MCP
2. Run `pbtech-extract-listing.js` via `browser_run_code`
3. Pass the extractor JSON to `pbtech_scrape`
4. If multi-page, repeat 1-3 for remaining pages
5. Query with `pbtech_query`: `SELECT part, title, gbps, max_watts, length_m, price FROM products WHERE gbps >= 40 AND max_watts >= 100 ORDER BY price`
6. Refine queries based on results
7. `pbtech_session_reset` when switching to a different product search

## Session database

SQLite at `~/.cache/pbtech/session.db`. Constructed anew per shopping task —
no cross-session persistence, no staleness logic. Data changes more frequently
than tool usage.

## Known category URLs

From `pbtech-extractor-README.md` (co-located with extractor at
`~/scripts/pbtech/`):

- `/category/cables-and-connectors/cables/thunderbolt-cables`
- `/category/cables-and-connectors/cables/usb-c-cables`
- `/category/peripherals/monitors/professional-monitors`
- `/category/peripherals/monitors/gaming-monitors`

## v0 limitations

- Connector strings not normalized to enums (use `LIKE '%USB-C%'` in queries)
- Mixed cable/monitor columns in one table (fine at <500 rows)
- LLM fallback not yet implemented (stragglers have null required fields)
- No pagination automation (Claude must navigate + extract each page)
- `pbtech_scrape` accepts pre-extracted JSON rather than driving its own
  browser (v0 simplification — avoids Playwright dependency and Cloudflare WAF)

## Build roadmap

- [x] Step 1: SQLite schema + three tool stubs
- [ ] Step 2: Test normalizer against real PB Tech extractor output
- [ ] Step 3: LLM fallback via gpt-4o-mini (OpenRouter)
- [ ] Step 4: Pagination convenience (detect multi-page, prompt user)
- [ ] Step 5: First real shopping session
