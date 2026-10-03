import type { ComponentType } from "preact";
import { useEffect, useState } from "preact/hooks";
import { Loading, Notice } from "./kit";

/** Loads a page chunk on first use. Keeps the initial bundle small for phones. */
export function Lazy<P extends object>({ load, props, what }: { load: () => Promise<{ default: ComponentType<P> }>; props: P; what: string }) {
  const [state, setState] = useState<{ C: ComponentType<P> | null; error: Error | null }>({ C: null, error: null });
  useEffect(() => {
    let live = true;
    load().then(
      (m) => live && setState({ C: m.default, error: null }),
      (e: unknown) => live && setState({ C: null, error: e instanceof Error ? e : new Error(String(e)) }),
    );
    return () => {
      live = false;
    };
  }, [load]);
  if (state.error) return <Notice tone="bad">Could not load {what}: {state.error.message}</Notice>;
  if (!state.C) return <Loading what={what} />;
  const C = state.C;
  return <C {...props} />;
}
