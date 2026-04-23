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

Separately, the Playwright MCP server must be configured to allow
`browser_run_code` to load `pbtech-prime-browser.js` and
`pbtech-fetch-category.js` from this repo. The Playwright MCP setup
requirements (output-dir flag, workspace root / symlink dance,
trailing-semicolon rule) are general to using Playwright MCP from Claude
Desktop and apply to other browser-driving projects too; they live in Open
Brain under topic `playwright-mcp` rather than being duplicated here.

## Usage pattern (in Claude Desktop chat)

Typical session — Claude orchestrates the browser and tools:

1. **Prime the browser** (once per Playwright session, before any navigation
   to pbtech.co.nz): run `pbtech-prime-browser.js` via `browser_run_code` with
   `filename: ...` pointing at the file in this repo. This writes PB Tech's
   popup-suppression cookies into the BrowserContext cookie jar so the
   web-push permission prompt and sale popup do not fire on the first
   category page load. See "Popup handling" below for the mechanism.
2. **Navigate** to a PB Tech category URL via Playwright MCP (warms Cloudflare
   and PHPSESSID cookies). Hub URLs (`/usb-c-cables`) and leaf URLs
   (`/usb-c-usb-c-cables`) both work; the fetch helper normalizes to
   `/shop-all` internally. Use an explicit `browser_navigate` — never
   `browser_evaluate` against whatever page happens to be loaded (see
   "Smoke-test convention" below).
3. **Extract**: run `pbtech-fetch-category.js` via `browser_run_code` with
   `filename: ...` pointing at the file in this repo (or wherever you've
   symlinked it per the Playwright MCP setup). This fetches the full listing
   in a single POST to PB Tech's ajax endpoint and returns structured JSON —
   no pagination loop needed.
4. **Ingest**: pass the extractor JSON to `pbtech_scrape`.
5. **Query** with `pbtech_query`:
   `SELECT part, title, gbps, max_watts, length_m, price FROM products WHERE gbps >= 40 AND max_watts >= 100 ORDER BY price`
6. **Refine** queries based on results.
7. **Reset**: `pbtech_session_reset` when switching to a different product search.

## Extraction notes

PB Tech's catalog pages have a few non-obvious quirks worth knowing:

- **Subtitle is where the rich spec text lives.** Stuff like "USB 3.2 Gen 2",
  "240W PD", "DCI-P3 99%" usually appears in the `<h3>` subtitle rather than
  as a structured spec row. Worth scanning when filtering for capabilities
  not covered by structured fields.
- **Inc-GST is the second `.full-price` per card.** First is ex-GST, which
  is what NZ business buyers see. Almost always you want the inc-GST
  headline price. The fetch helper already picks the second.
- **Server-rendered HTML, no public JSON API.** The `ajax_product_collection_view_pdo.php`
  endpoint the fetch helper calls returns a JSON envelope whose `content`
  field is HTML — we parse that HTML in a detached DOMParser document.
- **Cloudflare-clean from a residential Mac IP.** Validated 2026-04-15:
  PB Tech's WAF does not challenge a Playwright-driven Chromium running on
  a residential connection. If this changes (datacenter VPN, etc.), expect
  challenges and consider switching to a persistent browser profile.

### Spec fields by category (observed)

Different categories surface different spec rows. The extractor returns
`spec_fields_seen` so the caller knows which fields are available on a
given page.

- **Thunderbolt cables**: Cable Length, Colour, Connector 1, Connector 2,
  Braided, HDMI Version, MPN, Part #
- **Professional monitors**: Screen Size, Screen Resolution, Refresh Rate,
  Response Time, Sync Type, Panel Type, VESA Size, Video Cable Included,
  Curved (sometimes), MPN, Part #
- **Portable SSDs**: Storage Size, USB Powered, Form Factor, Interface,
  Colour, MPN, Part # — note the normalized `gbps` column is unreliable
  for this category (the stage-1 regex treats "2000MB/s" subtitles as
  Mbps and stores garbage values like 2.0); query title and subtitle
  text with LIKE instead.

## Popup handling

PB Tech shows site-owned modal popups (web-push permission prompt,
promotional sale popup) a few seconds after page load on fresh browser
sessions. Suppression is handled by `pbtech-prime-browser.js`, a small
companion script that writes the site's "already shown" cookies into the
BrowserContext cookie jar via Playwright's `context.addCookies()` API.
Once set, these cookies are sent with the first request to pbtech.co.nz,
so the site's display-once logic treats the popups as already shown and
skips them entirely.

The prime script must run **before any navigation** to pbtech.co.nz in a
new Playwright session. An earlier implementation used `document.cookie =
...` inside the extractor's `page.evaluate`, but this ran after the target
page had already loaded — too late to suppress popups on the first
category page of a session. Running a priming step that writes to the
cookie jar directly closes that timing gap.

Known cookie-keyed popups:

- `user_web_push_subscription_displayed=1` — web-push permission modal
- `sale_popup=true` — promotional sale popup

Popups that don't write a cookie on display (e.g. the "Become a PB Insider"
signup prompt, which appears on the home page only — not on category pages)
cannot be suppressed this way, but since the extractor only operates after
navigation to a category page, and parses response HTML rather than the
live DOM, any popup that did appear would be irrelevant to the fetch
anyway.

To handle a new popup that writes a cookie: dismiss it manually in a normal
browser, find the cookie in DevTools → Application → Cookies, and add a
new entry to the cookies array in `pbtech-prime-browser.js`.

## Smoke-test convention

When verifying the toolkit (after a Playwright MCP update, a config change,
or any "is this still working?" check), the smoke test must include an
explicit `browser_navigate` step, not just `browser_evaluate` against
whatever page happens to be loaded. Navigation has its own failure modes
(output-dir mkdir errors, Cloudflare challenges, 404s, auth redirects)
that evaluate-only tests silently miss because the prior page's content
is still in the DOM.

Source the URL from the "Known category URLs" list below rather than
reconstructing from memory — PB Tech's URL structure (hub vs leaf, required
path segments) makes fabricated paths a real trap.

A full smoke test also exercises `pbtech-prime-browser.js` as its first
step, so the test covers the end-to-end session-level flow including popup
suppression, not just the extractor in isolation.

The general principle applies beyond this project and is captured in Open
Brain under topic `playwright-mcp`.

## Session database

SQLite at `~/.cache/pbtech/session.db`. Constructed anew per shopping task —
no cross-session persistence, no staleness logic. Data changes more frequently
than tool usage.

## Known category URLs

Hub and leaf URLs both work as input (the fetch helper normalizes to
`/shop-all`). Examples below; for a full tour of what's available, browse
the relevant top-level department.

Cables (`/category/peripherals/cables/...`):

- `thunderbolt-cables` (leaf, ~20 products)
- `usb-c-cables` (hub, ~764 products across all connector-pair subcategories)
- `usb-c-cables/usb-c-usb-c-cables` (leaf, ~411 products)
- `usb-c-cables/usb-c-displayport-cables` (leaf)
- `usb-c-cables/usb-c-hdmi-cables` (leaf)
- Other cable categories (siblings of `usb-c-cables`): `audio-cables`,
  `displayport-cables`, `dvi-cables`, `hdmi-cables`, `kvm-cables`,
  `lightning-cables`, `network-telephone-cables`, `power-cables-external`,
  `power-cables-internal`, `sata-sas-cables`, `sfp-cables`, `usb-cables`,
  `vga-cables`, `fibre-optic-cables`, `coaxial-cables`, `rca-cables`,
  `serial-parallel-cables`, `cable-rolls`, `cable-management`, `other-cables`

Monitors (`/category/peripherals/monitors/...`):

- `4k-monitors` ("High-Resolution Monitors")
- `oled-monitors`, `mini-led-monitors`
- `professional-monitors` ("Professional Design Monitors")
- `business-monitors`, `gaming-monitors`, `home-monitors`
- `ultrawide-monitors`, `curved-monitors`, `portable-monitors`
- `usb-c-monitors`, `touch-screen-monitors`, `medical-monitors`
- `off-lease-monitors` (refurb)

External storage (`/category/peripherals/hdd-external/...`):

- `portable-ssd` (leaf, ~78 products)
- `portable-hdd` (leaf, ~61 products)
- `desktop-hdd`, `mac-drives`, `game-drives`, `direct-attached-storage`

Top-level departments worth knowing (`/category/...`):

- `peripherals` (cables, monitors, keyboards, mice, headsets, webcams, docks)
- `computers` (laptops, desktops, tablets)
- `components` (CPUs, GPUs, RAM, storage)
- `networking`, `phones-gps`, `headphones-audio`, `tv-av`, `gaming`

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

## Maintenance

If PB Tech redesigns and the extractor returns `count: 0` with the "no
.js-product-card elements" error, the recovery path is:

1. Navigate to a known-good category page (from the list above) in the
   Playwright Chrome.
2. Inspect a product card's outer HTML (`document.querySelector('.js-product-card').outerHTML`
   or similar) and identify any changed wrapper classes.
3. Update the selectors in `pbtech-fetch-category.js` (and `extractor.js`
   if you want the legacy fallback to keep working).

The same recovery applies if the AJAX endpoint contract changes — the
endpoint URL, payload shape, or response envelope structure are all
observable by dismissing the "Items per page" dropdown in DevTools and
watching `/code/ajax_product_collection_view_pdo.php` in the Network tab.

## v0 limitations

- Connector strings not normalized to enums (use `LIKE '%USB-C%'` in queries)
- Mixed cable/monitor columns in one table (fine at <500 rows)
- `pbtech_scrape` accepts pre-extracted JSON rather than driving its own
  browser (v0 simplification — avoids Playwright dependency and Cloudflare WAF)
- Stage 3 spec-table lookup fills in standard-permitted values when a cable's
  listing doesn't attest a specific rate (e.g. a TB5 SKU with no Gbps in the
  title gets 80/240). This reflects what the standard allows, not what the
  vendor guarantees — accurate enough for shortlisting.
- Stage-1 regex misinterprets MB/s as Mbps (→ decimal Gbps) when parsing
  SSD subtitles. The normalized `gbps` column is unreliable for storage
  categories; for SSDs, query title/subtitle text directly with LIKE.

## Build roadmap

- [x] Step 1: SQLite schema + three tool stubs
- [x] Step 2: Test normalizer against real PB Tech extractor output
- [x] Step 3: LLM fallback via gpt-4o-mini (OpenRouter)
- [x] Step 4: Pagination convenience — solved via single-POST mechanism
  (`toggle_records_pdo.php` + `ajax_product_collection_view_pdo.php`) in
  `pbtech-fetch-category.js`. Replaces the earlier multi-page plan.
- [x] Step 5: First real shopping session (external SSD for MacBook M5 Pro,
  2026-04-24). Surfaced the popup-suppression timing gap (fixed by adding
  `pbtech-prime-browser.js`) and the stage-1 regex MB/s-as-Mbps bug (noted
  in v0 limitations; fix deferred).

## Legacy

`extractor.js` is the original DOM-walking extractor that reads
`.js-product-card` elements from the live page. It's kept as a fallback in
case the ajax mechanism in `pbtech-fetch-category.js` ever stops working;
if that happens, reverting to `extractor.js` means accepting 20 products
per page and manually paginating but keeps the toolkit functional.
