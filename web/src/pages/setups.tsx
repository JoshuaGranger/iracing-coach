import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { type Schemas, api, paths } from "../core/api";
import { formatDate, formatDateTime } from "../core/format";
import { savePrefs, usePrefs } from "../core/prefs";
import { useDebounced, useResource } from "../core/resource";
import { href } from "../core/router";
import { readJson, writeJson } from "../core/store";
import { Chip, Empty, ErrorLine, Loading, Notice, Stripe } from "../ui/kit";
import { FilterBar, type FilterControl } from "../ui/filter-bar";
import { FilterChip, MultiSelect } from "../ui/multi-select";
import { Segmented } from "../ui/segmented";
import { type CarGroup, type Entry, type RacedFilter, carColors, groupLibrary, racedCountOf } from "../setups/library";
import { Chevron, RacedMark } from "../setups/glyphs";
import {
  MORE_ROWS,
  type SetupSelection,
  activeCount,
  emptySelection,
  fromSetupFilters,
  isEmptySelection,
  parseSelection,
  readCollapsed,
  revealMore,
  revealed,
  shownRows,
  toSetupFilters,
  toggleCollapsed,
  writeCollapsed,
} from "../setups/sections";
import "../setups/setups.css";

type Setup = Schemas["SetupSummary"];
const PAGE = 200;
/** The library is read page by page until it is whole, so a car's count never depends on which page it was in. */
const MAX_PAGES = 25;

const RACED_FILTERS = [
  { value: "all", label: "All" },
  { value: "raced", label: "Raced" },
  { value: "not-raced", label: "Not raced" },
] as const satisfies readonly { value: RacedFilter; label: string }[];

/** Width of the Name field and of the three-segment Raced control at their minimum, in rem (the FilterBar's fit rule). */
const NAME_MIN_REM = 12;
const RACED_MIN_REM = 17;

function SetupRow({ name, raced, count, last, to, nested }: { name: string; raced: boolean; count: number | null; last: string; to: string; nested?: boolean }) {
  return (
    <div class={`s-row s-grid${nested ? " s-version" : ""}`}>
      <a class="s-name" href={to}>
        <span class="s-title">{name}</span>
      </a>
      {raced ? <RacedMark count={count} /> : <span />}
      <span class="date">{formatDate(last)}</span>
    </div>
  );
}

function SetupEntry({ e, customerId }: { e: Extract<Entry, { kind: "setup" }>; customerId: number }) {
  const newest = e.versions[0]!;
  return (
    <li class="s-entry" data-entry="setup">
      <SetupRow name={e.name} raced={e.raced} count={e.racedCount} last={e.last} to={href({ page: "setup", customerId, setupId: newest.setup.setup_id })} />
    </li>
  );
}

function Captures({ e, customerId }: { e: Extract<Entry, { kind: "captures" }>; customerId: number }) {
  const [open, setOpen] = useState(false);
  const n = e.setups.length;
  if (n === 1) {
    const s = e.setups[0]!;
    return (
      <li class="s-entry" data-entry="captures">
        <SetupRow name="Garage capture" raced={s.raced} count={e.racedCount} last={s.last_seen_at} to={href({ page: "setup", customerId, setupId: s.setup_id })} />
      </li>
    );
  }
  return (
    <li class="s-entry stack" data-entry="captures">
      <div class="s-row s-grid">
        <button type="button" class="s-name s-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
          <span class="s-title">Garage captures</span>
          <span class="count" aria-label={`${n} captures`}>
            {n}
          </span>
          <Chevron down={open} />
        </button>
        {e.raced ? <RacedMark count={e.racedCount} /> : <span />}
        <span class="date">{formatDate(e.last)}</span>
      </div>
      {open ? (
        <ul class="s-versions" aria-label="Garage captures">
          {e.setups.map((s) => (
            <li key={s.setup_id}>
              <SetupRow nested name={formatDateTime(s.last_seen_at)} raced={s.raced} count={racedCountOf(s)} last={s.last_seen_at} to={href({ page: "setup", customerId, setupId: s.setup_id })} />
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

/** A file whose values the library cannot decode: the name, and why there is nothing more. */
function FileEntry({ e }: { e: Extract<Entry, { kind: "file" }> }) {
  return (
    <li class="s-entry unknown" data-entry="file">
      <div class="s-row s-grid">
        <span class="s-name">
          <span class="s-title">{e.name}</span>
          <Chip variant="constraint" title="The file's values are not decoded">
            No values
          </Chip>
        </span>
        <span />
        <span />
      </div>
    </li>
  );
}

function CarSection({
  g,
  customerId,
  collapsed,
  onToggle,
  shown,
  onMore,
}: {
  g: CarGroup;
  customerId: number;
  collapsed: boolean;
  onToggle(): void;
  shown: number;
  onMore(): void;
}) {
  const id = `car-${g.carPath.replace(/[^A-Za-z0-9]+/g, "-")}`;
  const rest = g.entries.length - shown;
  return (
    <section class="s-car" style={`--car:${g.color}`} aria-label={g.carName} data-collapsed={collapsed ? "true" : "false"}>
      <h2 class="s-car-head">
        <button type="button" class="s-car-toggle" aria-expanded={!collapsed} aria-controls={id} onClick={onToggle}>
          <Chevron down={!collapsed} />
          <span class="s-car-text">
            <span class="s-car-name">{g.carName}</span>
            <span class="n">
              <strong>{g.setups}</strong> {g.setups === 1 ? "setup" : "setups"} · <strong>{g.raced}</strong> raced
            </span>
          </span>
        </button>
      </h2>
      <div id={id} hidden={collapsed}>
        <ul class="s-list">
          {g.entries.slice(0, shown).map((e) =>
            e.kind === "setup" ? <SetupEntry key={e.key} e={e} customerId={customerId} /> : e.kind === "captures" ? <Captures key={e.key} e={e} customerId={customerId} /> : <FileEntry key={e.key} e={e} />,
          )}
        </ul>
        {rest > 0 ? (
          <button type="button" class="pill s-more" data-remaining={rest} onClick={onMore}>
            Show {MORE_ROWS} more
          </button>
        ) : null}
      </div>
    </section>
  );
}

const hintKey = (customerId: number) => `coach.setups.filters.${customerId}`;

/**
 * The selection lives in the driver's prefs (`setup_filters`), so it follows the driver to another device. The device keeps a copy
 * only so the first paint is already filtered while the prefs load, and so Back restores it.
 */
function useSelection(customerId: number): [SetupSelection, (next: SetupSelection) => void] {
  const { prefs } = usePrefs(customerId);
  const [sel, setSel] = useState<SetupSelection>(() => parseSelection(readJson<unknown>(hintKey(customerId), null)) ?? emptySelection());
  const touched = useRef(false);
  const adopted = useRef<number | null>(null);

  useEffect(() => {
    touched.current = false;
    adopted.current = null;
    setSel(parseSelection(readJson<unknown>(hintKey(customerId), null)) ?? emptySelection());
  }, [customerId]);
  useEffect(() => {
    if (!prefs || touched.current || adopted.current === customerId) return;
    adopted.current = customerId;
    const stored = fromSetupFilters(prefs.setup_filters);
    if (stored) {
      setSel(stored);
      writeJson(hintKey(customerId), stored);
    }
  }, [prefs, customerId]);

  return [
    sel,
    (next) => {
      touched.current = true;
      setSel(next);
      writeJson(hintKey(customerId), next);
      void savePrefs(customerId, { setup_filters: toSetupFilters(next) });
    },
  ];
}

export default function SetupsPage({ customerId }: { customerId: number }) {
  const [sel, setSel] = useSelection(customerId);
  const { cars, raced } = sel;
  const text = sel.text;
  const q = useDebounced(text.trim(), 250);
  const options = useResource(paths.carsAndTracks(customerId), () => api.carsAndTracks(customerId));
  const query = { q: q || undefined, limit: PAGE };
  const key = paths.setups(customerId, query);
  const first = useResource(key, (signal) => api.setups(customerId, query, { signal }));
  const [more, setMore] = useState<{ setups: Setup[]; cursor: string | null; pages: number } | null>(null);
  useEffect(() => setMore(null), [first.data, key]);
  const cursor = more ? more.cursor : first.data?.next_cursor ?? null;
  const pages = more?.pages ?? 1;
  useEffect(() => {
    if (!cursor || pages >= MAX_PAGES) return undefined;
    const ctl = new AbortController();
    api
      .setups(customerId, { ...query, cursor }, { signal: ctl.signal })
      .then((page) => setMore((m) => ({ setups: [...(m?.setups ?? []), ...page.setups], cursor: page.next_cursor ?? null, pages: (m?.pages ?? 1) + 1 })))
      .catch(() => undefined);
    return () => ctl.abort();
  }, [cursor, key, customerId]);

  const setups = useMemo(() => [...(first.data?.setups ?? []), ...(more?.setups ?? [])], [first.data, more]);
  const files = useMemo(() => (first.data?.unlinked_files ?? []).filter((f) => !q || f.relative_path.toLowerCase().includes(q.toLowerCase())), [first.data, q]);

  const colors = useMemo(
    () =>
      carColors(
        (options.data?.cars ?? []).map((c) => c.car_path),
        setups.map((s) => s.car_path),
      ),
    [options.data, setups],
  );
  const all = useMemo(() => groupLibrary(setups, files, colors), [setups, files, colors]);
  const groups = useMemo(() => groupLibrary(setups, files, colors, { cars: new Set(cars), raced }), [setups, files, colors, cars, raced]);
  const carOptions = all.map((g) => ({ key: g.carPath, label: g.carName, count: g.setups, mark: <Stripe color={g.color} /> }));
  const filtering = !isEmptySelection(sel);
  const nameOf = (path: string) => all.find((g) => g.carPath === path)?.carName ?? path;

  // A car header sticks under the app bar, whatever height the bar wraps to.
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const bar = document.querySelector<HTMLElement>(".topbar");
    if (!bar) return undefined;
    const set = () => root.current?.style.setProperty("--s-top", `${bar.getBoundingClientRect().height}px`);
    set();
    if (typeof ResizeObserver !== "function") return undefined;
    const observer = new ResizeObserver(set);
    observer.observe(bar);
    return () => observer.disconnect();
  }, []);

  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [, bump] = useState(0);
  const toggle = (carPath: string) => {
    const next = toggleCollapsed(collapsed, carPath);
    setCollapsed(next);
    writeCollapsed(next);
  };

  const controls: FilterControl[] = [
    { id: "car", label: "Car", node: <MultiSelect label="Car" noun="cars" options={carOptions} selected={cars} onChange={(next) => setSel({ ...sel, cars: next })} /> },
    {
      id: "name",
      label: "Name",
      minRem: NAME_MIN_REM,
      node: (
        <label class="s-search">
          <span class="visually-hidden">Search setups</span>
          <input type="search" placeholder="Name" value={text} onInput={(e) => setSel({ ...sel, text: (e.currentTarget as HTMLInputElement).value })} />
        </label>
      ),
    },
    { id: "raced", label: "Raced", minRem: RACED_MIN_REM, node: <Segmented label="Raced" options={RACED_FILTERS} value={raced} onChange={(next) => setSel({ ...sel, raced: next })} /> },
  ];

  return (
    <div class="setups-page ds" ref={root}>
      <FilterBar
        title={
          <>
            <h1>Setups</h1>
            <a class="s-baseline" href={href({ page: "baseline", customerId })}>
              Find a baseline
            </a>
          </>
        }
        controls={controls}
        activeCount={activeCount(sel)}
        onClear={() => setSel(emptySelection())}
      >
        {cars.length > 0
          ? cars.map((c) => {
              const color = colors.get(c.toLowerCase());
              return <FilterChip key={c} label={nameOf(c)} color={color} mark={<Stripe color={color ?? "var(--color-text-muted)"} />} onRemove={() => setSel({ ...sel, cars: cars.filter((x) => x !== c) })} />;
            })
          : null}
      </FilterBar>
      {first.error && !first.data ? <ErrorLine error={first.error} /> : null}
      {first.error && first.data ? <Notice tone="warn">Could not refresh: {first.error.message}</Notice> : null}
      {!first.data && !first.error ? <Loading what="setups" /> : null}
      {first.data && groups.length === 0 ? <Empty title={filtering ? "No setups match" : "No setups yet"} /> : null}
      <div class="s-cars" aria-label="Setups">
        {groups.map((g) => (
          <CarSection
            key={g.carPath}
            g={g}
            customerId={customerId}
            collapsed={collapsed.has(g.carPath.toLowerCase())}
            onToggle={() => toggle(g.carPath)}
            shown={shownRows(g.entries.length, revealed.get(customerId, g.carPath))}
            onMore={() => {
              revealed.set(customerId, g.carPath, revealMore(revealed.get(customerId, g.carPath)));
              bump((n) => n + 1);
            }}
          />
        ))}
      </div>
    </div>
  );
}
