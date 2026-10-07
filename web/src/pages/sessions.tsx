import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { savePrefs, usePrefs } from "../core/prefs";
import { api, paths } from "../core/api";
import { useResource } from "../core/resource";
import { href } from "../core/router";
import { readJson, writeJson } from "../core/store";
import { useLibraryChoice } from "../core/library";
import { carAccent } from "../core/palette";
import { FilterBar, type FilterControl } from "../ui/filter-bar";
import { Empty, ErrorLine, Loading, Notice } from "../ui/kit";
import { EventRow, LockIcon } from "../sessions/event-row";
import { useLibrary } from "../sessions/library";
import {
  FILTER_IDS,
  FILTER_LABEL,
  type FilterId,
  type PhaseToggles,
  type Selection,
  TOGGLE_CODES,
  allPhases,
  carColorIndex,
  emptySelection,
  facetOptions,
  fromSessionFilters,
  groupEvents,
  isEmptySelection,
  selectionCount,
  parsePhaseToggles,
  parseSelection,
  phaseOf,
  toSessionFilters,
  togglePhase,
} from "../sessions/model";
import { MultiSelect, type Option } from "../ui/multi-select";
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

/** The phase chips are a view choice of this device (backlog #88), not a filter: they follow no driver and no prefs. */
const PHASES_KEY = "coach.sessions.phases";

function usePhaseToggles(): [PhaseToggles, (next: PhaseToggles) => void] {
  const [t, setT] = useState<PhaseToggles>(() => parsePhaseToggles(readJson<unknown>(PHASES_KEY, null)));
  return [
    t,
    (next) => {
      setT(next);
      writeJson(PHASES_KEY, next);
    },
  ];
}

/**
 * R · Q · P (backlog #88): one toggle per phase in its hue, all on by default. Off hides that phase's lines in every card; the last
 * chip on cannot be turned off (it reads disabled). Fixed widths, so the heading never moves when one is toggled.
 */
function PhaseChips({ value, onChange }: { value: PhaseToggles; onChange: (next: PhaseToggles) => void }) {
  const on = TOGGLE_CODES.filter((c) => value[c]).length;
  return (
    <div class="seg sx-toggles" role="group" aria-label="Phases shown">
      {TOGGLE_CODES.map((code) => {
        const label = phaseOf(code === "R" ? "Race" : code === "Q" ? "Qualify" : "Practice").label;
        const last = value[code] && on === 1;
        return (
          <button
            key={code}
            type="button"
            class="sx-toggle"
            data-phase={code}
            aria-pressed={value[code]}
            aria-disabled={last || undefined}
            aria-label={label}
            title={last ? `${label} · the last phase shown` : value[code] ? `Hide ${label.toLowerCase()}` : `Show ${label.toLowerCase()}`}
            onClick={(e) => {
              onChange(togglePhase(value, code));
              // pointer use leaves no focus ring (#78)
              if (e.detail > 0) (e.currentTarget as HTMLButtonElement).blur();
            }}
          >
            <span>{code}</span>
          </button>
        );
      })}
    </div>
  );
}

/** The car's own accent (`core/palette.ts`), the same on the event cards, on Setups and in the filter. */
function CarStripe({ car }: { car: string }) {
  return <i class="sx-stripe" style={`--car:${carAccent(car)}`} aria-hidden="true" />;
}

export function SessionsPage({ customerId }: { customerId: number }) {
  // `lab` only by `?library=lab` in the URL (#34): no control in the page.
  const library = useLibraryChoice();
  const lib = useLibrary(customerId, library);
  const all = lib.data;
  const sessions = all ?? [];
  const [sel, setSel] = useSelection(customerId);
  const [phases, setPhases] = usePhaseToggles();
  const [shown, setShown] = useState(BATCH);
  useEffect(() => setShown(BATCH), [customerId, sel, library]);

  const colors = useMemo(() => carColorIndex(sessions), [all]);
  const facets = useMemo(() => {
    const out = {} as Record<FilterId, ReturnType<typeof facetOptions>>;
    for (const id of FILTER_IDS) out[id] = facetOptions(id, sessions, colors);
    return out;
  }, [all, colors]);
  const events = useMemo(() => groupEvents(sessions, sel, phases), [all, sel, phases]);
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
          <CarStripe car={o.key} />
        ) : id === "type" ? (
          <span class="sx-letter" data-phase={o.phase}>
            {o.phase}
          </span>
        ) : id === "setup" ? (
          <span class="sx-lock" aria-hidden="true">
            <LockIcon type={o.key === "Fixed" ? "Fixed" : "Open"} />
          </span>
        ) : undefined,
    }));

  /** Filters the library has nothing for are not offered (no Setup filter until a session carries its setup type). */
  const loadedIds = FILTER_IDS.filter((id) => facets[id].length > 0 || sel[id].length > 0);
  // A filter once offered stays for the visit: loading the library never reflows the heading or closes an open surface.
  const seen = useRef<{ customer: number; ids: Set<FilterId> | null }>({ customer: customerId, ids: null });
  if (seen.current.customer !== customerId) seen.current = { customer: customerId, ids: null };
  if (all) seen.current.ids = new Set([...(seen.current.ids ?? []), ...loadedIds]);
  const filterIds = seen.current.ids ? FILTER_IDS.filter((id) => seen.current.ids!.has(id)) : FILTER_IDS.filter((id) => id !== "setup");
  const controls: FilterControl[] = [
    ...filterIds.map((id) => ({
      id,
      label: FILTER_LABEL[id],
      node: <MultiSelect label={FILTER_LABEL[id]} noun={NOUN[id]} options={options(id)} selected={sel[id]} onChange={(next) => setSel({ ...sel, [id]: next })} />,
    })),
  ];

  const filtered = !isEmptySelection(sel) || TOGGLE_CODES.some((c) => !phases[c]);
  const visible = events.slice(0, shown);

  return (
    <div class="sx">
      <FilterBar
        title={
          <>
            <h1>Sessions</h1>
            {live ? (
              <a class="btn btn-accent" href={href({ page: "live", customerId })}>
                Live now
              </a>
            ) : null}
          </>
        }
        controls={controls}
        activeCount={selectionCount(sel)}
        aside={<PhaseChips value={phases} onChange={setPhases} />}
      />
      {lib.error && !all ? <ErrorLine error={lib.error} /> : null}
      {lib.error && all ? <Notice tone="warn">Could not refresh {"·"} {lib.error.message}</Notice> : null}
      {!all && !lib.error ? <Loading what="sessions" /> : null}
      {all && events.length === 0 ? (
        filtered ? (
          <Empty title="No matching sessions">
            <button type="button" class="sx-act" onClick={() => {
                setSel(emptySelection());
                setPhases(allPhases());
              }}
            >
              Clear filters
            </button>
          </Empty>
        ) : (
          <Empty title="No sessions yet">Drive with the collector running.</Empty>
        )
      ) : null}
      <div class="sx-events">
        {visible.map((ev) => (
          <EventRow key={ev.key} ev={ev} customerId={customerId} now={now} />
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
