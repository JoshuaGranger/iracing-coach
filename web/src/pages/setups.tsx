import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { type Schemas, api, paths } from "../core/api";
import { formatDate, formatDateTime } from "../core/format";
import { savePrefs, usePrefs } from "../core/prefs";
import { useLibraryChoice } from "../core/library";
import { useDebounced, useResource } from "../core/resource";
import { href } from "../core/router";
import { readJson, writeJson } from "../core/store";
import { Chip, Empty, ErrorLine, Loading, Notice } from "../ui/kit";
import { Segmented } from "../ui/segmented";
import { type CarGroup, type Entry, type RacedFilter, carsFilesOnly, carsWithSetups, carsWithValues, fileNames, groupLibrary, groupTrackLibrary, entryUsage } from "../setups/library";
import { Chevron } from "../setups/glyphs";
import {
  type SetupSelection,
  emptySelection,
  fromSetupFilters,
  isEmptySelection,
  parseSelection,
  toSetupFilters,
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

function SetupRow({ name, count, last, to, nested }: { name: string; count: number | null; last: string | null; to: string; nested?: boolean }) {
  return (
    <div class={`s-row s-grid${nested ? " s-version" : ""}`}>
      <a class="s-name" href={to}>
        <span class="s-title">{name}</span>
      </a>
      <span class="num" title="Usage of selected version" aria-label={count === null ? "Session count unknown" : `${count} sessions`}>{count ?? "—"}</span>
      <span class="date" title="Usage of selected version">{last ? formatDate(last) : "—"}</span>
    </div>
  );
}

function SetupEntry({ e, customerId }: { e: Extract<Entry, { kind: "setup" }>; customerId: number }) {
  const newest = e.versions[0]!;
  return (
    <li class="s-entry" data-entry="setup">
      <SetupRow name={e.name} {...entryUsage(e)} to={href({ page: "setup", customerId, setupId: newest.setup.setup_id })} />
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
        <SetupRow name="Garage capture" {...entryUsage(e)} to={href({ page: "setup", customerId, setupId: s.setup_id })} />
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
        <span class="num">{entryUsage(e).count ?? "—"}</span>
        <span class="date">{entryUsage(e).last ? formatDate(entryUsage(e).last!) : "—"}</span>
      </div>
      {open ? (
        <ul class="s-versions" aria-label="Garage captures">
          {e.setups.map((s) => (
            <li key={s.setup_id}>
              <SetupRow nested name={formatDateTime(s.last_seen_at)} count={s.session_count ?? null} last={s.last_used_at ?? null} to={href({ page: "setup", customerId, setupId: s.setup_id })} />
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

/** A file whose values the library cannot decode: the name and the constraint tag. */
function FileEntry({ e }: { e: Extract<Entry, { kind: "file" }> }) {
  return (
    <li class="s-entry unknown" data-entry="file">
      <div class="s-row s-grid">
        <span class="s-name">
          <span class="s-title">{e.name}</span>
          <Chip variant="constraint">No values</Chip>
        </span>
        <span />
        <span />
      </div>
    </li>
  );
}

function CarSection({ g, customerId, track }: { g: CarGroup; customerId: number; track: string | null }) {
  return <section class="s-car" aria-label={g.carName}>
    <h2 class="s-selected-title">{track ?? "Track not recorded"}</h2>
    {track ? <a class="s-track-baseline" href={href({ page: "baseline", customerId }, { car: g.carPath, track })}>Find a baseline for {track}</a> : null}
    <div class="s-cols s-grid" aria-hidden="true"><span>Setup</span><span>Sessions</span><span>Last used</span></div>
    <ul class="s-list">{g.entries.map((e) => e.kind === "setup" ? <SetupEntry key={e.key} e={e} customerId={customerId} /> : e.kind === "captures" ? <Captures key={e.key} e={e} customerId={customerId} /> : <FileEntry key={e.key} e={e} />)}</ul>
  </section>;
}

/** One car whose files have no decoded values: its screen name, the file count (never a setup count), the file names on demand. */
function FilesCar({ g }: { g: CarGroup }) {
  const [open, setOpen] = useState(false);
  const names = fileNames(g);
  const id = `files-${g.carPath.replace(/[^A-Za-z0-9]+/g, "-")}`;
  return (
    <li class="s-fcar" data-car-files={g.carPath} style={`--car:${g.color}`}>
      <button type="button" class="s-fcar-toggle" aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>
        <Chevron down={open} />
        <span class="s-fcar-name">{g.carName}</span>
        <span class="n">
          <strong>{names.length}</strong> {names.length === 1 ? "file" : "files"}
        </span>
      </button>
      <ul id={id} class="s-fcar-files" hidden={!open} aria-label={`${g.carName} files`}>
        {names.map((n, i) => (
          <li key={`${n}-${i}`}>{n}</li>
        ))}
      </ul>
    </li>
  );
}

/** Cars with files and no decoded setup, below the sections under a quiet divider, collapsed until asked for (#74). */
function FilesWithoutValues({ cars }: { cars: CarGroup[] }) {
  const [open, setOpen] = useState(false);
  return (
    <section class="s-files" aria-label="Files without values" data-open={open ? "true" : "false"}>
      <button type="button" class="s-files-toggle" aria-expanded={open} aria-controls="s-files-list" onClick={() => setOpen(!open)}>
        <span class="s-files-rule" aria-hidden="true" />
        <Chevron down={open} />
        <span class="s-files-label">
          Files without values Â· {cars.length} {cars.length === 1 ? "car" : "cars"}
        </span>
        <span class="s-files-rule" aria-hidden="true" />
      </button>
      <ul id="s-files-list" class="s-fcars" hidden={!open}>
        {cars.map((g) => (
          <FilesCar key={g.carPath} g={g} />
        ))}
      </ul>
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
  // `lab` only by `?library=lab` in the URL (#34): no control in the page.
  const library = useLibraryChoice();
  const [sel, setSel] = useSelection(customerId);
  const raced = sel.raced;
  const text = sel.text;
  const q = useDebounced(text.trim(), 250);
  const query = { q: q || undefined, limit: PAGE, library };
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

  const all = useMemo(() => groupLibrary(setups, files), [setups, files]);
  // The Car dropdown offers the cars the setup library holds (the setups response, never the sessions' cars), and a name search
  // does not shrink it: it keeps the list of the unsearched library for this driver and library.
  const offeredCars = useRef<{ at: string; cars: ReturnType<typeof carsWithSetups> } | null>(null);
  const here = `${customerId}|${library}`;
  const present = carsWithSetups(all);
  if (first.data && !q) offeredCars.current = { at: here, cars: present };
  const offered = offeredCars.current?.at === here ? offeredCars.current.cars : present;
  // A saved car the library no longer holds is not a filter: it could not be cleared from a list that does not show it.
  const cars = useMemo(() => {
    if (offered.length === 0) return sel.cars;
    const known = new Set(offered.map((c) => c.carPath.toLowerCase()));
    return sel.cars.filter((c) => known.has(c.toLowerCase()));
  }, [sel.cars, offered]);
  const selectedCars = cars.length ? cars : offered.slice(0, 1).map((c) => c.carPath);
  const effective: SetupSelection = { ...sel, cars: selectedCars };
  const everything = useMemo(() => groupLibrary(setups, files, { cars: new Set(selectedCars), raced }), [setups, files, selectedCars, raced]);
  const groups = useMemo(() => groupTrackLibrary(setups, files, { cars: new Set(selectedCars), raced }).flatMap((g) => carsWithValues(g.cars).map((car) => ({ track: g.track, car }))), [setups, files, selectedCars, raced]);
  const withoutValues = useMemo(() => carsFilesOnly(everything), [everything]);
  const filtering = !isEmptySelection(effective);

  return (
    <div class="setups-page ds">
      <h1>Setups</h1>
      <div class="s-workspace">
        <aside class="s-car-picker" aria-label="Cars">
          <label class="s-phone-car">Car<select aria-label="Car" value={selectedCars[0] ?? ""} onChange={(e) => setSel({ ...sel, cars: [e.currentTarget.value] })}>{offered.map((c) => <option key={c.carPath} value={c.carPath}>{c.carName}</option>)}</select></label>
          <div class="s-desktop-cars"><span class="s-picker-label">Cars · recent first</span>{offered.map((c) => <button key={c.carPath} class="s-car-choice" aria-pressed={selectedCars.includes(c.carPath)} onClick={() => setSel({ ...sel, cars: [c.carPath] })}><span>{c.carName}</span><small>{c.setups} {c.setups === 1 ? "setup" : "setups"}</small></button>)}</div>
        </aside>
        <div class="s-results">
          <div class="s-list-tools">
            <label class="s-search"><span class="visually-hidden">Search setups</span><input type="search" placeholder="Search setups" value={text} onInput={(e) => setSel({ ...sel, text: e.currentTarget.value })} /></label>
            <Segmented label="Raced" options={RACED_FILTERS} value={raced} onChange={(next) => setSel({ ...sel, raced: next })} />
            <a class="s-baseline" href={href({ page: "baseline", customerId }, { car: selectedCars.length === 1 ? selectedCars[0] : undefined })}>Find a baseline</a>
          </div>
      {first.error && !first.data ? <ErrorLine error={first.error} /> : null}
      {first.error && first.data ? <Notice tone="warn">Could not refresh: {first.error.message}</Notice> : null}
      {!first.data && !first.error ? <Loading what="setups" /> : null}
      {first.data && groups.length === 0 && withoutValues.length === 0 ? <Empty title={filtering ? "No setups match" : "No setups yet"} /> : null}
      <div class="s-cars" aria-label="Setups">
        {groups.map(({ track, car }) => <CarSection key={`${car.carPath}|${track}`} g={car} track={track} customerId={customerId} />)}
      </div>
      {first.data && withoutValues.length > 0 ? <FilesWithoutValues cars={withoutValues} /> : null}
        </div>
      </div>
    </div>
  );
}
