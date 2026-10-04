import { useMemo, useState } from "preact/hooks";
import { resolve, signalRecordedInRace, type TraceData } from "../geometry/trace-model";
import { activeLayout, type LayoutSet } from "../layout-sets";
import { SIGNAL_DEFINITIONS } from "../signals";
import type { TraceRowSpec } from "../types";

export type LayoutCommandRequest = { type: "select"; id: string } | { type: "new" } | { type: "copy" } | { type: "delete" };

export interface TraceToolboxProps {
  data: TraceData;
  rows: readonly TraceRowSpec[];
  maximumRows: number;
  layoutSet: LayoutSet;
  selectedRowId: string | null;
  onAddRow(signalId: string): void;
  onPlace(rowId: string, signalId: string): void;
  onRemoveSignal(rowId: string, signalId: string): void;
  onRemoveRow(rowId: string): void;
  onLayoutCommand(command: LayoutCommandRequest): void;
  onClose(): void;
}

/**
 * Whether this recording carries a signal. The catalog decides (audit T5): a catalog signal outside the default channel set is
 * recorded and is fetched when a chart asks for it. Loaded channels are checked for values.
 */
export function signalInRecording(data: TraceData, id: string): boolean {
  if (id === "delta") return data.aux.sessionTime !== null || data.signals.has("session-time");
  if (id === "tire-wear") return data.signals.has("tire-stress");
  if (!data.signals.has(id)) return false;
  return data.table.channels.has(id) ? signalRecordedInRace(data, id) : true;
}

export function TraceToolbox(props: TraceToolboxProps) {
  const { data, rows } = props;
  const [search, setSearch] = useState("");
  const selectedRow = rows.find((row) => row.row_id === props.selectedRowId) ?? null;
  const active = activeLayout(props.layoutSet);
  const library = useMemo(() => {
    const query = search.trim().toLowerCase();
    const groups = new Map<string, { id: string; name: string; unit: string; recorded: boolean }[]>();
    for (const definition of SIGNAL_DEFINITIONS) {
      const resolved = resolve(data, definition.id);
      if (!resolved) continue;
      if (query && !`${resolved.name} ${resolved.unit}`.toLowerCase().includes(query)) continue;
      const list = groups.get(resolved.group) ?? [];
      list.push({ id: definition.id, name: resolved.name, unit: resolved.unit, recorded: signalInRecording(data, definition.id) });
      groups.set(resolved.group, list);
    }
    for (const list of groups.values()) list.sort((a, b) => Number(b.recorded) - Number(a.recorded) || a.name.localeCompare(b.name));
    return [...groups.entries()];
  }, [data, search]);
  const count = library.reduce((sum, [, list]) => sum + list.length, 0);
  return (
    <aside id="analysis-trace-toolbox" class="ri-card ri-toolbox open" aria-label="Customize traces">
      <header class="ri-toolbox-head">
        <strong>Customize</strong>
        <button type="button" class="ri-icon-pill" aria-label="Close" title="Close" onClick={props.onClose}>×</button>
      </header>
      <div class="ri-toolbox-body">
        <section class="ri-layout-box" aria-label="Chart layouts">
          <div class="ri-toolbox-section"><div><small>Layout</small><strong data-active-layout={active.id}>{active.name}</strong></div><span>{rows.length} / {props.maximumRows}</span></div>
          <div class="ri-layout-actions">
            <button type="button" class="ri-pill-button small" onClick={() => props.onLayoutCommand({ type: "new" })}>New</button>
            <button type="button" class="ri-pill-button small" onClick={() => props.onLayoutCommand({ type: "copy" })}>Copy</button>
            <button type="button" class="ri-pill-button small" disabled={active.builtin} title={active.builtin ? "Default stays" : undefined} onClick={() => props.onLayoutCommand({ type: "delete" })}>Delete</button>
          </div>
        </section>
        {selectedRow && (
          <section>
            <div class="ri-toolbox-section"><div><small>Chart</small><strong>{selectedRow.signals.map((id) => resolve(data, id)?.name ?? id).join(" / ")}</strong></div><span>{selectedRow.signals.length} / 2</span></div>
            <div class="ri-selected-signals">
              {selectedRow.signals.map((id, index) => (
                <div class="ri-selected-signal" key={id}>
                  <i class={index === 0 ? "solid" : "dashed"} title={index === 0 ? "Solid" : "Dashed"} aria-hidden="true" />
                  <span><strong>{resolve(data, id)?.name ?? id}</strong><small>{resolve(data, id)?.unit ?? ""}</small></span>
                  <button type="button" class="ri-icon-pill small" data-analysis-no-drag aria-label={`Remove ${resolve(data, id)?.name ?? id}`} title="Remove" onClick={() => props.onRemoveSignal(selectedRow.row_id, id)}>×</button>
                </div>
              ))}
            </div>
            <button type="button" class="ri-pill-button small" data-analysis-no-drag disabled={rows.length <= 1} onClick={() => props.onRemoveRow(selectedRow.row_id)}>Remove chart</button>
          </section>
        )}
        <section class="ri-library">
          <div class="ri-toolbox-section"><div><small>Channels</small><strong>Add</strong></div><span>{count}</span></div>
          <label class="ri-search"><span class="ri-sr">Search channels</span><input type="search" placeholder="Search" value={search} onInput={(event) => setSearch((event.currentTarget as HTMLInputElement).value)} /></label>
          <div class="ri-library-list" role="list" aria-label="Channels">
            {count === 0 && <div class="ri-muted ri-pad">No match</div>}
            {library.map(([group, list]) => (
              <section class="ri-signal-category" key={group}>
                <h3>{group}</h3>
                {list.map((signal) => (
                  <article
                    class={`ri-signal-card${signal.recorded ? "" : " unavailable"}`} role="listitem" tabIndex={0} key={signal.id}
                    data-analysis-drag-signal={signal.id} data-signal-id={signal.id} data-signal-name={signal.name} data-recorded={signal.recorded}
                    aria-label={`${signal.name}, ${signal.unit}${signal.recorded ? "" : ", not recorded"}`}
                    title={signal.recorded ? signal.name : `${signal.name} · not recorded`}
                    onKeyDown={(event: KeyboardEvent) => {
                      if (event.key !== "Enter" && event.key !== " ") return;
                      event.preventDefault();
                      if (selectedRow) props.onPlace(selectedRow.row_id, signal.id);
                      else props.onAddRow(signal.id);
                    }}
                  >
                    <span class="ri-grip" aria-hidden="true">⋮⋮</span>
                    <div><strong>{signal.name}</strong><small>{signal.unit}{signal.recorded ? "" : " · not recorded"}</small></div>
                    <button type="button" class="ri-icon-pill small" data-analysis-no-drag disabled={rows.length >= props.maximumRows} aria-label={`Add ${signal.name} chart`} title="Add chart" onKeyDown={(event: KeyboardEvent) => event.stopPropagation()} onClick={() => props.onAddRow(signal.id)}>+</button>
                  </article>
                ))}
              </section>
            ))}
          </div>
        </section>
      </div>
      <footer class="ri-toolbox-foot"><span>Drop between charts: add</span><span>Drop on a chart: pair</span><span><kbd>Alt</kbd> + ↑↓: move</span></footer>
    </aside>
  );
}
