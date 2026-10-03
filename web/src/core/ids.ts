/** Ids the server accepts for layouts (`^[a-z0-9-]+$`). */
export function slugify(name: string): string {
  const s = name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return s || "layout";
}

/** A layout id valid for `^[a-z0-9-]+$` and unused. */
export function newLayoutId(name: string, existing: readonly string[]): string {
  const base = slugify(name).slice(0, 48);
  if (!existing.includes(base)) return base;
  for (let n = 2; ; n++) if (!existing.includes(`${base}-${n}`)) return `${base}-${n}`;
}
