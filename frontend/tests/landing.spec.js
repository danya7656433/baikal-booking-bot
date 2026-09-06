import { test, expect } from '@playwright/test';

for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }, { width: 320, height: 568 }, { width: 1920, height: 700 }, { width: 844, height: 390 }]) {
  test(`landing fits ${viewport.width}x${viewport.height}`, async ({ page }) => {
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.setViewportSize(viewport);
    await page.goto('/');
    await expect(page.locator('canvas.ready')).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(1700);
    const hero = page.locator('.lake-hero');
    const box = await hero.boundingBox();
    expect(box.height).toBeLessThan(viewport.height);
    const copy = await page.locator('.hero-caption').boundingBox();
    const bottom = await page.locator('.hero-bottom').boundingBox();
    expect(copy.y + copy.height).toBeLessThan(bottom.y);
    await page.screenshot({ path: `../test-results/landing-${viewport.width}.png` });
    for (const section of ['#place', '#stay', '#moments', '#arrival', '.landing-footer']) {
      await page.locator(section).scrollIntoViewIfNeeded();
      await page.waitForTimeout(200);
    }
    await page.waitForFunction(() => [...document.querySelectorAll('main img')].every(img => img.complete && img.naturalWidth > 0));
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
    await page.screenshot({ path: `../test-results/landing-full-${viewport.width}.png`, fullPage: true });
    expect(errors).toEqual([]);
  });
}

test('scene moves, reacts to pointer, pauses and is disposed on navigation', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('/');
  const canvas = page.locator('canvas.ready');
  await expect(canvas).toBeVisible();
  await page.waitForTimeout(1800);
  const first = await canvas.screenshot();
  await page.mouse.move(1300, 700);
  await page.mouse.click(1200, 720);
  await page.waitForTimeout(700);
  const moved = await canvas.screenshot();
  expect(first.equals(moved)).toBeFalsy();
  await page.locator('#motion-toggle').click();
  await expect(page.locator('#motion-toggle')).toHaveAttribute('aria-label', 'Включить анимацию');
  await page.waitForTimeout(200);
  const paused = await canvas.screenshot();
  await page.waitForTimeout(350);
  expect(paused.equals(await canvas.screenshot())).toBeTruthy();
  await page.getByRole('link', { name: 'Выбрать даты', exact: true }).click();
  await expect(page.locator('#search-form')).toBeVisible();
  await expect(page.locator('canvas')).toHaveCount(0);
  await expect(page.locator('.room-card').first()).toBeVisible();
  await page.locator('.brand').click();
  await expect(page.locator('canvas.ready')).toHaveCount(1);
  await page.locator('.stay-image').first().click();
  await expect(page.locator('dialog')).toBeVisible();
  await expect(page.locator('.gallery-image')).toBeVisible();
  await page.getByRole('button', { name: 'Закрыть', exact: true }).click();
  await page.locator('#arrival summary').first().click();
  await expect(page.locator('#arrival details').first()).toHaveAttribute('open', '');
});

test('reduced motion starts with a still scene', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  await expect(page.locator('canvas.ready')).toBeVisible();
  await expect(page.locator('#motion-toggle')).toHaveAttribute('aria-label', 'Включить анимацию');
  await page.waitForTimeout(300);
  const first = await page.locator('canvas').screenshot();
  await page.mouse.move(700, 500);
  await page.waitForTimeout(300);
  expect(first.equals(await page.locator('canvas').screenshot())).toBeTruthy();
});

test('photograph and booking remain available without WebGL', async ({ page }) => {
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (type, ...args) {
      return type.startsWith('webgl') ? null : original.call(this, type, ...args);
    };
  });
  await page.goto('/');
  await expect(page.locator('.lake-fallback')).toBeVisible();
  await expect(page.locator('#motion-toggle')).toBeHidden();
  expect(await page.locator('.lake-fallback').evaluate(img => img.complete && img.naturalWidth > 0)).toBeTruthy();
  await page.getByRole('link', { name: 'Выбрать даты', exact: true }).click();
  await expect(page.locator('.room-card').first()).toBeVisible();
});
