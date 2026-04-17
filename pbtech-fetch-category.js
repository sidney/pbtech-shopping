// PB Tech category listing fetcher + extractor.
// Runs inside page.evaluate() / browser_run_code — no Node/Playwright APIs.
// Fetches the full category listing in a single POST to PB Tech's ajax
// endpoint (bypassing pagination) and parses the response HTML into the
// same shape produced by the older DOM-walking extractor.js.
//
// Prerequisites: the browser must have already navigated to any page on
// www.pbtech.co.nz, so that Cloudflare cookies (cf_clearance, __cf_bm) and
// PHPSESSID are established. location.pathname is used to derive the
// category URL — the caller drives navigation before invoking this script.
//
// Returns (stringified): {url, title, count, total, page, pages,
//   spec_fields_seen, products[]} — same shape as the legacy extractor.
// On failure returns {url, title, count: 0, error: '...'} so that
// server.py's `if "error" in data` branch catches it.

(async () => {
  const origin = location.origin;
  let pathname = location.pathname;
  // Normalize: append /shop-all unless already present. Safe for both hub
  // and leaf categories — tested against /usb-c-cables/shop-all (hub, 764
  // products) and /usb-c-usb-c-cables/shop-all (leaf, 411 products).
  if (!pathname.endsWith('/shop-all')) {
    pathname = pathname.replace(/\/$/, '') + '/shop-all';
  }
  const categoryUrl = origin + pathname;

  // ----- Popup suppression ------------------------------------------------
  // PB Tech shows site-owned modal popups a few seconds after page load on
  // fresh browser sessions. Each popup writes a cookie when displayed; the
  // site's display-once logic checks for that cookie on subsequent loads
  // and skips the popup if present. Setting the cookie preemptively makes
  // the display-once logic treat the popup as already shown.
  //
  // To handle a new popup that writes a cookie:
  //   1. Dismiss it in your normal browser.
  //   2. DevTools → Application → Cookies → find the new cookie that was
  //      written (often a boolean-ish name like *_displayed or *_popup).
  //   3. Add a line below with the same name/value.
  //
  // Not every popup writes a cookie — e.g. the "Become a PB Insider"
  // signup prompt appears only on the home page (not on category pages)
  // and leaves no cookie trace; it cannot be suppressed this way but is
  // irrelevant for this helper's workflow.
  //
  // Guards are belt-and-braces: if the cookie is already set (including
  // by a prior run in the same browser session), skip to avoid churn.
  const dismissCookies = [
    ['user_web_push_subscription_displayed', '1'],  // web-push permission modal
    ['sale_popup', 'true'],                         // promotional sale popup
  ];
  for (const [name, value] of dismissCookies) {
    if (!document.cookie.includes(name + '=')) {
      document.cookie = `${name}=${value}; path=/; max-age=31536000`;
    }
  }

  const commonHeaders = {
    'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
    'X-Requested-With': 'XMLHttpRequest',
  };

  // Step 1: set the session's page-size preference to something large
  // enough to return the whole category in one response. recnum=9999
  // accepts any integer — tested up to 764 products (14.2MB JSON) with
  // clean parse. Server writes this into the PHPSESSID session; the
  // subsequent listing fetch reads it and returns all products on page 1.
  try {
    const toggle = await fetch('/code/toggle_records_pdo.php', {
      method: 'POST',
      headers: commonHeaders,
      body: 'recnum=9999',
    });
    if (!toggle.ok) {
      return JSON.stringify({
        url: categoryUrl,
        title: document.title,
        count: 0,
        error: `toggle_records_pdo.php returned ${toggle.status}`,
      });
    }
  } catch (e) {
    return JSON.stringify({
      url: categoryUrl,
      title: document.title,
      count: 0,
      error: `toggle_records_pdo.php fetch failed: ${e.message}`,
    });
  }

  // Step 2: fetch the listing. 17-field payload; most fields are empty
  // strings — PHP handler reads them without null-checking, so all must
  // be present. view=Gallery produces HTML with .js-product-card
  // selectors that the per-product parser below expects (Expanded List
  // view embeds the same spec data but under different class names).
  let envelope;
  try {
    const resp = await fetch('/code/ajax_product_collection_view_pdo.php', {
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
      return JSON.stringify({
        url: categoryUrl,
        title: document.title,
        count: 0,
        error: `ajax_product_collection_view_pdo.php returned ${resp.status}`,
      });
    }
    envelope = await resp.json();
  } catch (e) {
    return JSON.stringify({
      url: categoryUrl,
      title: document.title,
      count: 0,
      error: `listing fetch/parse failed: ${e.message}`,
    });
  }

  // Envelope shape: {totalProducts: "411 products", pageCount: "Page 1 of 1",
  //   collectionParams: {...}, showFilterHead: false, content: "<html>"}
  const contentHtml = envelope.content || '';
  if (!contentHtml) {
    return JSON.stringify({
      url: categoryUrl,
      title: document.title,
      count: 0,
      error: 'Response envelope missing content field',
    });
  }

  // Parse the content HTML in a detached document so we don't touch the
  // live DOM. This keeps the helper side-effect-free on the browser's
  // visible page and avoids interfering with anything Claude might do next.
  const parser = new DOMParser();
  const doc = parser.parseFromString(contentHtml, 'text/html');

  const cards = Array.from(doc.querySelectorAll('.js-product-card'));
  if (cards.length === 0) {
    return JSON.stringify({
      url: categoryUrl,
      title: document.title,
      count: 0,
      error: 'No .js-product-card elements in response content. ' +
             'PB Tech markup may have changed.',
    });
  }

  // Per-card parsing — selectors and output shape match legacy
  // extractor.js exactly, so downstream normalizer.py continues to work
  // unchanged.
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

  // totalProducts comes back as e.g. "411 products" — extract the integer.
  const totalMatch = (envelope.totalProducts || '').match(/(\d+)/);
  const total = totalMatch ? parseInt(totalMatch[1], 10) : products.length;

  const spec_fields_seen =
    [...new Set(products.flatMap((p) => Object.keys(p.specs)))].sort();

  return JSON.stringify({
    url: categoryUrl,
    title: document.title,
    count: products.length,
    total,
    page: 1,
    pages: 1,
    spec_fields_seen,
    products,
  });
})();
