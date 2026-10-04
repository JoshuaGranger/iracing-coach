import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { savePrefs, usePrefs } from "../core/prefs";
import { api, paths } from "../core/api";
import { useResource } from "../core/resource";
import { href } from "../core/router";
import { readJson, writeJson } from "../core/store";
import { Empty, ErrorLine, Loading, Notice, PageHead } from "../ui/kit";
import { EventCard, SetupBadge } from "../sessions/event-card";
import { useLibrary } from "../sessions/library";
import {
  FILTER_IDS,
  FILTER_LABEL,
  type FilterId,
  type Selection,
  carColorIndex,
  emptySelection,
  facetOptions,
  fromSessionFilters,
  groupEvents,
  isEmptySelection,
  parseSelection,
  phaseOf,
  toSessionFilters,
  toggleOption,
} from "../sessions/model";
import { FilterChip, FilterChips, MultiSelect, type Option } from "../ui/multi-select";
import { watchTabs } from "../sessions/tab-memory";
import "../sessions/sessions.css";

export { groupEvents };

// The tab each session was left on follows the hash from the first load, whichever page the driver lands on.
watchTabs();

const NOUN: Record<FilterId, string> = { car: "cars", track: "tracks", series: "series", type: "types", setup: "setups" };
const hintKey = (customerId: number) => `coach.sessions.filters.${customerId}`;
/** Events drawn at first; "Older sessions" draws the next batch. */
const BATCH = 24;

/**
 * The selection lives in the driver's prefs (`session_filters`), so it follows the driver to another device. The device keeps a copy
 * only so the first paint is already filtered while the prefs load.
 */
function useSelection(customerId: number): [Selection, (next: Selection) => void] {
  const { prefs } = usePrefs(customerId);
  const [sel, setSel] = useState<Selection>(() => parseSelection(readJson<unknown>(hintKey(customerId), null)) ?? emptySelection());
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
    const stored = fromSessionFilters(prefs.session_filters);
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
      void savePrefs(customerId, { session_filters: toSessionFilters(next) });
    },
  ];
}

function CarStripe({ index }: { index: number }) {
  return <i class="sx-stripe" data-car={index} aria-hidden="true" />;
}

export function SessionsPage({ customerId }: { customerId: number }) {
  const lib = useLibrary(customerId);
  const all = lib.data;
  const sessions = all ?? [];
  const [sel, setSel] = useSelection(customerId);
  const [shown, setShown] = useState(BATCH);
  useEffect(() => setShown(BATCH), [customerId, sel]);

  const colors = useMemo(() => carColorIndex(sessions), [all]);
  const facets = useMemo(() => {
    const out = {} as Record<FilterId, ReturnType<typeof facetOptions>>;
    for (const id of FILTER_IDS) out[id] = facetOptions(id, sessions, colors);
    return out;
  }, [all, colors]);
  const events = useMemo(() => groupEvents(sessions, sel), [all, sel]);
  const now = useMemo(() => new Date(), [all]);

  const drivers = useResource(paths.drivers, () => api.drivers());
  const live = drivers.data?.drivers.find((d) => d.customer_id === customerId)?.live;

  const options = (id: FilterId): Option[] =>
    facets[id].map((o) => ({
      key: o.key,
      label: o.label,
      count: o.count,
      mark:
        id === "car" ? (
          <CarStripe index={o.car ?? 0} />
        ) : id === "type" ? (
          <span class="sx-letter" data-phase={o.phase}>
            {o.phase}
          </span>
        ) : id === "setup" ? (
          <SetupBadge type={o.key === "Fixed" ? "Fixed" : "Open"} />
        ) : undefined,
    }));

  /** Filters the library has nothing for are not offered (no Setup filter until a session carries its setup type). */
  const filterIds = FILTER_IDS.filter((id) => facets[id].length > 0 || sel[id].length > 0);
  const chips = filterIds.flatMap((id) =>
    sel[id].map((key) => {
      const o = facets[id].find((f) => f.key === key);
      const label = o?.label ?? (id === "type" ? phaseOf(key).label : key);
      return { id, key, label, car: id === "car" ? (o?.car ?? 0) : undefined };
    }),
  );

  const filtered = !isEmptySelection(sel);
  const visible = events.slice(0, shown);

  return (
    <div class="sx">
      <PageHead title="Sessions">
        {live ? (
          <a class="btn btn-accent" href={href({ page: "live", customerId })}>
            Live now
          </a>
        ) : null}
      </PageHead>
      {all ? (
        <div class="filter-bar" role="search" aria-label="Filter sessions">
          {filterIds.map((id) => (
            <MultiSelect key={id} label={FILTER_LABEL[id]} noun={NOUN[id]} options={options(id)} selected={sel[id]} onChange={(next) => setSel({ ...sel, [id]: next })} />
          ))}
          <FilterChips count={chips.length} onClear={() => setSel(emptySelection())}>
            {chips.map((c) => (
              <FilterChip
                key={`${c.id}:${c.key}`}
                label={c.label}
                color={c.car !== undefined ? `var(--car-${c.car + 1})` : undefined}
                mark={c.car !== undefined ? <CarStripe index={c.car} /> : undefined}
                onRemove={() => setSel(toggleOption(sel, c.id, c.key))}
              />
            ))}
          </FilterChips>
        </div>
      ) : null}
      {lib.error && !all ? <ErrorLine error={lib.error} /> : null}
      {lib.error && all ? <Notice tone="warn">Could not refresh {"·"} {lib.error.message}</Notice> : null}
      {!all && !lib.error ? <Loading what="sessions" /> : null}
      {all && events.length === 0 ? (
        filtered ? (
          <Empty title="No matching sessions">
            <button type="button" class="sx-act" onClick={() => setSel(emptySelection())}>
              Clear filters
            </button>
          </Empty>
        ) : (
          <Empty title="No sessions yet">Drive with the collector running.</Empty>
        )
      ) : null}
      <div class="sx-events">
        {visible.map((ev) => (
          <EventCard key={ev.key} ev={ev} customerId={customerId} colors={colors} now={now} />
        ))}
      </div>
      {events.length > visible.length ? (
        <button type="button" class="btn load-more" onClick={() => setShown((n) => n + BATCH)}>
          Older sessions
        </button>
      ) : null}
    </div>
  );
}
