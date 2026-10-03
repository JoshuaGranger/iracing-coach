import type { ComponentChildren } from "preact";

export type Tone = "neutral" | "good" | "warn" | "bad" | "info";

export function Chip({ tone = "neutral", title, children }: { tone?: Tone; title?: string; children: ComponentChildren }) {
  return (
    <span class={`chip chip-${tone}`} title={title}>
      {children}
    </span>
  );
}

export function Notice({ tone = "neutral", children, role }: { tone?: Tone; children: ComponentChildren; role?: "status" | "alert" }) {
  return (
    <p class={`notice notice-${tone}`} role={role ?? (tone === "bad" ? "alert" : "status")}>
      {children}
    </p>
  );
}

export function Empty({ title, children }: { title: string; children?: ComponentChildren }) {
  return (
    <div class="empty">
      <h2>{title}</h2>
      {children ? <p>{children}</p> : null}
    </div>
  );
}

export function Loading({ what }: { what?: string }) {
  return (
    <p class="loading" role="status">
      {what ? `Loading ${what}` : "Loading"}
    </p>
  );
}

export function PageHead({ title, sub, children }: { title: string; sub?: string; children?: ComponentChildren }) {
  return (
    <header class="page-head">
      <div>
        <h1>{title}</h1>
        {sub ? <p class="sub">{sub}</p> : null}
      </div>
      {children ? <div class="page-actions">{children}</div> : null}
    </header>
  );
}

export function ErrorLine({ error }: { error: Error | null }) {
  return error ? <Notice tone="bad">{error.message}</Notice> : null;
}
