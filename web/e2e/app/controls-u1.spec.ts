import { type Page, type Route, expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";
import path from "node:path";
import { routeSynthetic } from "../../src/investigation/fixtures/api";
import { buildSectionsAnalysis } from "../../src/investigation/fixtures/sections";
import { DRIVER, appTest, expectNoHorizontalScroll, hash } from "./helpers";
import { ROUND2_DEVICES } from "./round2-shared";

// Web round 5, U1 (backlog #78 #79 #80 #82 #83): one control recipe, focus only for the keyboard, the Spotlight list and the rail.
// The audits visit every page the owner named and measure or click every control they find.
const ANALYSIS = buildSectionsAnalysis("an-103");
const SHOTS = path.resolve(process.cwd(), process.env.R5_U1_SHOTS ?? path.join("target", "round5", "U1"));

async function serve(page: Page): Promise<void> {
  await page.route("**/api/v1/analyses/an-103/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const routed = routeSynthetic(ANALYSIS, `${url.pathname}${url.search}`);
    await route.fulfill({ status: routed.status, contentType: routed.contentType, body: typeof routed.body === "string" ? routed.body : Buffer.from(routed.body) });
  });
}

interface Stop {
  name: string;
  /** Path under the hash, or a function that opens the page (the live editor). */
  open: string | ((page: Page) => Promise<void>);
  ready: (page: Page) => Promise<void>;
}

const PAGES: Stop[] = [
  { name: "sessions", open: `/d/${DRIVER}/sessions`, ready: (p) => expect(p.getByRole("heading", { name: "Sessions", exact: true })).toBeVisible() },
  { name: "setups", open: `/d/${DRIVER}/setups`, ready: (p) => expect(p.getByRole("heading", { name: "Setups", exact: true })).toBeVisible() },
  { name: "setup-detail", open: `/d/${DRIVER}/setups/1`, ready: (p) => expect(p.locator("main h1, main h2").first()).toBeVisible() },
  { name: "live", open: `/d/${DRIVER}/live`, ready: (p) => expect(p.locator("[data-metric=position]")).toContainText("P8") },
  {
    name: "live-editor",
    open: async (p) => {
      await p.goto(hash(`/d/${DRIVER}/live`));
      await expect(p.locator("[data-metric=position]")).toContainText("P8");
      await p.getByRole("button", { name: "Edit layout" }).click();
    },
    ready: (p) => expect(p.getByRole("toolbar", { name: "Layout editor" })).toBeVisible(),
  },
  { name: "race", open: `/d/${DRIVER}/s/103/race`, ready: (p) => expect(p.getByRole("tab", { name: "Traces" })).toBeVisible() },
  { name: "runs", open: `/d/${DRIVER}/s/103/runs`, ready: (p) => expect(p.getByRole("tab", { name: "Runs" })).toBeVisible() },
  { name: "sections", open: `/d/${DRIVER}/s/103/sections`, ready: (p) => expect(p.getByRole("tablist", { name: "Sections" })).toBeVisible() },
  { name: "traces", open: `/d/${DRIVER}/s/103/traces`, ready: (p) => expect(p.locator(".ri-lap-row").first()).toBeVisible() },
];

async function visit(page: Page, stop: Stop, seed?: (page: Page) => Promise<void>): Promise<void> {
  // A clean page: the mock's state is reset and the app reloaded, so a previous page's editing or open panels never carry over.
  await page.goto("about:blank");
  await page.request.post("/__mock/state", { data: { reset: true } });
  await seed?.(page);
  if (typeof stop.open === "string") await page.goto(hash(stop.open));
  else await stop.open(page);
  await stop.ready(page);
  // Let fonts and the first layout settle before anything is measured.
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(250);
}

/** Selector for everything the app draws as a chip, pill, badge, tab, segment, select or button. */
const CONTROLS = [
  "button", "select", "[role=tab]", "[role=button]", ".chip", ".badge", ".btn", ".ms-pill", ".ri-pill-select", ".ri-count-chip", ".sb-pill",
  "[class*=chip]:not(input)", "[class*=pill]", "[class*=badge]",
].join(", ");

/** One line per control class and child kind: the spread of offsets and the pages it shows on, so a failure reads as a list of recipes to fix. */
function summarise(offenders: readonly Offender[]): string[] {
  const groups = new Map<string, { offsets: number[]; pages: Set<string>; example: string }>();
  for (const o of offenders) {
    const control = o.control.replace(/ ".*$/, "");
    const child = o.child.startsWith("text") ? "text" : o.child.replace(/ ".*$/, "");
    const key = `${control} > ${child}`;
    const group = groups.get(key) ?? { offsets: [], pages: new Set<string>(), example: o.control + " / " + o.child };
    group.offsets.push(o.offset);
    group.pages.add(o.page);
    groups.set(key, group);
  }
  return [...groups].map(([key, g]) => `${key}: ${Math.min(...g.offsets)}..${Math.max(...g.offsets)} px on ${[...g.pages].join(",")} (${g.example})`);
}

interface Offender { page: string; control: string; child: string; offset: number }

/** Each visible control's children (elements and text runs) against the control's own centre, vertically, within 1 px. */
async function measureCentres(page: Page): Promise<{ offenders: Offender[]; checked: number; recipe: string[] }> {
  return page.evaluate((selector) => {
    const offenders: { page: string; control: string; child: string; offset: number }[] = [];
    const recipe: string[] = [];
    let checked = 0;
    const canvas = document.createElement("canvas").getContext("2d")!;
    const describe = (el: Element): string => `${el.tagName.toLowerCase()}${el.className && typeof el.className === "string" ? `.${el.className.trim().split(/\s+/).join(".")}` : ""} "${(el.textContent ?? "").trim().replace(/\s+/g, " ").slice(0, 24)}"`;
    const visible = (el: Element): boolean => {
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) return false;
      for (let node: Element | null = el; node; node = node.parentElement) {
        const cs = getComputedStyle(node);
        if (cs.display === "none" || cs.visibility === "hidden") return false;
      }
      return true;
    };
    /** The vertical centre of a text run's capital letters: the line's baseline less half the cap height, which is what reads as the text's centre. */
    const inkCentre = (node: Text): number | null => {
      const parent = node.parentElement;
      if (!parent) return null;
      const cs = getComputedStyle(parent);
      canvas.font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
      const metrics = canvas.measureText("H0");
      const range = document.createRange();
      range.selectNodeContents(node);
      const rects = range.getClientRects();
      if (rects.length === 0) return null;
      // A run that wraps is judged as a block: the mean of its first and last lines' capital-letter centres.
      const centreOf = (rect: DOMRect): number => rect.top + metrics.fontBoundingBoxAscent - metrics.actualBoundingBoxAscent / 2;
      return (centreOf(rects[0]!) + centreOf(rects[rects.length - 1]!)) / 2;
    };
    /** Leaves of a control: text runs by their capital-letter centre, marks and icons by their box. */
    const leaves = (node: Element, out: { label: string; centre: number }[]): void => {
      for (const child of node.childNodes) {
        if (child.nodeType === Node.TEXT_NODE) {
          if (!(child.textContent ?? "").trim()) continue;
          const centre = inkCentre(child as Text);
          if (centre !== null) out.push({ label: `text "${(child.textContent ?? "").trim().slice(0, 20)}"`, centre });
        } else if (child instanceof HTMLElement || child instanceof SVGElement) {
          const cs = getComputedStyle(child);
          if (cs.position === "absolute" || cs.position === "fixed" || cs.display === "none" || cs.visibility === "hidden") continue;
          const r = child.getBoundingClientRect();
          if (r.height === 0 || r.width === 0) continue;
          if (child instanceof SVGElement || !(child.textContent ?? "").trim()) out.push({ label: describe(child), centre: r.top + r.height / 2 });
          else leaves(child, out);
        }
      }
    };
    for (const control of document.querySelectorAll<HTMLElement>(selector)) {
      if (control.closest("svg") || control.tagName === "INPUT" || control.tagName === "SELECT" || !visible(control)) continue;
      const box = control.getBoundingClientRect();
      // A control is one line of chrome, at most the 3 rem toolbar tall; a card or a heading row that happens to be a button is not.
      if (box.height > 48 || box.width < 8) continue;
      const found: { label: string; centre: number }[] = [];
      leaves(control, found);
      if (found.length === 0) continue;
      checked++;
      // Content stacked on purpose (two lines of a cell, 8 px or more apart) is judged as a block by its first and last line; anything
      // closer is one line, and each of its parts is judged alone.
      const sorted = found.slice().sort((a, b) => a.centre - b.centre);
      const lines: { label: string; centre: number }[][] = [];
      for (const part of sorted) {
        const line = lines[lines.length - 1];
        if (line && part.centre - line[0]!.centre < 8) line.push(part);
        else lines.push([part]);
      }
      const mean = (items: { centre: number }[]): number => items.reduce((sum, item) => sum + item.centre, 0) / items.length;
      const parts = lines.length > 1 ? [{ label: "stacked lines", centre: (mean(lines[0]!) + mean(lines[lines.length - 1]!)) / 2 }] : found;
      const centre = box.top + box.height / 2;
      for (const part of parts) {
        const offset = part.centre - centre;
        if (Math.abs(offset) > 1) offenders.push({ page: location.hash, control: describe(control), child: part.label, offset: Math.round(offset * 10) / 10 });
      }
      if (found.length > 1) {
        const cs = getComputedStyle(control);
        const flex = ["inline-flex", "flex", "inline-grid", "grid"].includes(cs.display);
        if (!flex || cs.alignItems !== "center") recipe.push(`${describe(control)} display ${cs.display} align-items ${cs.alignItems}`);
      }
    }
    return { offenders, checked, recipe };
  }, CONTROLS);
}


/**
 * After a pointer click nothing may be left lit. A focus ring or outline, a `:focus-visible` match, a focus border and a class that
 * lights an item without the real state behind it all fail; what stays is the real state itself (a selected tile, an open list, a
 * pressed toggle) and a text field's own focus. A native select keeps focus while its list is open, so for it the audit looks at what
 * is drawn: no outline and no focus shadow.
 */
async function leftovers(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const bad: string[] = [];
    const describe = (el: Element): string => `${el.tagName.toLowerCase()}${typeof el.className === "string" && el.className ? `.${el.className.trim().split(/\s+/).join(".")}` : ""} "${(el.textContent ?? "").trim().replace(/\s+/g, " ").slice(0, 24)}"`;
    const typing = (el: Element | null): boolean => !!el && el.matches("input:not([type=checkbox]):not([type=radio]):not([type=range]):not([type=button]), textarea, [contenteditable=true]");
    const real = (el: Element): boolean => el.matches("[aria-pressed=true], [aria-selected=true], [aria-current]:not([aria-current=false]), [aria-expanded=true], [aria-checked=true], .selected, .on, [data-selected=true]");
    const native = (el: Element): boolean => el.matches("button, a, select, input, summary, textarea");
    const active = document.activeElement;
    if (active && active !== document.body && !typing(active)) {
      const programmatic = active.getAttribute("tabindex") === "-1" || active.getAttribute("role") === "application";
      const heldByDialog = !!active.closest("[role=dialog], [aria-modal=true]");
      const selectedItem = !native(active) && real(active);
      // A chart mark (lap dot, pit tag, run line) keeps its focus: the focus is what shows its readout on a tap, and it draws no ring.
      const chartMark = active instanceof SVGElement || active.matches("li[data-run-line]");
      if (!programmatic && !heldByDialog && !selectedItem && !chartMark && active.tagName !== "SELECT") bad.push(`focus left on ${describe(active)}`);
    }
    for (const el of document.querySelectorAll("*")) {
      if (typing(el) || el.matches("svg *")) continue;
      const cs = getComputedStyle(el);
      const ring = cs.outlineStyle !== "none" && cs.outlineStyle !== "dashed" && cs.outlineStyle !== "dotted" && parseFloat(cs.outlineWidth) > 0;
      if (ring && !real(el)) bad.push(`outline on ${describe(el)}`);
      if (el.matches(":focus-visible") && el.tagName !== "SELECT" && !programmaticOrHeld(el)) bad.push(`:focus-visible on ${describe(el)}`);
      if (el.tagName === "SELECT" && el === document.activeElement && cs.boxShadow !== "none") bad.push(`focus shadow on ${describe(el)}`);
      // A class that lights an item must be backed by the real state: the same item says so in ARIA, or the pointer is on it.
      if (typeof el.className === "string" && /(^|\s)(hl|focus|focused|active|lit)(\s|$)/.test(el.className) && !real(el) && !el.matches(":hover")) bad.push(`lit class on ${describe(el)}`);
    }
    function programmaticOrHeld(el: Element): boolean {
      return el.getAttribute("tabindex") === "-1" && el.matches("[role=listbox], [role=dialog], [role=group]");
    }
    return bad;
  });
}


const CLICKABLE = "main button, main a[href], main select, main [role=tab], main [role=button], main [tabindex='0'], header button, header a[href], header select, [role=dialog] button, [role=toolbar] button, summary";

/** The ith interactive thing on the page: the native controls first, then anything else the pointer treats as clickable (cards, tiles, chart dots). */
function nthTarget({ index, selector }: { index: number; selector: string }): Element | null {
  const natives = [...document.querySelectorAll(selector)];
  const pointer = [...document.querySelectorAll("main *, header *, [role=dialog] *")].filter((el) => {
    if (natives.some((native) => native === el || native.contains(el))) return false;
    if (el.matches("input, textarea, label, option, optgroup, html, body")) return false;
    return getComputedStyle(el).cursor === "pointer";
  });
  const outer = pointer.filter((el) => !pointer.some((other) => other !== el && other.contains(el)));
  return [...natives, ...outer][index] ?? null;
}

async function clickEverything(page: Page, stop: Stop): Promise<{ clicked: number; bad: string[] }> {
  const bad: string[] = [];
  let clicked = 0;
  const start = page.url();
  for (let i = 0; i < 200; i++) {
    const handle = await page.evaluateHandle(nthTarget, { index: i, selector: CLICKABLE });
    const target = handle.asElement();
    if (!target) break;
    const info = await target.evaluate((el) => {
      const r = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      const href = el instanceof HTMLAnchorElement ? el.getAttribute("href") ?? "" : "";
      return { ok: r.width > 0 && r.height > 0 && cs.visibility !== "hidden" && !(el as HTMLButtonElement).disabled, external: href !== "" && !href.startsWith("#"), name: (el.getAttribute("aria-label") ?? el.textContent ?? "").trim().slice(0, 30), tag: el.tagName, cls: (el.getAttribute("class") ?? "").slice(0, 40) };
    }).catch(() => null);
    if (!info || !info.ok || info.external) continue;
    // Controls that leave the page (file pickers, downloads) or end the session are not exercised.
    if (/^(delete|retry|log ?out|download)/i.test(info.name)) continue;
    try {
      await target.scrollIntoViewIfNeeded({ timeout: 800 });
      await target.click({ timeout: 1500, noWaitAfter: true });
    } catch {
      continue;
    }
    clicked++;
    await page.waitForTimeout(40);
    const found = await leftovers(page);
    for (const text of found) bad.push(`${stop.name} · after clicking ${info.tag.toLowerCase()} "${info.name}" (${info.cls}): ${text}`);
    if (page.url() !== start) {
      await page.goBack();
      await page.waitForTimeout(150);
      if (page.url() !== start) await visit(page, stop);
    }
  }
  return { clicked, bad };
}


for (const device of ROUND2_DEVICES) {
  appTest(device, (d) => {
    test.beforeEach(async ({ page }) => serve(page));
    test.setTimeout(240_000);

    test(`#80 every chip, pill, badge, tab, segment and button keeps its label, value and chevron on one centre line (${d.name})`, async ({ page }) => {
      mkdirSync(SHOTS, { recursive: true });
      const all: Offender[] = [];
      const recipe: string[] = [];
      let checked = 0;
      for (const stop of PAGES) {
        await visit(page, stop);
        const result = await measureCentres(page);
        all.push(...result.offenders.map((o) => ({ ...o, page: stop.name })));
        recipe.push(...result.recipe.map((r) => `${stop.name}: ${r}`));
        checked += result.checked;
        expect(result.checked, `${stop.name} has controls`).toBeGreaterThan(0);
        await expectNoHorizontalScroll(page);
        if (["traces", "sessions", "sections", "runs", "race", "setups", "live"].includes(stop.name)) await page.screenshot({ path: path.join(SHOTS, `centres-${stop.name}-${d.viewport.width}.png`) });
      }
      expect(checked).toBeGreaterThan(40);
      expect(summarise(all), "children off their control's centre by more than 1 px").toEqual([]);
      expect(recipe, "controls with several children that are not inline-flex with align-items center").toEqual([]);
    });

    test(`#82 #83 the progress key spans the rail and no two rail headings run together (${d.name})`, async ({ page }) => {
      mkdirSync(SHOTS, { recursive: true });
      await visit(page, PAGES.find((stop) => stop.name === "traces")!);
      const geometry = await page.evaluate(() => {
        const edges = (el: Element) => {
          const r = el.getBoundingClientRect();
          return { left: r.left, right: r.right };
        };
        const rail = edges(document.querySelector(".ri-rail-bar")!);
        const bar = edges(document.querySelector(".ri-progress-bar")!);
        const [first, last] = [...document.querySelectorAll(".ri-progress-key > span")].map(edges);
        const headings = [...document.querySelectorAll(".ri-rail-cols > span")]
          .filter((el) => (el.textContent ?? "").trim() !== "" && getComputedStyle(el).display !== "none")
          .map((el) => {
            const range = document.createRange();
            range.selectNodeContents(el);
            const r = range.getBoundingClientRect();
            return { text: (el.textContent ?? "").trim(), left: r.left, right: r.right };
          });
        return { rail, bar, first: first!, last: last!, headings };
      });
      // The gradient runs from the first label to the last across the whole key; the labels sit at the rail's ends.
      expect(geometry.bar.left - geometry.first.right, "gap after the first label").toBeLessThan(14);
      expect(geometry.last.left - geometry.bar.right, "gap before the last label").toBeLessThan(14);
      expect(geometry.bar.right - geometry.bar.left, "gradient width").toBeGreaterThan((geometry.rail.right - geometry.rail.left) * 0.6);
      expect(geometry.first.left - geometry.rail.left, "first label at the rail start").toBeLessThan(20);
      expect(geometry.rail.right - geometry.last.right, "last label at the rail end").toBeLessThan(20);
      await expect(page.locator(".ri-progress-key")).toContainText("L1");
      // Adjacent headings (Lap Time Δ S1 S2 S3 Fuel Inc Pit) keep a clear gutter: the ink of one never comes within 12 px of the next.
      const tight: string[] = [];
      for (let i = 0; i + 1 < geometry.headings.length; i++) {
        const gap = geometry.headings[i + 1]!.left - geometry.headings[i]!.right;
        if (gap < 12) tight.push(`${geometry.headings[i]!.text} | ${geometry.headings[i + 1]!.text}: ${gap.toFixed(1)} px`);
      }
      expect(tight, "rail headings closer than 12 px").toEqual([]);
      await page.locator(".ri-lap-rail").screenshot({ path: path.join(SHOTS, `rail-${d.viewport.width}.png`) });
    });

    test(`#79 the Spotlight and Layout lists are the filters' styled dropdown and offer only what is drawn (${d.name})`, async ({ page }) => {
      mkdirSync(SHOTS, { recursive: true });
      // One saved layout besides the built-in Default, written the way the app writes it.
      await visit(page, PAGES.find((stop) => stop.name === "traces")!, async (p) => {
        const saved = await p.request.put(`/api/v1/drivers/${DRIVER}/trace-layouts/analysis-layout-seeded`, { data: { name: "Custom", body: { rows: [{ row_id: "c-speed", signals: ["speed"] }, { row_id: "c-brake", signals: ["brake"] }] } } });
        expect(saved.ok()).toBeTruthy();
      });
      const actions = page.locator(".ri-trace-actions");
      // Not a native select: a pill button with a list under it, the way the filters are.
      await expect(actions.locator("select")).toHaveCount(0);
      const pill = actions.getByRole("button", { name: /Spotlight/ });
      await expect(pill).toHaveAttribute("aria-haspopup", "listbox");
      const rows = page.locator(".ri-lap-row");
      const drawn = async (): Promise<number[]> => (await rows.evaluateAll((els) => els.filter((el) => el.getAttribute("aria-pressed") === "true").map((el) => Number(el.getAttribute("data-lap")))));
      const railTimes = async (): Promise<Map<number, string>> => new Map(await rows.evaluateAll((els) => els.map((el) => [Number(el.getAttribute("data-lap")), el.querySelector(".ri-tm")?.textContent ?? ""] as const)));
      const offered = async () => page.getByRole("listbox", { name: "Spotlight" }).getByRole("option").evaluateAll((els) => els.map((el) => ({ key: el.getAttribute("data-key"), text: (el.querySelector(".ss-opt-label")?.textContent ?? "").trim() })));

      // Add two more laps by the rail; the list is exactly those drawn, in rail order, with the rail's times.
      await page.locator(".ri-lap-row[data-lap='12']").click();
      await page.locator(".ri-lap-row[data-lap='20']").click();
      const selected = await drawn();
      expect(selected.length).toBeGreaterThanOrEqual(3);
      await pill.click();
      const times = await railTimes();
      const listed = await offered();
      expect(listed[0]).toEqual({ key: "", text: "None" });
      expect(listed.slice(1).map((o) => Number(o.key))).toEqual(selected);
      for (const o of listed.slice(1)) expect(o.text).toBe(`L${o.key} · ${times.get(Number(o.key))}`);
      // Open state is the pill's own marked state; no option is lit before the pointer or a key touches the list.
      await expect(pill).toHaveAttribute("aria-expanded", "true");
      await expect(page.locator(".ss-opt.hl")).toHaveCount(0);
      await page.screenshot({ path: path.join(SHOTS, `spotlight-open-${d.viewport.width}.png`) });
      // Choosing a lap spotlights it: the pill reads it, the rail row carries the spotlight, nothing is added to the chart.
      await page.getByRole("option", { name: /^L12 · / }).click();
      await expect(pill).toContainText("L12 ·");
      await expect(page.getByRole("listbox", { name: "Spotlight" })).toHaveCount(0);
      await expect(page.locator(".ri-lap-row[data-lap='12']")).toHaveClass(/spotlight/);
      expect(await drawn()).toEqual(selected);
      // A lap taken off the chart leaves the list, and the spotlight with it.
      await page.locator(".ri-lap-row[data-lap='12']").click();
      await expect(pill).not.toContainText("L12");
      await pill.click();
      expect((await offered()).map((o) => o.key)).not.toContain("12");
      // Keyboard: Escape closes and keeps the pill; Down and Enter choose.
      await page.keyboard.press("Escape");
      await expect(page.getByRole("listbox", { name: "Spotlight" })).toHaveCount(0);
      await expect(pill).toBeFocused();
      await page.keyboard.press("ArrowDown");
      await expect(page.getByRole("listbox", { name: "Spotlight" })).toBeVisible();
      // The list opened on None; two more Downs reach the second drawn lap.
      await page.keyboard.press("ArrowDown");
      await page.keyboard.press("ArrowDown");
      await page.keyboard.press("Enter");
      const second = (await drawn())[1]!;
      await expect(pill).toContainText(`L${second} ·`);
      await expect(pill).toBeFocused();
      // Layout: with two saved layouts the pill offers both; choosing one switches the chart's rows and the pill reads it.
      const layout = actions.getByRole("button", { name: /Layout/ });
      await expect(layout).toContainText("Default");
      const rowCount = await page.locator(".ri-row-label").count();
      await layout.click();
      expect((await page.getByRole("listbox", { name: "Layout" }).getByRole("option").allTextContents()).map((text) => text.trim())).toEqual(["Default", "Custom"]);
      await page.getByRole("option", { name: "Custom" }).click();
      await expect(layout).toContainText("Custom");
      await expect(page.locator(".ri-row-label")).toHaveCount(2);
      await layout.click();
      await page.getByRole("option", { name: "Default" }).click();
      await expect(layout).toContainText("Default");
      await expect(page.locator(".ri-row-label")).toHaveCount(rowCount);
      await expectNoHorizontalScroll(page);
    });

    test(`#79 #80 the map colour is the shared dropdown, drawn over the chart, with no ring after a pointer choice (${d.name})`, async ({ page }) => {
      mkdirSync(SHOTS, { recursive: true });
      await visit(page, PAGES.find((stop) => stop.name === "traces")!);
      const panel = page.locator(".ri-track-panel");
      // Not a native select: the pill and list of the filters and the Spotlight, reading `MAP value` with its chevron.
      await expect(panel.locator("select")).toHaveCount(0);
      const pill = panel.getByRole("button", { name: /^Map / });
      await expect(pill).toHaveAttribute("aria-haspopup", "listbox");
      await expect(pill).toContainText("Traces");
      await pill.click();
      const list = page.getByRole("listbox", { name: "Map" });
      await expect(list).toBeVisible();
      await expect(pill).toHaveAttribute("aria-expanded", "true");
      // The modes the recording carries, the current one marked, none lit before the pointer or a key touches the list.
      expect((await list.getByRole("option").allTextContents()).map((text) => text.trim())).toEqual(["Traces", "Speed", "Throttle", "Brake", "Tire load"]);
      await expect(list.getByRole("option", { name: "Traces" })).toHaveAttribute("aria-selected", "true");
      await expect(page.locator(".ss-opt.hl")).toHaveCount(0);
      // The list hangs below the map strip over the traces card: not clipped by the strip, not under the chart. Each option is the
      // topmost element at its own centre.
      await list.scrollIntoViewIfNeeded();
      const hidden = await list.getByRole("option").evaluateAll((els) => els.filter((el) => {
        const r = el.getBoundingClientRect();
        const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return !hit || !el.contains(hit);
      }).map((el) => (el.textContent ?? "").trim()));
      expect(hidden, "options hidden under something else").toEqual([]);
      await page.screenshot({ path: path.join(SHOTS, `map-colour-open-${d.viewport.width}.png`) });
      // A pointer choice colours the map by it, closes the list and leaves nothing lit.
      await list.getByRole("option", { name: "Speed" }).click();
      await expect(page.getByRole("listbox", { name: "Map" })).toHaveCount(0);
      await expect(pill).toContainText("Speed");
      await expect(page.locator(".ri-map-legend[data-mode=speed]")).toBeVisible();
      await expect.poll(() => page.locator(".ri-metric-segment").count()).toBeGreaterThan(0);
      await page.waitForTimeout(60);
      expect(await leftovers(page)).toEqual([]);
      // Keyboard: Down opens the list on the current mode, Up moves, Enter chooses and gives the focus back to the pill.
      await pill.focus();
      await page.keyboard.press("ArrowDown");
      await expect(list).toBeVisible();
      await page.keyboard.press("ArrowUp");
      await page.keyboard.press("Enter");
      await expect(pill).toContainText("Traces");
      await expect(pill).toBeFocused();
      await expect(page.locator(".ri-map-legend")).toHaveCount(0);
      // The pill's capitals, value and chevron sit on its centre line (the #80 audit, for this control alone).
      const offsets = await pill.evaluate((el) => {
        const box = el.getBoundingClientRect();
        const centre = box.top + box.height / 2;
        return [...el.children].filter((child) => getComputedStyle(child).display !== "none").map((child) => { const r = child.getBoundingClientRect(); return Math.abs(r.top + r.height / 2 - centre); });
      });
      for (const offset of offsets) expect(offset).toBeLessThanOrEqual(1);
      await expectNoHorizontalScroll(page);
    });

    test(`#78 the keyboard keeps its focus ring, and the ring goes with the pointer (${d.name})`, async ({ page }) => {
      await visit(page, PAGES.find((stop) => stop.name === "traces")!);
      const ring = () => page.evaluate(() => {
        const el = document.activeElement;
        if (!el || el === document.body) return null;
        const cs = getComputedStyle(el);
        return { cls: (el.getAttribute("class") ?? "").split(" ")[0], ring: cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) > 0, mode: document.documentElement.dataset.input ?? "" };
      });
      // Tab to a lap row: the ring shows, and the arrow keys move it to the next row.
      for (let i = 0; i < 60; i++) {
        await page.keyboard.press("Tab");
        if ((await ring())?.cls === "ri-lap-row") break;
      }
      expect(await ring(), "Tab reaches a lap row with a ring").toEqual({ cls: "ri-lap-row", ring: true, mode: "keyboard" });
      await page.keyboard.press("ArrowDown");
      expect(await ring(), "the next row has the ring").toEqual({ cls: "ri-lap-row", ring: true, mode: "keyboard" });
      // A pointer press anywhere ends it: no ring, no focus on the row that was pressed.
      await page.locator(".ri-lap-row[data-lap='12']").click();
      await page.waitForTimeout(60);
      expect(await ring()).toBeNull();
      expect(await page.evaluate(() => document.documentElement.dataset.input)).toBe("pointer");
      // The next key brings the ring back.
      await page.keyboard.press("Tab");
      const back = await ring();
      expect(back?.mode).toBe("keyboard");
      expect(back?.ring, "the ring returns with the keyboard").toBe(true);
    });

    test(`#78 a pointer click leaves no focus ring, outline or lit class on any control of any page (${d.name})`, async ({ page }) => {
      let clicked = 0;
      const bad: string[] = [];
      for (const stop of PAGES) {
        await visit(page, stop);
        const result = await clickEverything(page, stop);
        clicked += result.clicked;
        bad.push(...result.bad);
      }
      expect(clicked, "controls clicked").toBeGreaterThan(80);
      expect(bad).toEqual([]);
    });
  });
}
