import type { LiveConnection } from "./connection";
import { useLiveSample } from "./connection";
import { LiveGrid } from "./grid";
import type { LayoutBody } from "./layout";

/**
 * The tile grid on its own render loop: a frame arrives, the grid draws, and nothing else on the page re-renders with it.
 * Always the full stream rate; the loop's frame time decides decimation.
 */
export function LiveBoard({
  conn,
  body,
  editing,
  selectedId,
  onSelect,
  onChange,
  flow,
}: {
  conn: LiveConnection;
  body: LayoutBody;
  editing: boolean;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onChange: (body: LayoutBody) => void;
  flow: boolean;
}) {
  const { frame, tick } = useLiveSample(conn);
  return <LiveGrid body={body} frame={frame} history={conn.history} tick={tick} editing={editing} selectedId={selectedId} onSelect={onSelect} onChange={onChange} flow={flow} />;
}
