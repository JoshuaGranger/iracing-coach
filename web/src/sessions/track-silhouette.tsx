import { useEffect, useRef, useState } from "preact/hooks";
import { type Schemas, getJson, paths } from "../core/api";
import { readJson, writeJson } from "../core/store";
import { type Silhouette, silhouetteOf } from "./silhouette";

/**
 * A silhouette belongs to a track layout, not to an event, so it is fetched once per layout (from any ready analysis there) and kept:
 * in memory for the page load, and in the device's storage so the next visit asks for nothing. A layout without usable geometry
 * is asked for once per page load and then drawn without. Only the current centreline (geometry with `transform.longitude_scale`,
 * analysis profile v24) is kept in storage: an older outline is drawn for the page load and asked for again on the next, so a
 * re-analysed layout replaces it. A new version of the key leaves every outline stored under an older one unread: version 2
 * dropped the pre-v24 centrelines, version 3 the outlines stored before they were turned so the long axis is horizontal
 * (`silhouette.ts`, backlog #81).
 */
export const SILHOUETTE_STORE = "coach.silhouette.3.";
const memory = new Map<string, Silhouette | null>();
const inflight = new Map<string, Promise<Silhouette | null>>();
const waiting: (() => void)[] = [];
let running = 0;
const MAX_PARALLEL = 2;

function slot(): Promise<void> {
  return new Promise((resolve) => {
    const go = () => {
      running += 1;
      resolve();
    };
    if (running < MAX_PARALLEL) go();
    else waiting.push(go);
  });
}
function release(): void {
  running -= 1;
  waiting.shift()?.();
}

function stored(trackKey: string): Silhouette | null {
  const v = readJson<Silhouette | null>(SILHOUETTE_STORE + trackKey, null);
  return v && typeof v.d === "string" && Array.isArray(v.tick) ? v : null;
}

/** Geometry built by the current centreline rule: its transform carries the longitude scale. */
export function isCurrentCentreline(geometry: Pick<Schemas["TrackGeometry"], "transform">): boolean {
  const k = geometry.transform?.longitude_scale;
  return typeof k === "number" && k > 0;
}

export function silhouetteFor(trackKey: string, analysisId: string): Promise<Silhouette | null> {
  if (memory.has(trackKey)) return Promise.resolve(memory.get(trackKey) ?? null);
  const hit = stored(trackKey);
  if (hit) {
    memory.set(trackKey, hit);
    return Promise.resolve(hit);
  }
  const running = inflight.get(trackKey);
  if (running) return running;
  const p = (async () => {
    await slot();
    try {
      const geometry = await getJson<Schemas["TrackGeometry"]>(paths.track(analysisId));
      const sil = geometry.status === "usable" ? silhouetteOf(geometry.main_path?.x, geometry.main_path?.y) : null;
      memory.set(trackKey, sil);
      if (sil && isCurrentCentreline(geometry)) writeJson(SILHOUETTE_STORE + trackKey, sil);
      return sil;
    } catch {
      memory.set(trackKey, null);
      return null;
    } finally {
      release();
      inflight.delete(trackKey);
    }
  })();
  inflight.set(trackKey, p);
  return p;
}

/** Whether there is nothing left to wait for: the layout is known (drawn or without geometry) or no analysis can supply it. */
function settledFor(trackKey: string, analysisId: string | null): boolean {
  return memory.has(trackKey) || stored(trackKey) !== null || analysisId === null;
}

/**
 * The card's silhouette; the request waits until the card is on screen. While it is awaited the host holds its square, so the
 * identity block does not move when the outline arrives; a layout with no outline draws nothing and holds no space (#71).
 */
export function TrackSilhouette({ trackKey, analysisId }: { trackKey: string; analysisId: string | null }) {
  const host = useRef<SVGSVGElement>(null);
  const [sil, setSil] = useState<Silhouette | null>(() => memory.get(trackKey) ?? stored(trackKey));
  const [settled, setSettled] = useState(() => settledFor(trackKey, analysisId));
  const [visible, setVisible] = useState(() => typeof IntersectionObserver === "undefined");

  useEffect(() => {
    setSil(memory.get(trackKey) ?? stored(trackKey));
    setSettled(settledFor(trackKey, analysisId));
  }, [trackKey, analysisId]);
  useEffect(() => {
    const el = host.current;
    if (visible || !el) return;
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        setVisible(true);
        io.disconnect();
      }
    });
    io.observe(el);
    return () => io.disconnect();
  }, [visible]);
  useEffect(() => {
    if (sil || !visible || !analysisId) return;
    let live = true;
    void silhouetteFor(trackKey, analysisId).then((s) => {
      if (!live) return;
      setSil(s);
      setSettled(true);
    });
    return () => {
      live = false;
    };
  }, [sil, visible, trackKey, analysisId]);

  if (!sil) return settled ? null : <svg ref={host} class="sx-sil" width="1" height="1" aria-hidden="true" data-pending="true" />;
  return (
    <svg ref={host} class="sx-sil" width={sil.width} height={sil.height} viewBox={`0 0 ${sil.width} ${sil.height}`} aria-hidden="true" data-testid="silhouette">
      <path class="sx-sil-line" d={sil.d} fill="none" stroke-linejoin="round" stroke-linecap="round" />
      <line class="sx-sil-tick" x1={sil.tick[0]} y1={sil.tick[1]} x2={sil.tick[2]} y2={sil.tick[3]} />
      <line class="sx-sil-tick sx-sil-tick-dash" x1={sil.tick[0]} y1={sil.tick[1]} x2={sil.tick[2]} y2={sil.tick[3]} />
    </svg>
  );
}
