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

1. Regex on title + subtitle (`40Gbps`, `480Mbps`, `100W`, `1.5m`, `2560x1440`,
   `165Hz`). Monitor-only patterns (resolution, Hz, inches) are category-gated
   so they don't contaminate cable rows.
2. Structured spec rows from extractor (`Cable Length`, `Connector 1/2`, etc.).
   The `Braided` value is matched exactly against `yes`/`no` — "Not Specified"
   leaves the field NULL rather than being misread as not-braided.
3. Spec table lookup (`Thunderbolt 5` → gbps=80 max_watts=240, etc.). "Gen2"
   and "Gen 2" are both accepted.
4. gpt-4o-mini fallback via OpenRouter for rows still missing required fields.
   Requires `OPENROUTER_API_KEY` in the server's environment; a no-op with a
   single warning if unset. Row gets `llm_normalized=1` if the LLM returned
   usable values. Prompt includes a "charging-only USB-C cable → USB 2.0"
   heuristic and explicitly instructs the model to return null for unknowns.

## Setup

```bash
cd ~/pbtech-shopping
pip install -r requirements.txt
export OPENROUTER_API_KEY=sk-or-v1-...   # optional; stage 4 is a no-op without it
```

### Claude Desktop config

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "pbtech-shopping": {
      "command": "python3",
      "args": ["/Users/sidney/pbtech-shopping/server.py"],
      "env": {
        "OPENROUTER_API_KEY": "sk-or-v1-..."
      }
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

Top-level category pages redirect to hub pages with sub-categories. The actual
listing URLs one level deeper are:

- `/category/peripherals/cables/thunderbolt-cables` (listing, ~20 products)
- `/category/peripherals/cables/usb-c-cables/usb-c-usb-c-cables` (~410 products, 21 pages)
- `/category/peripherals/cables/usb-c-cables/usb-c-displayport-cables`
- `/category/peripherals/cables/usb-c-cables/usb-c-hdmi-cables`
- `/category/peripherals/monitors/professional-monitors`
- `/category/peripherals/monitors/gaming-monitors`

See also `pbtech-extractor-README.md` (co-located with extractor at
`~/scripts/pbtech/`).

## Tests

- `test_smoke.py` — mock cables + monitors, exercises DB pipeline
- `test_real.py [fixture.json]` — runs normalizer stages 1-3 against a real
  extractor dump and reports coverage + stragglers
- `test_stage4.py` — monkeypatches `_call_openrouter` to verify stage 4
  plumbing (type coercion, partial results, no-key path) without hitting the
  real API

Fixtures:
- `fixture_tb_cables_2026-04-16.json` — Thunderbolt cables, 20 products,
  stages 1-3 achieve 20/20 coverage
- `fixture_usbc_cables_2026-04-16.json` — USB-C to USB-C cables (page 1 of 21),
  stages 1-3 achieve 5/15/20 on gbps/max_watts/length_m; 18 stragglers need
  stage 4

## v0 limitations

- Connector strings not normalized to enums (use `LIKE '%USB-C%'` in queries)
- Mixed cable/monitor columns in one table (fine at <500 rows)
- No pagination automation (Claude must navigate + extract each page)
- `pbtech_scrape` accepts pre-extracted JSON rather than driving its own
  browser (v0 simplification — avoids Playwright dependency and Cloudflare WAF)
- Stage 3 spec-table lookup fills in standard-permitted values when a cable's
  listing doesn't attest a specific rate (e.g. a TB5 SKU with no Gbps in the
  title gets 80/240). This reflects what the standard allows, not what the
  vendor guarantees — accurate enough for shortlisting.

## Build roadmap

- [x] Step 1: SQLite schema + three tool stubs
- [x] Step 2: Test normalizer against real PB Tech extractor output
- [x] Step 3: LLM fallback via gpt-4o-mini (OpenRouter)
- [ ] Step 4: Pagination convenience (detect multi-page, prompt user)
- [ ] Step 5: First real shopping session
