// PB Tech category listing fetcher + extractor.
//
// Usage:
// 1. Once per Playwright session, before any navigation to pbtech.co.nz:
//    run pbtech-prime-browser.js via browser_run_code. This sets
//    popup-suppression cookies in the BrowserContext cookie jar so that
//    PB Tech's web-push permission and sale popups do not fire on the
//    first category page load of the session. This extractor has no
//    self-protection against those popups — see the NOTE at the top of
//    the inner page.evaluate function.
// 2. Navigate the Playwright browser to the target PB Tech category URL
//    (also warms Cloudflare cookies and PHPSESSID for the subsequent
//    ajax POSTs performed by this extractor).
// 3. Call browser_run_code with this file as `filename`.
//
// Returns: { url, title, count, total, page, pages, spec_fields_seen, products[] }
//
// Fetches the full category listing in a single POST to PB Tech's ajax
// endpoint (bypassing pagination) and parses the response HTML into the
// same shape produced by the older DOM-walking extractor.js. The category
// URL is derived from location.pathname — the caller drives navigation
// before invoking this script.
//
// On failure returns {url, title, count: 0, error: '...'} so server.py's
// `if "error" in data` branch catches it.
//
// File ends with `}` not `};` — Playwright MCP wraps the contents as
// `await (FILE_CONTENTS)(page)` and a trailing semicolon breaks the wrap.

async (page) => {
  return await page.evaluate(async () => {
    // NOTE: this extractor has no protection of its own against PB Tech's
    // cookie-keyed popups (web-push permission prompt, promotional sale
    // popup). At time of writing, pbtech-prime-browser.js is the companion
    // script that primes the BrowserContext cookie jar to prevent them,
    // and must run once per Playwright session before any navigation to
    // pbtech.co.nz. If no priming has occurred, the popups will display
    // in the browser on the first category page load of the session —
    // harmless to this extractor (which fetches the listing via ajax and
    // parses the response HTML, so modal dismissal is not required for
    // correct output) but visually noisy and a sign that the priming
    // step has been skipped.

    const origin = location.origin;
    let pathname = location.pathname;
    // Normalize: append /shop-all unless already present. Safe for both
    // hub and leaf categories — tested against /usb-c-cables/shop-all
    // (hub, 764 products) and /usb-c-usb-c-cables/shop-all (leaf, 411
    // products).
    if (!pathname.endsWith('/shop-all')) {
      pathname = pathname.replace(/\/$/, '') + '/shop-all';
    }
    const categoryUrl = origin + pathname;

    const commonHeaders = {
      'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
      'X-Requested-With': 'XMLHttpRequest',
    };

    // Step 1: set the session's page-size preference to something large
    // enough to return the whole category in one response. recnum=9999
    // accepts any integer — tested up to 764 products (14.2MB JSON)
    // with clean parse. Server writes this into the PHPSESSID session;
    // the subsequent listing fetch reads it and returns all products on
    // page 1.
    try {
      const toggle = await fetch('/code/toggle_records_pdo.php', {
        method: 'POST',
        headers: commonHeaders,
        body: 'recnum=9999',
      });
      if (!toggle.ok) {
        return {
          url: categoryUrl,
          title: document.title,
          count: 0,
          error: `toggle_records_pdo.php returned ${toggle.status}`,
        };
      }
    } catch (e) {
      return {
        url: categoryUrl,
        title: document.title,
        count: 0,
        error: `toggle_records_pdo.php fetch failed: ${e.message}`,
      };
    }

    // Step 2: fetch the listing. 17-field payload; most fields are empty
    // strings — PHP handler reads them without null-checking, so all
    // must be present. view=Gallery produces HTML with .js-product-card
    // selectors that the per-product parser below expects (Expanded
    // List view embeds the same spec data but under different class
    // names).
    let envelope;
    try {
      const resp = await fetch('/code/ajax_display_products_pdo.php', {
        method: 'POST',
        headers: commonHeaders,
        body: new URLSearchParams({
          view: 'Gallery',
          url: pathname,
          catParent: '',
          catListId: '',
          catListName: '',
          callout: '',
          searchParams: '',
          searchValue: '',
          filterParams: '',
          pageParams: '1',
          appleURL: '',
          forceOpenBox: 'true',
          forceExDemo: 'true',
          sortOrder: 'popularity',
          productList: '',
          brandParam: '',
          branchParam: '',
        }),
      });
      if (!resp.ok) {
        return {
          url: categoryUrl,
          title: document.title,
          count: 0,
          error: `ajax_display_products_pdo.php returned ${resp.status}`,
        };
      }
      envelope = await resp.json();
    } catch (e) {
      return {
        url: categoryUrl,
        title: document.title,
        count: 0,
        error: `listing fetch/parse failed: ${e.message}`,
      };
    }

    // Envelope shape: {totalProducts: "411 products", pageCount: "Page 1
    // of 1", collectionParams: {...}, showFilterHead: false, content:
    // "<html>"}
    const contentHtml = envelope.content || '';
    if (!contentHtml) {
      return {
        url: categoryUrl,
        title: document.title,
        count: 0,
        error: 'Response envelope missing content field',
      };
    }

    // Parse the content HTML in a detached document so we don't touch
    // the live DOM. This keeps the helper side-effect-free on the
    // browser's visible page and avoids interfering with anything
    // Claude might do next.
    const parser = new DOMParser();
    const doc = parser.parseFromString(contentHtml, 'text/html');

    const cards = Array.from(doc.querySelectorAll('.js-product-card'));
    if (cards.length === 0) {
      return {
        url: categoryUrl,
        title: document.title,
        count: 0,
        error: 'No .js-product-card elements in response content. ' +
               'PB Tech markup may have changed.',
      };
    }

    // Per-card parsing — selectors and output shape match legacy
    // extractor.js exactly, so downstream normalizer.py continues to
    // work unchanged.
    const products = cards.map((c) => {
      const link = c.querySelector('.js-product-link');
      const part = link ? link.getAttribute('data-product-code') : null;
      const titleEl = c.querySelector('h2.np_title');
      const subtitleEl = c.querySelector('h3.np_title');
      const url = link ? link.getAttribute('href') : null;

      // Spec rows: a small div whose text is "Label:" with a sibling
      // holding the value.
      const specs = {};
      c.querySelectorAll('div').forEach((d) => {
        const t = (d.textContent || '').trim();
        if (/^[A-Za-z0-9 #/\-]+:$/.test(t) && t.length < 30 && d.nextElementSibling) {
          specs[t.replace(':', '').trim()] =
            d.nextElementSibling.textContent.trim().replace(/\s+/g, ' ');
        }
      });

      // Two .full-price elements per card; second is inc-GST.
      const fullPrices = c.querySelectorAll('.full-price');
      let priceIncGst = null;
      const priceEl = fullPrices.length >= 2 ? fullPrices[1] : fullPrices[0];
      if (priceEl) {
        const m = priceEl.textContent.match(/\$([\d,]+\.\d{2})/);
        if (m) priceIncGst = parseFloat(m[1].replace(/,/g, ''));
      }

      // URLs in the response are relative (e.g. "product/CABXYZ/...").
      // Resolve against origin, not location — the browser may be on a
      // different page than the category we fetched.
      let absUrl = null;
      if (url) {
        try { absUrl = new URL(url, origin).href; } catch (e) { absUrl = url; }
      }

      return {
        part,
        title: titleEl ? titleEl.textContent.trim().replace(/\s+/g, ' ') : null,
        subtitle: subtitleEl ? subtitleEl.textContent.trim().replace(/\s+/g, ' ') : null,
        url: absUrl,
        price_nzd_inc_gst: priceIncGst,
        specs,
      };
    });

    // totalProducts comes back as e.g. "411 products" — extract the
    // integer.
    const totalMatch = (envelope.totalProducts || '').match(/(\d+)/);
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
  });
}
