import { useEffect, useRef, useState } from "preact/hooks";
import { type Schemas, getJson, paths } from "../core/api";
import { readJson, writeJson } from "../core/store";
import { type Silhouette, silhouetteOf } from "./silhouette";

/**
 * A silhouette belongs to a track layout, not to an event, so it is fetched once per layout (from any ready analysis there) and kept:
 * in memory for the page load, and in the device's storage so the next visit asks for nothing. A layout without usable geometry
 * is asked for once per page load and then drawn without. Only the current centreline (geometry with `transform.longitude_scale`,
 * analysis profile v24) is kept in storage: an older outline is drawn for the page load and asked for again on the next, so a
 * re-analysed layout replaces it. Version 2 of the key drops the outlines stored before.
 */
const STORE = "coach.silhouette.2.";
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
  const v = readJson<Silhouette | null>(STORE + trackKey, null);
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
      if (sil && isCurrentCentreline(geometry)) writeJson(STORE + trackKey, sil);
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

/** The card's silhouette; the request waits until the card is on screen. */
export function TrackSilhouette({ trackKey, analysisId }: { trackKey: string; analysisId: string | null }) {
  const host = useRef<SVGSVGElement>(null);
  const [sil, setSil] = useState<Silhouette | null>(() => memory.get(trackKey) ?? stored(trackKey));
  const [visible, setVisible] = useState(() => typeof IntersectionObserver === "undefined");

  useEffect(() => {
    setSil(memory.get(trackKey) ?? stored(trackKey));
  }, [trackKey]);
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
    void silhouetteFor(trackKey, analysisId).then((s) => live && setSil(s));
    return () => {
      live = false;
    };
  }, [sil, visible, trackKey, analysisId]);

  if (!sil) return <svg ref={host} class="sx-sil" width="1" height="1" aria-hidden="true" />;
  return (
    <svg ref={host} class="sx-sil" width={sil.width} height={sil.height} viewBox={`0 0 ${sil.width} ${sil.height}`} aria-hidden="true" data-testid="silhouette">
      <path class="sx-sil-line" d={sil.d} fill="none" stroke-linejoin="round" stroke-linecap="round" />
      <line class="sx-sil-tick" x1={sil.tick[0]} y1={sil.tick[1]} x2={sil.tick[2]} y2={sil.tick[3]} />
      <line class="sx-sil-tick sx-sil-tick-dash" x1={sil.tick[0]} y1={sil.tick[1]} x2={sil.tick[2]} y2={sil.tick[3]} />
    </svg>
  );
}
