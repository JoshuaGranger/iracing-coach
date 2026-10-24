import { createStore, readJson, writeJson } from "../core/store";

/**
 * Visual looks: five alternative skins over the same pages, for choosing what to keep (owner request, 2026-10-24). A look restyles
 * the chrome, the controls and the type; the information, the layouts of the session analysis views and every racing data colour stay
 * as they are. `current` is the shipped look (styles.css and the generated theme). Each other look is one stylesheet scoped to
 * `html[data-look="<id>"]`, loaded the first time it is chosen, so the shipped look carries none of their rules.
 */
export type LookId = "current" | "pitwall" | "console" | "glass" | "programme" | "hud";

export interface Look {
  id: LookId;
  name: string;
  /** One line: the idea, the type and the signature controls. */
  idea: string;
}

export const LOOKS: readonly Look[] = [
  { id: "current", name: "Current", idea: "The shipped look: warm graphite, copper brand, pill controls." },
  { id: "pitwall", name: "Pit Wall", idea: "Broadcast timing tower: black and white, Saira condensed italic, slanted tabs, checkered rule." },
  { id: "console", name: "Console", idea: "Engineer's data console: JetBrains Mono, hairline grid, icon rail, IDE tabs, [x] toggles." },
  { id: "glass", name: "Glass", idea: "Night glass: Inter, frosted panels, sidebar, segmented tabs, switches, sentence case." },
  { id: "programme", name: "Programme", idea: "Race programme: Fraunces serif figures, ruled pages, numbered sections, text controls." },
  { id: "hud", name: "HUD", idea: "Cockpit HUD: Oxanium, chamfered frames, corner brackets, LED toggles, scan grid." },
];

const STORAGE_KEY = "coach.look";

const loaders: Record<Exclude<LookId, "current">, () => Promise<unknown>> = {
  pitwall: () => import("./pitwall.css"),
  console: () => import("./console.css"),
  glass: () => import("./glass.css"),
  programme: () => import("./programme.css"),
  hud: () => import("./hud.css"),
};

export function isLookId(value: unknown): value is LookId {
  return typeof value === "string" && LOOKS.some((look) => look.id === value);
}

/** `?look=<id>` in the page URL (before the hash) wins over the stored choice. */
export function initialLook(search: string, stored: unknown): LookId {
  const fromUrl = new URLSearchParams(search).get("look");
  if (isLookId(fromUrl)) return fromUrl;
  return isLookId(stored) ? stored : "current";
}

export const currentLook = createStore<LookId>(initialLook(typeof location === "undefined" ? "" : location.search, readJson<unknown>(STORAGE_KEY, null)));

/** The next look in the list, wrapping; `step` -1 goes back. */
export function stepLook(id: LookId, step: 1 | -1): LookId {
  const index = LOOKS.findIndex((look) => look.id === id);
  return LOOKS[(index + step + LOOKS.length) % LOOKS.length]!.id;
}

/** Loads the look's stylesheet, then sets `html[data-look]`, so a page never shows the new look half-styled. */
export async function applyLook(id: LookId): Promise<void> {
  if (id !== "current") await loaders[id]();
  if (currentLook.get() !== id) return;
  const root = document.documentElement;
  if (id === "current") delete root.dataset.look;
  else root.dataset.look = id;
}

export function chooseLook(id: LookId): void {
  currentLook.set(id);
  writeJson(STORAGE_KEY, id);
  void applyLook(id);
}
