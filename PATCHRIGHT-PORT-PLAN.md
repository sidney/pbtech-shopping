# pbtech-shopping: Patchright Port Plan

**Status:** Design complete, not yet implemented.  
**Goal:** Make `pbtech_scrape` self-contained — pass a URL, get results — eliminating the 3-step Playwright MCP dependency.  
**Delete this file** once the feature is merged.

---

## Background

The current fetch flow requires three external Playwright MCP tool calls before `pbtech_scrape` even runs:

1. `browser_navigate(url)` — warms Cloudflare cookies, establishes `PHPSESSID`
2. `browser_run_code(filename=pbtech-fetch-category.js)` — fires two AJAX POSTs inside the browser session, parses the response HTML, returns compact JSON
3. `pbtech_scrape(url, json)` — normalises and stores

The goal is to collapse this to a single tool call:

```
pbtech_scrape(url)   ← does everything internally
```

web-to-markdown-mcp (at `~/OpenSource/web-to-markdown-mcp`) already has the patchright singleton + lifespan pattern we need. This port borrows that pattern directly.

---

## Why patchright, not web-to-markdown-mcp directly

web-to-markdown-mcp fetches pages as Markdown. pbtech-shopping doesn't need Markdown — it already produces compact structured JSON (~150 bytes/product) via AJAX calls fired inside the browser session. The current output is more token-efficient than any Markdown representation of a product listing page would be. So we're not changing the data path; we're just internalising the browser management that currently lives in Playwright MCP.

---

## Key design decision: persistent context (not per-fetch contexts)

web-to-markdown-mcp creates a new `browser.new_context()` per fetch for cookie isolation across arbitrary sites.

pbtech-shopping only ever talks to `pbtech.co.nz`. The `PHPSESSID` and Cloudflare cookies from the first `page.goto()` are reused by the subsequent AJAX calls. So the right structure is:

```
persistent browser → persistent context → reused across all pbtech_scrape calls in a session
```

On first `pbtech_scrape` call: create browser, create context, prime popup cookies, navigate. On subsequent calls: navigate to new category URL (inherits warm session), fire AJAX, return JSON.

---

## How the JS logic ports

`pbtech-fetch-category.js` has this outer structure (Playwright MCP calling convention):

```javascript
async (page) => {
  return await page.evaluate(async () => {
    // ← this inner body is what we want
  });
}
```

The inner body runs inside the browser's JS engine and can be **lifted verbatim** into a Python `page.evaluate(js_string)` call. It uses `fetch()`, `DOMParser`, and `location` — all available in the browser context. Zero rewriting needed.

---

## Files to change

### `requirements.txt`

Replace:
```
mcp>=1.0.0
```
With:
```
fastmcp
patchright
```

After `pip install -r requirements.txt`, run once:
```bash
patchright install chromium
```

---

### New file: `fetcher.py`

```python
"""Browser-based fetcher for PB Tech category listings.

Manages a single persistent patchright Chromium session for the lifetime
of the MCP server process. On first use, primes popup-suppression cookies
and warms the Cloudflare/PHPSESSID session via a real page navigation.
Subsequent fetches in the same server session reuse the context — AJAX
calls inherit the live session cookies without re-navigating.
"""
from __future__ import annotations

import asyncio
import logging

from patchright.async_api import Browser, BrowserContext, async_playwright

logger = logging.getLogger(__name__)

_playwright_instance = None
_browser: Browser | None = None
_context: BrowserContext | None = None
_browser_lock = asyncio.Lock()

# Pre-populated before first navigation so PB Tech's display-once logic
# never triggers the web-push or sale popups.
# Source: pbtech-prime-browser.js (now superseded by this module).
_PBTECH_COOKIES = [
    {
        "name": "user_web_push_subscription_displayed",
        "value": "1",
        "domain": ".pbtech.co.nz",
        "path": "/",
    },
    {
        "name": "sale_popup",
        "value": "true",
        "domain": ".pbtech.co.nz",
        "path": "/",
    },
]

# Inner body of the page.evaluate() call.
# Lifted verbatim from pbtech-fetch-category.js (the part inside
# `page.evaluate(async () => { ... })`). Uses fetch(), DOMParser, location —
# all available in the browser JS engine. Output shape is identical to the
# JS script's, so normalizer.py is unchanged.
_JS_FETCH_CATEGORY = """
async () => {
  const origin = location.origin;
  let pathname = location.pathname;
  if (!pathname.endsWith('/shop-all')) {
    pathname = pathname.replace(/\\/$/, '') + '/shop-all';
  }
  const categoryUrl = origin + pathname;

  const commonHeaders = {
    'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
    'X-Requested-With': 'XMLHttpRequest',
  };

  try {
    const toggle = await fetch('/code/toggle_records_pdo.php', {
      method: 'POST',
      headers: commonHeaders,
      body: 'recnum=9999',
    });
    if (!toggle.ok) {
      return { url: categoryUrl, title: document.title, count: 0,
               error: `toggle_records_pdo.php returned ${toggle.status}` };
    }
  } catch (e) {
    return { url: categoryUrl, title: document.title, count: 0,
             error: `toggle_records_pdo.php fetch failed: ${e.message}` };
  }

  let envelope;
  try {
    const resp = await fetch('/code/ajax_product_collection_view_pdo.php', {
      method: 'POST',
      headers: commonHeaders,
      body: new URLSearchParams({
        view: 'Gallery',
        url: pathname,
        catParent: '', catListId: '', catListName: '', callout: '',
        searchParams: '', searchValue: '', filterParams: '',
        pageParams: '1',
        appleURL: '',
        forceOpenBox: 'true',
        forceExDemo: 'true',
        sortOrder: 'popularity',
        productList: '', brandParam: '', branchParam: '',
      }),
    });
    if (!resp.ok) {
      return { url: categoryUrl, title: document.title, count: 0,
               error: `ajax_product_collection_view_pdo.php returned ${resp.status}` };
    }
    envelope = await resp.json();
  } catch (e) {
    return { url: categoryUrl, title: document.title, count: 0,
             error: `listing fetch/parse failed: ${e.message}` };
  }

  const contentHtml = envelope.content || '';
  if (!contentHtml) {
    return { url: categoryUrl, title: document.title, count: 0,
             error: 'Response envelope missing content field' };
  }

  const parser = new DOMParser();
  const doc = parser.parseFromString(contentHtml, 'text/html');
  const cards = Array.from(doc.querySelectorAll('.js-product-card'));
  if (cards.length === 0) {
    return { url: categoryUrl, title: document.title, count: 0,
             error: 'No .js-product-card elements in response content. PB Tech markup may have changed.' };
  }

  const products = cards.map((c) => {
    const link = c.querySelector('.js-product-link');
    const part = link ? link.getAttribute('data-product-code') : null;
    const titleEl = c.querySelector('h2.np_title');
    const subtitleEl = c.querySelector('h3.np_title');
    const url = link ? link.getAttribute('href') : null;

    const specs = {};
    c.querySelectorAll('div').forEach((d) => {
      const t = (d.textContent || '').trim();
      if (/^[A-Za-z0-9 #\\/\\-]+:$/.test(t) && t.length < 30 && d.nextElementSibling) {
        specs[t.replace(':', '').trim()] =
          d.nextElementSibling.textContent.trim().replace(/\\s+/g, ' ');
      }
    });

    const fullPrices = c.querySelectorAll('.full-price');
    let priceIncGst = null;
    const priceEl = fullPrices.length >= 2 ? fullPrices[1] : fullPrices[0];
    if (priceEl) {
      const m = priceEl.textContent.match(/\\$([\\d,]+\\.\\d{2})/);
      if (m) priceIncGst = parseFloat(m[1].replace(/,/g, ''));
    }

    let absUrl = null;
    if (url) {
      try { absUrl = new URL(url, origin).href; } catch (e) { absUrl = url; }
    }

    return {
      part,
      title: titleEl ? titleEl.textContent.trim().replace(/\\s+/g, ' ') : null,
      subtitle: subtitleEl ? subtitleEl.textContent.trim().replace(/\\s+/g, ' ') : null,
      url: absUrl,
      price_nzd_inc_gst: priceIncGst,
      specs,
    };
  });

  const totalMatch = (envelope.totalProducts || '').match(/(\\d+)/);
  const total = totalMatch ? parseInt(totalMatch[1], 10) : products.length;
  const spec_fields_seen =
    [...new Set(products.flatMap((p) => Object.keys(p.specs)))].sort();

  return {
    url: categoryUrl,
    title: document.title,
    count: products.length,
    total,
    page: 1,
    pages: 1,
    spec_fields_seen,
    products,
  };
}
"""


async def _get_context() -> BrowserContext:
    """Return the shared browser context, creating it if needed or after a crash."""
    global _playwright_instance, _browser, _context
    async with _browser_lock:
        if _browser is None or not _browser.is_connected():
            if _playwright_instance is not None:
                try:
                    await _playwright_instance.stop()
                except Exception:
                    pass
            _playwright_instance = await async_playwright().start()
            _browser = await _playwright_instance.chromium.launch(headless=True)
            _context = await _browser.new_context()
            await _context.add_cookies(_PBTECH_COOKIES)
            logger.info("fetcher: browser context created, popup cookies primed")
        return _context


async def fetch_category(url: str) -> dict:
    """Navigate to a PB Tech category URL and return the full product listing.

    Returns the same dict shape as pbtech-fetch-category.js:
    {url, title, count, total, page, pages, spec_fields_seen, products[]}
    On error: {url, title, count: 0, error: str}
    """
    context = await _get_context()
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        result = await page.evaluate(_JS_FETCH_CATEGORY)
        if not isinstance(result, dict):
            return {"url": url, "count": 0,
                    "error": f"evaluate returned unexpected type: {type(result)}"}
        return result
    except Exception as exc:
        return {"url": url, "count": 0, "error": str(exc)}
    finally:
        await page.close()  # close page but keep context alive (session cookies persist)


async def close_browser() -> None:
    """Shut down the browser. Called from server lifespan on exit."""
    global _playwright_instance, _browser, _context
    for obj, method in [
        (_context, "close"), (_browser, "close"), (_playwright_instance, "stop")
    ]:
        if obj is not None:
            try:
                await getattr(obj, method)()
            except Exception:
                pass
```

---

### Updated `server.py`

Three changes from current:

**1. Imports and lifespan** — replace the FastMCP import and add lifespan:

```python
# Replace:
from mcp.server.fastmcp import FastMCP

# With:
from contextlib import asynccontextmanager
from fastmcp import FastMCP
from fetcher import close_browser

@asynccontextmanager
async def _lifespan(server):
    yield
    await close_browser()

mcp = FastMCP("pbtech-shopping", lifespan=_lifespan)
```

**2. `pbtech_scrape` signature** — make async, add optional URL-only path:

```python
@mcp.tool()
async def pbtech_scrape(category_url: str, extractor_json: str = "") -> str:
    """Normalize and store PB Tech product data.

    Two usage modes:
    - URL-only (new): pbtech_scrape(category_url) — fetches the category
      internally using a headless patchright browser. No Playwright MCP
      required.
    - Manual (legacy/testing): pbtech_scrape(category_url, extractor_json)
      — accepts pre-extracted JSON string from pbtech-fetch-category.js.
      Useful for debugging or if the internal browser fails.

    Args:
        category_url: The PB Tech category URL to scrape.
        extractor_json: Optional. If empty (default), the category is
            fetched internally. If provided, used as-is (legacy path).
    """
    if not extractor_json:
        from fetcher import fetch_category
        data = await fetch_category(category_url)
    else:
        try:
            data = json.loads(extractor_json)
        except json.JSONDecodeError as e:
            return f"ERROR: Invalid JSON — {e}"

    # ... rest of the function body is unchanged from current server.py ...
```

**3. `pbtech_query` and `pbtech_session_reset`** — no changes needed, but they can optionally be made `async def` for consistency. Not required; FastMCP handles both.

---

## What stays the same

- `normalizer.py` — no changes
- `db.py` — no changes  
- All test files — still work because `extractor_json` path is preserved
- `pbtech-fetch-category.js` — keep as fallback (three-step Playwright MCP flow still works if internal browser fails)
- `pbtech-prime-browser.js` — superseded by `fetcher.py`'s `_PBTECH_COOKIES`; keep for reference

---

## New session workflow

**Before:**
```
browser_navigate(url)           ← Playwright MCP
browser_run_code(filename=...)  ← Playwright MCP
pbtech_scrape(url, json)        ← pbtech MCP
```

**After:**
```
pbtech_scrape(url)              ← pbtech MCP (self-contained)
```

The `~/.playwright-mcp/pbtech/` symlink and `browser_run_code` invocation can be retired from README usage docs.

---

## Verification checklist

- [ ] `patchright install chromium` run after pip install
- [ ] `pbtech_scrape("https://www.pbtech.co.nz/category/cables/thunderbolt-cables")` returns ~21 products with coverage stats
- [ ] Second call to same URL is faster (context reuse, no browser cold-start)
- [ ] `pbtech_scrape(url, extractor_json=fixture_json)` still works (legacy path)
- [ ] `pbtech_session_reset()` still works
- [ ] Claude Desktop config needs no changes (same MCP server entry, `uv run --with-requirements`)

---

## Claude Desktop config reminder

No changes needed to `~/Library/Application Support/Claude/claude_desktop_config.json`. The MCP server entry is the same — `uv` will pick up the new `requirements.txt` automatically on next launch.

The `OPENROUTER_API_KEY` env block stays in the config as-is.
