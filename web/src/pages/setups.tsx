import { useEffect, useMemo, useState } from "preact/hooks";
import { type Schemas, api, paths } from "../core/api";
import { formatDate, plural } from "../core/format";
import { useDebounced, useResource } from "../core/resource";
import { href } from "../core/router";
import { Chip, Empty, ErrorLine, Loading, Notice, PageHead } from "../ui/kit";
import { sameNameAs, seenSpan, setupTitle, uniqueSources } from "../setups/model";

type Setup = Schemas["SetupSummary"];
const PAGE = 50;
const FILES_SHOWN = 20;

export function SetupRow({ s, customerId, shared = 0 }: { s: Setup; customerId: number; shared?: number }) {
  return (
    <li>
      <a class="row-link setup-row" href={href({ page: "setup", customerId, setupId: s.setup_id })}>
        <span class="row-main">
          <span class="row-title">{setupTitle(s)}</span>
          {s.raced ? <Chip tone="good">Raced</Chip> : null}
          {uniqueSources(s.sources).map((x) => (
            <Chip key={x}>{x}</Chip>
          ))}
        </span>
        <span class="row-meta">
          {s.car_name ?? s.car_path} · {plural(s.observation_count, "sighting")} · {shared > 0 ? seenSpan(s) : `last ${formatDate(s.last_seen_at)}`}
          {s.names.length > 1 ? ` · ${s.names.length} file names` : ""}
          {shared > 0 ? ` · Same file name, different values (${plural(shared, "other")})` : null}
        </span>
      </a>
    </li>
  );
}

export default function SetupsPage({ customerId }: { customerId: number }) {
  const [car, setCar] = useState("");
  const [text, setText] = useState("");
  const [racedOnly, setRacedOnly] = useState(false);
  const q = useDebounced(text.trim(), 250);
  const options = useResource(paths.carsAndTracks(customerId), () => api.carsAndTracks(customerId));
  const query = { car_path: car || undefined, q: q || undefined, raced_only: racedOnly || undefined, limit: PAGE };
  const key = paths.setups(customerId, query);
  const first = useResource(key, (signal) => api.setups(customerId, query, { signal }));
  const [more, setMore] = useState<{ setups: Setup[]; cursor: string | null } | null>(null);
  const [showAllFiles, setShowAllFiles] = useState(false);
  useEffect(() => setMore(null), [first.data, key]);

  const setups = useMemo(() => [...(first.data?.setups ?? []), ...(more?.setups ?? [])], [first.data, more]);
  const cursor = more ? more.cursor : first.data?.next_cursor ?? null;
  const files = (first.data?.unlinked_files ?? []).filter((f) => !car || f.car_folder === car || f.relative_path.toLowerCase().includes(car.toLowerCase()));
  const shownFiles = showAllFiles ? files : files.slice(0, FILES_SHOWN);

  return (
    <>
      <PageHead title="Setups">
        <a class="btn btn-accent" href={href({ page: "baseline", customerId })}>
          Find a baseline
        </a>
        <a class="btn" href={href({ page: "tuning", customerId, analysisId: null })}>
          Progressive tuning
        </a>
      </PageHead>
      <div class="filters" role="search">
        <label class="select-field">
          <span>Car</span>
          <select value={car} onChange={(e) => setCar((e.currentTarget as HTMLSelectElement).value)}>
            <option value="">All cars</option>
            {(options.data?.cars ?? []).map((c) => (
              <option key={c.car_path} value={c.car_path}>
                {c.car_name ?? c.car_path}
              </option>
            ))}
          </select>
        </label>
        <label class="search">
          <span class="visually-hidden">Search setups</span>
          <input type="search" placeholder="Name or car" value={text} onInput={(e) => setText((e.currentTarget as HTMLInputElement).value)} />
        </label>
        <label class="check">
          <input type="checkbox" checked={racedOnly} onChange={(e) => setRacedOnly((e.currentTarget as HTMLInputElement).checked)} />
          <span>Raced only</span>
        </label>
      </div>
      {first.error && !first.data ? <ErrorLine error={first.error} /> : null}
      {first.error && first.data ? <Notice tone="warn">Could not refresh: {first.error.message}</Notice> : null}
      {!first.data && !first.error ? <Loading what="setups" /> : null}
      {first.data && setups.length === 0 && files.length === 0 ? (
        <Empty title="No setups match">Setups appear from every recording, from garage visits with the collector running, and from exported files.</Empty>
      ) : null}
      {setups.length > 0 ? (
        <ul class="rows" aria-label="Setups">
          {setups.map((s) => (
            <SetupRow key={s.setup_id} s={s} customerId={customerId} shared={sameNameAs(s, setups).length} />
          ))}
        </ul>
      ) : null}
      {cursor ? (
        <button
          type="button"
          class="btn load-more"
          onClick={async () => {
            const page = await api.setups(customerId, { ...query, cursor });
            setMore({ setups: [...(more?.setups ?? []), ...page.setups], cursor: page.next_cursor ?? null });
          }}
        >
          More setups
        </button>
      ) : null}
      {files.length > 0 ? (
        <section class="unknown-files" aria-label="Setup files with values unknown">
          <h2>
            Values unknown <span class="count">{files.length}</span>
          </h2>
          <p class="sub">Files iRacing stores encrypted. They list here by name; their values appear once a recording or garage visit loads them unmodified.</p>
          <ul class="rows">
            {shownFiles.map((f) => (
              <li key={f.sha256 + f.relative_path} class="row-link row-static">
                <span class="row-main">
                  <span class="row-title">{f.relative_path}</span>
                  <Chip tone="warn">Values unknown</Chip>
                  {!f.present ? <Chip>File removed</Chip> : null}
                </span>
                <span class="row-meta">{f.car_folder}</span>
              </li>
            ))}
          </ul>
          {files.length > FILES_SHOWN && !showAllFiles ? (
            <button type="button" class="btn" onClick={() => setShowAllFiles(true)}>
              Show all {files.length}
            </button>
          ) : null}
        </section>
      ) : null}
    </>
  );
}
