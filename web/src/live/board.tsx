import type { LiveConnection } from "./connection";
import { useLiveSample } from "./connection";
import { type GridEdit, LiveGrid } from "./grid";
import type { LayoutBody } from "./layout";

/**
 * The tile grid on its own render loop: a frame arrives, the grid draws, and nothing else on the page re-renders with it.
 * Always the full stream rate; the loop's frame time decides decimation. While editing, the edit layer rides along memoised.
 */
export function LiveBoard({ conn, body, edit, flow }: { conn: LiveConnection; body: LayoutBody; edit: GridEdit | null; flow: boolean }) {
  const { frame, tick } = useLiveSample(conn);
  return <LiveGrid body={body} frame={frame} history={conn.history} tick={tick} edit={edit} flow={flow} />;
}
