"""Browser-based fetcher for PB Tech category listings.

Manages a single persistent patchright Chromium session for the lifetime
of the MCP server process. On first use, primes popup-suppression cookies
and warms the Cloudflare/PHPSESSID session via a real page navigation.
Subsequent fetches in the same server session reuse the context — the
in-browser fetch() call for the /shop-all page inherits live session
cookies without re-navigating.
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

  let contentHtml;
  try {
    const resp = await fetch(categoryUrl);
    if (!resp.ok) {
      return { url: categoryUrl, title: document.title, count: 0,
               error: `shop-all fetch returned ${resp.status}` };
    }
    contentHtml = await resp.text();
  } catch (e) {
    return { url: categoryUrl, title: document.title, count: 0,
             error: `listing fetch/parse failed: ${e.message}` };
  }

  if (!contentHtml) {
    return { url: categoryUrl, title: document.title, count: 0,
             error: 'shop-all returned empty response' };
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

  const total = products.length;
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
