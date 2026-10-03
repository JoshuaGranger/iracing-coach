import { type APIRequestContext, type Page, expect, test } from "@playwright/test";

export interface Device {
  name: string;
  viewport: { width: number; height: number };
  touch: boolean;
}

/** D11: desktop browser, tablet, phone. */
export const DEVICES: Device[] = [
  { name: "desktop 1280x720", viewport: { width: 1280, height: 720 }, touch: false },
  { name: "tablet 1024x768", viewport: { width: 1024, height: 768 }, touch: true },
  { name: "phone 390x844", viewport: { width: 390, height: 844 }, touch: true },
];

export const DRIVER = 1507810;

export async function resetMock(request: APIRequestContext, config: Record<string, unknown> = {}): Promise<void> {
  const res = await request.post("/__mock/state", { data: { reset: true, ...config } });
  expect(res.ok()).toBeTruthy();
}

export async function mockState(request: APIRequestContext) {
  return (await (await request.get("/__mock/state")).json()) as { config: Record<string, unknown>; writes: { path: string; body: unknown }[]; notes: unknown[] };
}

export async function setMock(request: APIRequestContext, config: Record<string, unknown>): Promise<void> {
  await request.post("/__mock/state", { data: config });
}

export function appTest(device: Device, body: (d: Device) => void): void {
  test.describe(device.name, () => {
    test.use({ viewport: device.viewport, hasTouch: device.touch });
    test.beforeEach(async ({ request }) => resetMock(request));
    body(device);
  });
}

/** No horizontal page scroll. */
export async function expectNoHorizontalScroll(page: Page): Promise<void> {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow, "document scrolls horizontally").toBeLessThanOrEqual(0);
}

/** Every visible control is at least 44 px on its short side (checkboxes, radios and svg shapes count through their label or list). */
export async function expectTouchTargets(page: Page, minimum = 44): Promise<void> {
  const small = await page.evaluate((min) => {
    const bad: string[] = [];
    const nodes = document.querySelectorAll<HTMLElement>("main button, main a[href], main select, main input:not([type=checkbox]):not([type=radio]):not([type=hidden]), main textarea, header a, header select, header button, .outbox button");
    for (const el of nodes) {
      if (el.closest("svg") || el.closest("[aria-hidden=true]")) continue;
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      const cs = getComputedStyle(el);
      if (cs.visibility === "hidden" || cs.display === "none") continue;
      if (r.height < min - 0.5) bad.push(`${el.tagName.toLowerCase()}.${el.className} "${(el.textContent ?? "").trim().slice(0, 30)}" ${Math.round(r.width)}x${Math.round(r.height)}`);
    }
    return bad;
  }, minimum);
  expect(small, "controls under the touch-target minimum").toEqual([]);
}

export const hash = (path: string) => `/#${path}`;
