import { useEffect, useMemo, useState } from "preact/hooks";
import { type Schemas, api, paths } from "../core/api";
import { formatDate, formatDateTime } from "../core/format";
import { useDebounced, useResource } from "../core/resource";
import { href } from "../core/router";
import { Empty, ErrorLine, Loading, Notice, PageHead, Stripe } from "../ui/kit";
import { type CarGroup, type Entry, type RacedFilter, type Version, carColors, groupLibrary } from "../setups/library";
import { CheckeredFlag, Chevron } from "../setups/glyphs";
import { FilterChip, FilterChips, MultiSelect } from "../ui/multi-select";
import "../setups/setups.css";

type Setup = Schemas["SetupSummary"];
const PAGE = 200;

const RACED_FILTERS: readonly [RacedFilter, string][] = [
  ["all", "All"],
  ["raced", "Raced"],
  ["not-raced", "Not raced"],
];

function Values({ known }: { known: boolean }) {
  return known ? <span class="vk">Values known</span> : <span class="vu">Values unknown</span>;
}

function Raced({ raced }: { raced: boolean }) {
  return raced ? (
    <span class="raced">
      <CheckeredFlag />
      Raced
    </span>
  ) : (
    <span />
  );
}

function Seen({ n }: { n: number }) {
  return <span class="num" title={`${n} sightings`}>{n}×</span>;
}

function VersionRow({ v, customerId, name }: { v: Version; customerId: number; name: string }) {
  const s = v.setup;
  return (
    <li class="s-row s-grid s-version" data-version={v.number}>
      <a class="s-name" href={href({ page: "setup", customerId, setupId: s.setup_id })} aria-label={`${name}, version ${v.number}`}>
        <span class="s-title">Version {v.number}</span>
        {v.latest ? <span class="tag">Latest</span> : null}
        <small>{formatDate(s.first_seen_at) === formatDate(s.last_seen_at) ? formatDate(s.last_seen_at) : `${formatDate(s.first_seen_at)} to ${formatDate(s.last_seen_at)}`}</small>
      </a>
      <Values known />
      <Raced raced={s.raced} />
      <Seen n={s.observation_count} />
      <span class="date">{formatDate(s.last_seen_at)}</span>
      <span class="go">
        <Chevron />
      </span>
    </li>
  );
}

function SetupEntry({ e, customerId }: { e: Extract<Entry, { kind: "setup" }>; customerId: number }) {
  const [open, setOpen] = useState(false);
  const newest = e.versions[0]!;
  const many = e.versions.length > 1;
  return (
    <li class="s-entry" data-entry="setup">
      <div class="s-row s-grid">
        <a class="s-name" href={href({ page: "setup", customerId, setupId: newest.setup.setup_id })}>
          <span class="s-title">{e.name}</span>
        </a>
        <Values known />
        <Raced raced={e.raced} />
        <Seen n={e.seen} />
        <span class="date">{formatDate(e.last)}</span>
        {many ? (
          <button type="button" class="versions" aria-expanded={open} aria-label={`${e.name}, ${e.versions.length} versions`} onClick={() => setOpen(!open)}>
            {e.versions.length} versions
            <Chevron down={open} />
          </button>
        ) : (
          <span class="go">
            <Chevron />
          </span>
        )}
      </div>
      {many && open ? (
        <ul class="s-versions" aria-label={`${e.name} versions`}>
          {e.versions.map((v) => (
            <VersionRow key={v.setup.setup_id} v={v} customerId={customerId} name={e.name} />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

function Captures({ e, customerId }: { e: Extract<Entry, { kind: "captures" }>; customerId: number }) {
  const [open, setOpen] = useState(false);
  const n = e.setups.length;
  const one = n === 1;
  return (
    <li class={`s-entry${one ? "" : " stack"}`} data-entry="captures">
      <div class="s-row s-grid">
        {one ? (
          <a class="s-name" href={href({ page: "setup", customerId, setupId: e.setups[0]!.setup_id })}>
            <span class="s-title">Garage capture</span>
            <small>Unnamed</small>
          </a>
        ) : (
          <button type="button" class="s-name s-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
            <span class="s-title">Garage captures</span>
            <span class="count" aria-label={`${n} captures`}>{n}</span>
            <small>Unnamed</small>
          </button>
        )}
        <Values known />
        <Raced raced={e.raced} />
        <Seen n={e.seen} />
        <span class="date">{formatDate(e.last)}</span>
        {one ? (
          <span class="go">
            <Chevron />
          </span>
        ) : (
          <span class="go" aria-hidden="true">
            <Chevron down={open} />
          </span>
        )}
      </div>
      {!one && open ? (
        <ul class="s-versions" aria-label="Garage captures">
          {e.setups.map((s) => (
            <li key={s.setup_id} class="s-row s-grid s-version">
              <a class="s-name" href={href({ page: "setup", customerId, setupId: s.setup_id })}>
                <span class="s-title">{formatDateTime(s.last_seen_at)}</span>
              </a>
              <Values known />
              <Raced raced={s.raced} />
              <Seen n={s.observation_count} />
              <span class="date">{formatDate(s.last_seen_at)}</span>
              <span class="go">
                <Chevron />
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

function FileEntry({ e }: { e: Extract<Entry, { kind: "file" }> }) {
  return (
    <li class="s-entry unknown" data-entry="file">
      <div class="s-row s-grid">
        <span class="s-name">
          <span class="s-title">{e.name}</span>
        </span>
        <Values known={false} />
        <span />
        <span />
        <span />
        <span />
      </div>
    </li>
  );
}

function CarCard({ g, customerId }: { g: CarGroup; customerId: number }) {
  return (
    <section class="s-car" style={`--car:${g.color}`} aria-label={g.carName}>
      <header class="s-car-head">
        <h2>{g.carName}</h2>
        <span class="n">
          <strong>{g.setups}</strong> {g.setups === 1 ? "setup" : "setups"} · <strong>{g.raced}</strong> raced
        </span>
      </header>
      <div class="s-cols s-grid" aria-hidden="true">
        <span>Setup</span>
        <span>Values</span>
        <span>Raced</span>
        <span class="r">Seen</span>
        <span class="r">Last</span>
        <span />
      </div>
      <ul class="s-list">
        {g.entries.map((e) =>
          e.kind === "setup" ? <SetupEntry key={e.key} e={e} customerId={customerId} /> : e.kind === "captures" ? <Captures key={e.key} e={e} customerId={customerId} /> : <FileEntry key={e.key} e={e} />,
        )}
      </ul>
    </section>
  );
}

export default function SetupsPage({ customerId }: { customerId: number }) {
  const [cars, setCars] = useState<string[]>([]);
  const [text, setText] = useState("");
  const [raced, setRaced] = useState<RacedFilter>("all");
  const q = useDebounced(text.trim(), 250);
  const options = useResource(paths.carsAndTracks(customerId), () => api.carsAndTracks(customerId));
  const query = { q: q || undefined, limit: PAGE };
  const key = paths.setups(customerId, query);
  const first = useResource(key, (signal) => api.setups(customerId, query, { signal }));
  const [more, setMore] = useState<{ setups: Setup[]; cursor: string | null } | null>(null);
  useEffect(() => setMore(null), [first.data, key]);

  const setups = useMemo(() => [...(first.data?.setups ?? []), ...(more?.setups ?? [])], [first.data, more]);
  const files = useMemo(() => (first.data?.unlinked_files ?? []).filter((f) => !q || f.relative_path.toLowerCase().includes(q.toLowerCase())), [first.data, q]);
  const cursor = more ? more.cursor : first.data?.next_cursor ?? null;

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
  const filtering = cars.length > 0 || raced !== "all" || text.trim() !== "";
  const nameOf = (path: string) => all.find((g) => g.carPath === path)?.carName ?? path;

  return (
    <div class="setups-page ds">
      <PageHead title="Setups">
        <a class="pill primary" href={href({ page: "baseline", customerId })}>
          Find a baseline
        </a>
        <a class="pill" href={href({ page: "tuning", customerId, analysisId: null })}>
          Progressive tuning
        </a>
      </PageHead>
      <div class="filter-bar s-filters" role="search" aria-label="Filter setups">
        <MultiSelect label="Car" noun="cars" options={carOptions} selected={cars} onChange={setCars} />
        <label class="s-search">
          <span class="visually-hidden">Search setups</span>
          <input type="search" placeholder="Name" value={text} onInput={(e) => setText((e.currentTarget as HTMLInputElement).value)} />
        </label>
        <div class="seg" role="group" aria-label="Raced">
          {RACED_FILTERS.map(([id, label]) => (
            <button key={id} type="button" class={raced === id ? "on" : ""} aria-pressed={raced === id} onClick={() => setRaced(id)}>
              {label}
            </button>
          ))}
        </div>
        <FilterChips count={cars.length} onClear={() => setCars([])}>
          {cars.map((c) => {
            const color = colors.get(c.toLowerCase());
            return <FilterChip key={c} label={nameOf(c)} color={color} mark={<Stripe color={color ?? "var(--color-text-muted)"} />} onRemove={() => setCars(cars.filter((x) => x !== c))} />;
          })}
        </FilterChips>
      </div>
      {first.error && !first.data ? <ErrorLine error={first.error} /> : null}
      {first.error && first.data ? <Notice tone="warn">Could not refresh: {first.error.message}</Notice> : null}
      {!first.data && !first.error ? <Loading what="setups" /> : null}
      {first.data && groups.length === 0 ? <Empty title={filtering ? "No setups match" : "No setups yet"} /> : null}
      <div class="s-cars" aria-label="Setups">
        {groups.map((g) => (
          <CarCard key={g.carPath} g={g} customerId={customerId} />
        ))}
      </div>
      {cursor ? (
        <button
          type="button"
          class="pill load-more"
          onClick={async () => {
            const page = await api.setups(customerId, { ...query, cursor });
            setMore({ setups: [...(more?.setups ?? []), ...page.setups], cursor: page.next_cursor ?? null });
          }}
        >
          More setups
        </button>
      ) : null}
    </div>
  );
}

