// PB Tech category listing extractor
// Runs inside page.evaluate() — no Node/Playwright APIs, DOM only.
// Returns structured JSON: {url, title, count, total, page, pages, spec_fields_seen, products[]}

(() => {
  const cards = Array.from(document.querySelectorAll('.js-product-card'));
  if (cards.length === 0) {
    return {
      url: location.href,
      title: document.title,
      count: 0,
      error: 'No .js-product-card elements found. PB Tech markup may have changed, or this is not a category listing page.',
    };
  }
  const products = cards.map((c) => {
    const link = c.querySelector('.js-product-link');
    const part = link ? link.getAttribute('data-product-code') : null;
    const titleEl = c.querySelector('h2.np_title');
    const subtitleEl = c.querySelector('h3.np_title');
    const url = link ? link.getAttribute('href') : null;

    // Spec rows: a small div whose text is "Label:" with a sibling holding the value.
    const specs = {};
    c.querySelectorAll('div').forEach((d) => {
      const t = (d.textContent || '').trim();
      if (/^[A-Za-z0-9 #/\-]+:$/.test(t) && t.length < 30 && d.nextElementSibling) {
        specs[t.replace(':', '').trim()] = d.nextElementSibling.textContent.trim().replace(/\s+/g, ' ');
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

    return {
      part,
      title: titleEl ? titleEl.textContent.trim().replace(/\s+/g, ' ') : null,
      subtitle: subtitleEl ? subtitleEl.textContent.trim().replace(/\s+/g, ' ') : null,
      url: url ? new URL(url, location.origin).href : null,
      price_nzd_inc_gst: priceIncGst,
      specs,
    };
  });

  // Pagination
  const pagMatch = document.body.textContent.match(/(\d+)\s+products?\s+Page\s+(\d+)\s+of\s+(\d+)/);

  // All distinct spec field names seen
  const spec_fields_seen = [...new Set(products.flatMap((p) => Object.keys(p.specs)))].sort();

  return {
    url: location.href,
    title: document.title,
    count: products.length,
    total: pagMatch ? parseInt(pagMatch[1]) : null,
    page: pagMatch ? parseInt(pagMatch[2]) : null,
    pages: pagMatch ? parseInt(pagMatch[3]) : null,
    spec_fields_seen,
    products,
  };
})();
