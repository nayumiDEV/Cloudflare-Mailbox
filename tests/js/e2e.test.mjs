import { chromium } from 'playwright';
import assert from 'node:assert/strict';

async function launchBrowser() {
  for (const channel of ['chrome', 'msedge', undefined]) {
    try {
      const browser = await chromium.launch({ channel, headless: true });
      return browser;
    } catch (_) {
      // try next channel
    }
  }
  throw new Error('Could not launch any browser (Chrome, Edge, or Chromium)');
}

async function ensureLoggedIn(page) {
  await page.goto('http://127.0.0.1:8000', { waitUntil: 'domcontentloaded' });
  const loginInput = page.locator('#login-user');
  if (await loginInput.isVisible()) {
    await loginInput.fill('admin');
    await page.locator('#login-pass').fill('admin12345');
    await page.locator('button:has-text("Đăng nhập")').click();
    await page.waitForSelector('.gmail-header', { timeout: 8000 });
  } else {
    await page.waitForSelector('.gmail-header', { timeout: 5000 });
  }
}

async function run() {
  console.log('🚀 Launching Playwright E2E browser tests...');
  const browser = await launchBrowser();
  console.log('✅ Browser launched successfully: Chrome/Chromium');

  try {
    // -------------------------------------------------------------
    // TEST SUITE 1: Mobile Viewport (iPhone SE: 375x667)
    // -------------------------------------------------------------
    console.log('\n--- 1. Testing Mobile Viewport (375x667) ---');
    const mobileContext = await browser.newContext({
      viewport: { width: 375, height: 667 },
      isMobile: true,
      hasTouch: true,
    });
    const mPage = await mobileContext.newPage();
    await ensureLoggedIn(mPage);

    // 1.1 Header dimensions and overflow
    const headerMetrics = await mPage.evaluate(() => {
      const h = document.querySelector('.gmail-header');
      const b = document.body;
      const sb = document.querySelector('.gmail-search-bar');
      const inp = document.querySelector('.gmail-search-bar input');
      const toggle = document.getElementById('theme-toggle');
      const left = document.querySelector('.gmail-header-left');
      const right = document.querySelector('.gmail-header-right');
      const logo = document.querySelector('.gmail-header-left .gmail-logo');

      return {
        windowWidth: window.innerWidth,
        bodyScrollWidth: b.scrollWidth,
        headerScrollWidth: h.scrollWidth,
        headerClientWidth: h.clientWidth,
        searchBarWidth: sb.getBoundingClientRect().width,
        searchBarRight: sb.getBoundingClientRect().right,
        searchInputWidth: inp.getBoundingClientRect().width,
        themeToggleX: toggle.getBoundingClientRect().x,
        themeToggleWidth: toggle.getBoundingClientRect().width,
        logoVisible: logo ? window.getComputedStyle(logo).display !== 'none' : false,
        hamburgerVisible: left.querySelector('.hamburger-btn') !== null,
        profileVisible: right.querySelector('.avatar') !== null,
      };
    });

    console.log('Mobile header metrics:', {
      windowWidth: headerMetrics.windowWidth,
      headerScrollWidth: headerMetrics.headerScrollWidth,
      headerClientWidth: headerMetrics.headerClientWidth,
      searchBarWidth: Math.round(headerMetrics.searchBarWidth),
      searchInputWidth: Math.round(headerMetrics.searchInputWidth),
    });

    assert.equal(headerMetrics.headerScrollWidth, headerMetrics.headerClientWidth, 'Header must NOT have horizontal scroll/overflow');
    assert.equal(headerMetrics.bodyScrollWidth, 375, 'Body must NOT horizontally overflow 375px');
    assert.equal(headerMetrics.logoVisible, false, 'Brand logo text must be hidden in mobile header to save space');
    assert.ok(headerMetrics.hamburgerVisible, 'Hamburger button must be visible in mobile header');
    assert.ok(headerMetrics.profileVisible, 'Avatar chip must be visible in mobile header');
    assert.ok(headerMetrics.searchBarWidth >= 200, `Search bar width should be >= 200px, was ${headerMetrics.searchBarWidth}`);
    assert.ok(headerMetrics.searchInputWidth >= 120, `Search input width should be >= 120px, was ${headerMetrics.searchInputWidth}`);
    assert.ok(headerMetrics.searchBarRight <= headerMetrics.themeToggleX, 'Search bar must not extend under/past theme toggle');
    console.log('✅ Mobile header layout: PASSED (no overflow, search bar width is spacious)');

    // 1.2 Search interaction & clear button
    console.log('Testing mobile search input & clear button...');
    const searchInput = mPage.locator('.gmail-search-bar input');
    await searchInput.click();
    await searchInput.fill('Google');
    await mPage.waitForTimeout(400); // debounce

    const clearBtn = mPage.locator('.gmail-search-clear');
    assert.ok(await clearBtn.isVisible(), 'Clear button must appear when search has a query');

    await clearBtn.click();
    await mPage.waitForTimeout(300);
    const clearedVal = await searchInput.inputValue();
    assert.equal(clearedVal, '', 'Clear button must reset search input');
    console.log('✅ Mobile search & clear: PASSED');

    // 1.3 Mobile drawer navigation
    console.log('Testing mobile drawer toggle & navigation...');
    const hamburger = mPage.locator('.hamburger-btn');
    await hamburger.click();
    await mPage.waitForTimeout(250);

    const drawerOpen = await mPage.evaluate(() => {
      const sb = document.getElementById('sidebar');
      const bd = document.getElementById('sidebar-backdrop');
      return sb.classList.contains('mobile-open') && bd.classList.contains('active');
    });
    assert.ok(drawerOpen, 'Sidebar and backdrop must be active when hamburger is tapped');

    // Verify drawer branding and close button
    const drawerBrandVisible = await mPage.locator('.mobile-drawer-brand').isVisible();
    assert.ok(drawerBrandVisible, 'Drawer must display full brand logo and name');

    // Tap a nav button inside the drawer (e.g., Mailbox)
    await mPage.locator('.nav button:has-text("Mailbox")').first().click();
    await mPage.waitForTimeout(400);

    const drawerClosedAfterNav = await mPage.evaluate(() => {
      const sb = document.getElementById('sidebar');
      return !sb.classList.contains('mobile-open');
    });
    assert.ok(drawerClosedAfterNav, 'Drawer must automatically close after selecting a navigation item');

    // Return to Inbox
    await hamburger.click();
    await mPage.waitForTimeout(250);
    await mPage.locator('.nav button:has-text("Hộp thư")').click();
    await mPage.waitForTimeout(400);
    console.log('✅ Mobile drawer toggle & auto-close: PASSED');

    // 1.4 Mobile Email Reader
    console.log('Testing mobile email reader view...');
    const firstMsg = mPage.locator('.msg-row').first();
    if (await firstMsg.isVisible()) {
      await firstMsg.click();
      await mPage.waitForTimeout(500);

      const readerOpen = await mPage.evaluate(() => {
        const reader = document.querySelector('.reader');
        return reader && reader.classList.contains('mobile-open');
      });
      assert.ok(readerOpen, 'Reader pane must open full-screen on mobile (.reader.mobile-open)');

      const backBtn = mPage.locator('.reader-back-btn');
      assert.ok(await backBtn.isVisible(), 'Mobile reader must show back button');
      await backBtn.click();
      await mPage.waitForTimeout(400);

      const readerClosed = await mPage.evaluate(() => {
        const reader = document.querySelector('.reader');
        return !reader.classList.contains('mobile-open');
      });
      assert.ok(readerClosed, 'Reader must close when back button is tapped');
    }
    console.log('✅ Mobile reader open & back: PASSED');

    // 1.5 Theme toggle on mobile
    console.log('Testing mobile theme toggle...');
    const themeBtn = mPage.locator('#theme-toggle');
    assert.ok(await themeBtn.isVisible(), 'Theme toggle must be visible');
    const wasDark = await mPage.evaluate(() => document.documentElement.classList.contains('dark'));
    await themeBtn.click();
    await mPage.waitForTimeout(200);
    const isDarkNow = await mPage.evaluate(() => document.documentElement.classList.contains('dark'));
    assert.notEqual(wasDark, isDarkNow, 'Clicking theme toggle must switch dark/light mode');
    // Restore
    await themeBtn.click();
    console.log('✅ Mobile theme toggle: PASSED');

    await mobileContext.close();

    // -------------------------------------------------------------
    // TEST SUITE 2: Ultra-narrow Viewport (360x640 Android)
    // -------------------------------------------------------------
    console.log('\n--- 2. Testing Ultra-Narrow Viewport (360x640) ---');
    const narrowContext = await browser.newContext({
      viewport: { width: 360, height: 640 },
      isMobile: true,
      hasTouch: true,
    });
    const nPage = await narrowContext.newPage();
    await ensureLoggedIn(nPage);

    const narrowMetrics = await nPage.evaluate(() => {
      const h = document.querySelector('.gmail-header');
      const b = document.body;
      const sb = document.querySelector('.gmail-search-bar');
      return {
        headerScrollWidth: h.scrollWidth,
        headerClientWidth: h.clientWidth,
        bodyScrollWidth: b.scrollWidth,
        searchBarWidth: sb.getBoundingClientRect().width,
      };
    });

    assert.equal(narrowMetrics.headerScrollWidth, narrowMetrics.headerClientWidth, 'Header must not overflow on 360px viewport');
    assert.equal(narrowMetrics.bodyScrollWidth, 360, 'Body must not overflow on 360px viewport');
    assert.ok(narrowMetrics.searchBarWidth >= 180, `Search bar should remain >= 180px on 360px screen, was ${narrowMetrics.searchBarWidth}`);
    console.log('✅ Ultra-narrow 360px layout: PASSED');
    await narrowContext.close();

    // -------------------------------------------------------------
    // TEST SUITE 3: Desktop Viewport (1280x800)
    // -------------------------------------------------------------
    console.log('\n--- 3. Testing Desktop Viewport (1280x800) ---');
    const desktopContext = await browser.newContext({
      viewport: { width: 1280, height: 800 },
    });
    const dPage = await desktopContext.newPage();
    await ensureLoggedIn(dPage);

    const desktopMetrics = await dPage.evaluate(() => {
      const logo = document.querySelector('.gmail-header-left .gmail-logo');
      const brandName = document.querySelector('.gmail-brand-name');
      const brandTag = document.querySelector('.gmail-brand-tag');
      const sb = document.querySelector('.gmail-search-bar');
      const rightBtns = document.querySelectorAll('.gmail-header-right .gmail-icon-btn');
      const profile = document.querySelector('.gmail-profile-chip');

      return {
        logoVisible: logo && window.getComputedStyle(logo).display !== 'none',
        brandNameText: brandName?.textContent,
        brandTagText: brandTag?.textContent,
        searchBarWidth: sb.getBoundingClientRect().width,
        rightBtnCount: rightBtns.length,
        profileVisible: profile !== null,
      };
    });

    assert.ok(desktopMetrics.logoVisible, 'Brand logo must be visible on desktop');
    assert.equal(desktopMetrics.brandNameText, 'OTP Inbox', 'Brand name must match APP_NAME');
    assert.equal(desktopMetrics.brandTagText, 'OTP', 'Brand tag must be OTP');
    assert.ok(desktopMetrics.searchBarWidth >= 400, 'Search bar should be wide on desktop');
    assert.ok(desktopMetrics.rightBtnCount >= 2, 'Header right must include refresh and notification icons on desktop');
    assert.ok(desktopMetrics.profileVisible, 'Profile chip must be visible on desktop');

    // Test desktop sidebar rail toggle
    const hamburgerDesktop = dPage.locator('.hamburger-btn');
    await hamburgerDesktop.click();
    await dPage.waitForTimeout(250);
    const isSidebarCollapsed = await dPage.evaluate(() => {
      const sb = document.getElementById('sidebar');
      return sb.classList.contains('collapsed');
    });
    assert.ok(isSidebarCollapsed, 'Sidebar should collapse into icon rail on desktop hamburger click');

    await hamburgerDesktop.click();
    await dPage.waitForTimeout(250);
    const isSidebarExpanded = await dPage.evaluate(() => {
      const sb = document.getElementById('sidebar');
      return !sb.classList.contains('collapsed');
    });
    assert.ok(isSidebarExpanded, 'Sidebar should expand back on second click');
    console.log('✅ Desktop header & sidebar rail: PASSED');

    await desktopContext.close();

    console.log('\n=========================================');
    console.log('🎉 ALL PLAYWRIGHT E2E TESTS PASSED 100%!');
    console.log('=========================================\n');
  } finally {
    await browser.close();
  }
}

run().catch(err => {
  console.error('\n❌ PLAYWRIGHT E2E TEST FAILED:', err);
  process.exit(1);
});
