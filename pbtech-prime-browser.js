// PB Tech popup suppression primer.
//
// Usage: run this via browser_run_code ONCE per Playwright session,
// before any navigation to pbtech.co.nz. All subsequent navigations to
// any PB Tech page in the same session will have the popup-suppression
// cookies already present in the BrowserContext cookie jar and will
// therefore not trigger the site's popup modals.
//
// Mechanism: PB Tech shows two cookie-keyed modal popups a few seconds
// after page load on fresh browser sessions — a web-push permission
// prompt and a promotional sale popup. Each popup writes a cookie on
// display, and the site's display-once logic checks for those cookies
// on subsequent loads to decide whether to show the popup. This primer
// pre-populates those cookies via Playwright's context.addCookies() API,
// which writes directly to the BrowserContext cookie jar without any
// page being loaded. Because the cookies are in the jar before the first
// request to pbtech.co.nz, they are sent with that request and the
// site's server-side logic generates an "already shown" page that
// never reaches the popup-display code.
//
// Why not document.cookie inside page.evaluate (as an earlier
// implementation did in pbtech-fetch-category.js)? Because
// document.cookie only runs after a page has loaded — too late to
// suppress popups on the first PB Tech page of a session, which is
// exactly when they fire on a fresh browser profile. This primer runs
// before any pbtech.co.nz navigation and closes that timing gap.
//
// Idempotent: calling addCookies() with a cookie that already exists
// in the jar simply overwrites it with the same value, so running the
// primer multiple times in a session is harmless.
//
// To add suppression for a new cookie-keyed popup:
//   1. Dismiss the popup manually in a normal browser.
//   2. DevTools → Application → Cookies → find the new cookie that was
//      written (often a boolean-ish name like *_displayed or *_popup).
//   3. Add a new object to the cookies array below with the same name
//      and value, and the domain and path that match what you see in
//      DevTools.
//
// File ends with `}` not `};` — Playwright MCP wraps the contents as
// `await (FILE_CONTENTS)(page)` and a trailing semicolon breaks the wrap.

async (page) => {
  const oneYearFromNow = Math.floor(Date.now() / 1000) + 31536000;
  const cookies = [
    // web-push permission modal
    {
      name: 'user_web_push_subscription_displayed',
      value: '1',
      domain: '.pbtech.co.nz',
      path: '/',
      expires: oneYearFromNow,
    },
    // promotional sale popup
    {
      name: 'sale_popup',
      value: 'true',
      domain: '.pbtech.co.nz',
      path: '/',
      expires: oneYearFromNow,
    },
  ];
  await page.context().addCookies(cookies);
  return { primed: true, cookies: cookies.length, domain: '.pbtech.co.nz' };
}
