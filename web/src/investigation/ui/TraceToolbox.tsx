import { useMemo, useState } from "preact/hooks";
import { signalRecordedInRace, resolve, type TraceData } from "../geometry/trace-model";
import { SIGNAL_DEFINITIONS } from "../signals";
import type { TraceRowSpec } from "../types";

export interface TraceToolboxProps {
  data: TraceData;
  rows: readonly TraceRowSpec[];
  maximumRows: number;
  layoutName: string;
  selectedRowId: string | null;
  onAddRow(signalId: string): void;
  onPlace(rowId: string, signalId: string): void;
  onRemoveSignal(rowId: string, signalId: string): void;
  onRemoveRow(rowId: string): void;
  onClose(): void;
}

export function TraceToolbox(props: TraceToolboxProps) {
  const { data, rows } = props;
  const [search, setSearch] = useState("");
  const selectedRow = rows.find((row) => row.row_id === props.selectedRowId) ?? null;
  const library = useMemo(() => {
    const query = search.trim().toLowerCase();
    const groups = new Map<string, { id: string; name: string; unit: string; recorded: boolean }[]>();
    for (const definition of SIGNAL_DEFINITIONS) {
      const resolved = resolve(data, definition.id);
      if (!resolved) continue;
      if (query && !`${resolved.name} ${resolved.unit}`.toLowerCase().includes(query)) continue;
      const list = groups.get(resolved.group) ?? [];
      list.push({ id: definition.id, name: resolved.name, unit: resolved.unit, recorded: signalRecordedInRace(data, definition.id) });
      groups.set(resolved.group, list);
    }
    for (const list of groups.values()) list.sort((a, b) => Number(b.recorded) - Number(a.recorded) || a.name.localeCompare(b.name));
    return [...groups.entries()];
  }, [data, search]);
  const count = library.reduce((sum, [, list]) => sum + list.length, 0);
  return (
    <aside id="analysis-trace-toolbox" class="ri-panel ri-toolbox open" aria-label="Customize traces">
      <header class="ri-toolbox-head">
        <div><strong>Customize traces</strong><span>Drag telemetry onto a chart. Drag a chart title to reorder.</span></div>
        <button type="button" class="ri-icon-button" aria-label="Hide trace toolbox" title="Hide toolbox" onClick={props.onClose}>×</button>
      </header>
      <div class="ri-toolbox-body">
        <section>
          <div class="ri-toolbox-section"><div><small>Trace layout</small><strong>{props.layoutName}</strong></div><span>{rows.length} / {props.maximumRows}</span></div>
        </section>
        {selectedRow && (
          <section>
            <div class="ri-toolbox-section"><div><small>Selected chart</small><strong>{selectedRow.signals.map((id) => resolve(data, id)?.name ?? id).join(" / ")}</strong></div><span>{selectedRow.signals.length} trace{selectedRow.signals.length === 1 ? "" : "s"}</span></div>
            <div class="ri-selected-signals">
              {selectedRow.signals.map((id, index) => (
                <div class="ri-selected-signal" key={id}>
                  <i class={index === 0 ? "solid" : "dashed"} title={index === 0 ? "Solid trace" : "Dashed trace"} aria-hidden="true" />
                  <span><strong>{resolve(data, id)?.name ?? id}</strong><small>{resolve(data, id)?.unit ?? ""}</small></span>
                  <button type="button" data-analysis-no-drag aria-label={`Remove ${resolve(data, id)?.name ?? id} from this chart`} title="Remove this trace" onClick={() => props.onRemoveSignal(selectedRow.row_id, id)}>×</button>
                </div>
              ))}
            </div>
            <button type="button" class="ri-button" data-analysis-no-drag disabled={rows.length <= 1} onClick={() => props.onRemoveRow(selectedRow.row_id)}>Remove chart</button>
          </section>
        )}
        <section class="ri-library">
          <div class="ri-toolbox-section"><div><small>Trace library</small><strong>Add telemetry</strong></div><span>{count}</span></div>
          <label class="ri-search"><span class="ri-sr">Search trace telemetry</span><input type="search" placeholder="Search speed, pedals, load..." value={search} onInput={(event) => setSearch((event.currentTarget as HTMLInputElement).value)} /></label>
          <div class="ri-library-list" role="list" aria-label="Telemetry traces">
            {count === 0 && <div class="ri-muted ri-pad"><strong>No matching telemetry</strong> Try a shorter name or unit.</div>}
            {library.map(([group, list]) => (
              <section class="ri-signal-category" key={group}>
                <h3>{group}</h3>
                {list.map((signal) => (
                  <article
                    class={`ri-signal-card${signal.recorded ? "" : " unavailable"}`} role="listitem" tabIndex={0} key={signal.id}
                    data-analysis-drag-signal={signal.id} data-signal-id={signal.id} data-signal-name={signal.name}
                    aria-label={`${signal.name}, ${signal.unit}. Drag onto a chart, or press Enter to pair or replace it in the selected chart.`}
                    title={signal.recorded ? `Drag ${signal.name} onto a chart` : `${signal.name} was not recorded in this race; the saved chart will activate when data is available`}
                    onKeyDown={(event: KeyboardEvent) => {
                      if (event.key !== "Enter" && event.key !== " ") return;
                      event.preventDefault();
                      if (selectedRow) props.onPlace(selectedRow.row_id, signal.id);
                      else props.onAddRow(signal.id);
                    }}
                  >
                    <span class="ri-grip" aria-hidden="true">⋮⋮</span>
                    <div><strong>{signal.name}</strong><small>{signal.unit}{signal.recorded ? "" : " · not recorded"}</small></div>
                    <button type="button" class="ri-icon-button" data-analysis-no-drag disabled={rows.length >= props.maximumRows} aria-label={`Add ${signal.name} as a new chart`} title="Add as a new chart" onKeyDown={(event: KeyboardEvent) => event.stopPropagation()} onClick={() => props.onAddRow(signal.id)}>+</button>
                  </article>
                ))}
              </section>
            ))}
          </div>
        </section>
      </div>
      <footer class="ri-toolbox-foot"><span>Drop between charts to add</span><span>Drop on a chart to pair</span><span><kbd>Alt</kbd> + arrow reorders</span></footer>
    </aside>
  );
}
