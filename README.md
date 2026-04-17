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

Clone the repo anywhere you like — the code doesn't assume a particular
location. Wherever you clone it, substitute that path for `<REPO>` below.

```bash
cd <REPO>                                # e.g. ~/OpenSource/pbtech-shopping
pip install -r requirements.txt
export OPENROUTER_API_KEY=sk-or-v1-...   # optional; stage 4 is a no-op without it
```

The session database lives at `~/.cache/pbtech/session.db` regardless of where
the repo is cloned.

### Claude Desktop config

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`,
replacing `<REPO>` with the absolute path to your clone:

```json
{
  "mcpServers": {
    "pbtech-shopping": {
      "command": "python3",
      "args": ["<REPO>/server.py"],
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

1. Navigate to any PB Tech category URL via Playwright MCP (warms Cloudflare
   and PHPSESSID cookies). Hub URLs (`/usb-c-cables`) and leaf URLs
   (`/usb-c-usb-c-cables`) both work; the fetch helper normalizes to
   `/shop-all` internally.
2. Run `pbtech-fetch-category.js` via `browser_run_code`. This fetches the
   full listing in a single POST to PB Tech's ajax endpoint and returns
   structured JSON — no pagination loop needed.
3. Pass the extractor JSON to `pbtech_scrape`.
4. Query with `pbtech_query`:
   `SELECT part, title, gbps, max_watts, length_m, price FROM products WHERE gbps >= 40 AND max_watts >= 100 ORDER BY price`
5. Refine queries based on results.
6. `pbtech_session_reset` when switching to a different product search.

## Popup handling

PB Tech shows site-owned modal popups (web-push permission prompt,
promotional sale popup) a few seconds after page load on fresh browser
sessions. `pbtech-fetch-category.js` preemptively suppresses those that key
on cookies by setting the cookie the site uses to track "already shown."
Known cookie-keyed popups:

- `user_web_push_subscription_displayed=1` — web-push permission modal
- `sale_popup=true` — promotional sale popup

Popups that don't write a cookie on display (e.g. the "Become a PB Insider"
signup prompt, which appears on the home page only — not on category pages)
cannot be suppressed this way, but since the helper only operates after
Claude has navigated to a category page, and parses response HTML rather
than the live DOM, any popup that did appear would be irrelevant to the
fetch anyway.

To handle a new popup that writes a cookie: dismiss it manually in a normal
browser, find the cookie in DevTools → Application → Cookies, and add it
to the `dismissCookies` array at the top of `pbtech-fetch-category.js`.

## Session database

SQLite at `~/.cache/pbtech/session.db`. Constructed anew per shopping task —
no cross-session persistence, no staleness logic. Data changes more frequently
than tool usage.

## Known category URLs

Hub and leaf URLs both work as input (the fetch helper normalizes to
`/shop-all`). Examples:

- `/category/peripherals/cables/thunderbolt-cables` (leaf, ~20 products)
- `/category/peripherals/cables/usb-c-cables` (hub, ~764 products across
  all connector-pair subcategories)
- `/category/peripherals/cables/usb-c-cables/usb-c-usb-c-cables` (leaf,
  ~411 products)
- `/category/peripherals/cables/usb-c-cables/usb-c-displayport-cables` (leaf)
- `/category/peripherals/cables/usb-c-cables/usb-c-hdmi-cables` (leaf)
- `/category/peripherals/monitors/professional-monitors` (leaf)
- `/category/peripherals/monitors/gaming-monitors` (leaf)

See also `pbtech-fetch-category-README.md` if co-located with the fetch
helper at `~/scripts/pbtech/` (rename your local sibling README to match).

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
- `fixture_usbc_cables_2026-04-16.json` — USB-C to USB-C cables (page 1 of 21
  at the old 20-per-page scrape), stages 1-3 achieve 5/15/20 on
  gbps/max_watts/length_m; 18 stragglers need stage 4

Fixtures were produced by the legacy DOM-walking `extractor.js`, which
returned 20 products per page. They remain valid for normalizer regression
tests because the per-product output shape is unchanged between the two
extractors.

## v0 limitations

- Connector strings not normalized to enums (use `LIKE '%USB-C%'` in queries)
- Mixed cable/monitor columns in one table (fine at <500 rows)
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
- [x] Step 4: Pagination convenience — solved via single-POST mechanism
  (`toggle_records_pdo.php` + `ajax_product_collection_view_pdo.php`) in
  `pbtech-fetch-category.js`. Replaces the earlier multi-page plan.
- [ ] Step 5: First real shopping session

## Legacy

`extractor.js` is the original DOM-walking extractor that reads
`.js-product-card` elements from the live page. It's kept as a fallback in
case the ajax mechanism in `pbtech-fetch-category.js` ever stops working;
if that happens, reverting to `extractor.js` means accepting 20 products
per page and manually paginating but keeps the toolkit functional.
