import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { ApiError, type Schemas, api, getJson, paths } from "../core/api";
import { formatDate, formatNumber, formatSigned, humanize, sessionTypeLabel, trackLabel } from "../core/format";
import { useJobRun } from "../core/jobs";
import { outbox } from "../core/outbox";
import { useResource } from "../core/resource";
import { href } from "../core/router";
import { useServerEvent } from "../core/sse";
import { useStore } from "../core/store";
import { Chip, Empty, ErrorLine, Loading, Notice, PageHead } from "../ui/kit";
import {
  BALANCES,
  type Balance,
  type DraftBody,
  EMPTY_DRAFT,
  EMPTY_EDITOR,
  EXTRA_STAGES,
  GOALS,
  type EditorState,
  type MapTurn,
  RUN_PHASES,
  type RunPhase,
  STAGES,
  SYMPTOMS,
  type TuningResult,
  analyzeBlocker,
  annotationBody,
  applyPriority,
  buildFeedback,
  buildMapSubmission,
  mergeTurns,
  phaseCount,
  priorityCorner,
  readDraft,
  readEditorState,
  readResult,
  recommendRequest,
  replaceFeedback,
  runFacts,
  toggleRun,
  validBounds,
} from "../tuning/model";
import { TrackMap, turnDetail } from "../tuning/track-map";

type Ctx = Schemas["TuningContext"];
type DraftRes = Schemas["TuningDraft"] | null;

async function getOrNull<T>(path: string, signal: AbortSignal): Promise<T | null> {
  try {
    return await getJson<T>(path, { signal });
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
}

/** Analyses of this driver that can feed tuning: the newest analyzed sessions. */
function Entry({ customerId }: { customerId: number }) {
  const res = useResource(paths.sessions(customerId, { limit: 40 }), () => api.sessions(customerId, { limit: 40 }));
  const ready = (res.data?.sessions ?? []).filter((s) => s.analysis_state === "ready" && s.analysis_id);
  return (
    <>
      <PageHead title="Progressive tuning" sub="Pick the recording to tune from." />
      {res.error && !res.data ? <ErrorLine error={res.error} /> : null}
      {!res.data && !res.error ? <Loading what="recordings" /> : null}
      {res.data && ready.length === 0 ? <Empty title="No analyzed recording yet">Analyze a session first, then return here.</Empty> : null}
      <ul class="rows" aria-label="Recordings">
        {ready.map((s) => (
          <li key={s.session_id}>
            <a class="row-link" href={href({ page: "tuning", customerId, analysisId: s.analysis_id! })}>
              <span class="row-main">
                <Chip>{sessionTypeLabel(s.session_type)}</Chip>
                <span class="row-title">{trackLabel(s.track, s.track_config)}</span>
              </span>
              <span class="row-meta">
                {s.car_name ?? s.car_path} · {formatDate(s.start_time_utc)}
              </span>
            </a>
          </li>
        ))}
      </ul>
    </>
  );
}

function FeedbackEditor(p: {
  turn: MapTurn;
  phase: RunPhase;
  state: EditorState;
  priority: boolean;
  saved: number;
  status: string;
  onChange(s: EditorState): void;
  onPriority(on: boolean): void;
  onClose(): void;
}) {
  const s = p.state;
  const setBalance = (stage: "entry" | "center" | "exit", b: Balance) => {
    const balance = { ...s.balance };
    if (balance[stage] === b) delete balance[stage];
    else balance[stage] = b;
    p.onChange({ ...s, balance });
  };
  const [extraStage, setExtraStage] = useState<"whole" | "entry" | "center" | "exit">("whole");
  const toggleSymptom = (id: string) => {
    const key = `${extraStage}|${id}`;
    p.onChange({ ...s, extras: s.extras.includes(key) ? s.extras.filter((k) => k !== key) : [...s.extras, key] });
  };
  return (
    <section class="feedback-editor" aria-label={`Feedback for ${p.turn.label}`}>
      <header>
        <div>
          <span class="eyebrow">{RUN_PHASES.find(([id]) => id === p.phase)?.[1]} run</span>
          <h2>{p.turn.label}</h2>
        </div>
        <div class="row-actions">
          <button type="button" class={`icon-btn${p.priority ? " on" : ""}`} aria-pressed={p.priority} aria-label={p.priority ? "Remove highest priority" : "Make highest priority"} title="Highest priority turn" onClick={() => p.onPriority(!p.priority)}>
            ★
          </button>
          <button type="button" class="icon-btn" aria-label="Close turn feedback" onClick={p.onClose}>
            ×
          </button>
        </div>
      </header>
      <div class="balance-grid" aria-label="Corner balance">
        {STAGES.map(([stage, label]) => (
          <div class="balance-row" key={stage}>
            <strong>{label}</strong>
            <div role="radiogroup" aria-label={`${label} balance`}>
              {BALANCES.map(([b, bl]) => (
                <button key={b} type="button" role="radio" aria-checked={s.balance[stage] === b} class={`balance-${b}${s.balance[stage] === b ? " on" : ""}`} onClick={() => setBalance(stage, b)}>
                  {bl}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
      <div class="symptoms">
        <div class="seg" role="group" aria-label="Symptom location">
          {EXTRA_STAGES.map(([id, label]) => (
            <button key={id} type="button" aria-pressed={extraStage === id} class={extraStage === id ? "on" : ""} onClick={() => setExtraStage(id)}>
              {label}
            </button>
          ))}
        </div>
        <div class="chip-grid" role="group" aria-label="Symptoms">
          {SYMPTOMS.map((sy) => {
            const on = s.extras.includes(`${extraStage}|${sy.id}`);
            return (
              <button key={sy.id} type="button" class={on ? "on" : ""} aria-pressed={on} title={sy.help} onClick={() => toggleSymptom(sy.id)}>
                {sy.label}
              </button>
            );
          })}
        </div>
      </div>
      <label class="note-field">
        <span>Turn note</span>
        <textarea rows={2} maxLength={500} placeholder="Anything else about this turn" value={s.note} onInput={(e) => p.onChange({ ...s, note: (e.currentTarget as HTMLTextAreaElement).value.replace(/^\s+/, "") })} />
      </label>
      <div class="ratings">
        <label title="How much this hurt your ability to drive the corner: 1 minor, 5 race-limiting.">
          <span>Severity</span>
          <input type="range" min={1} max={5} step={1} value={s.severity} aria-label="Severity, from minor to race-limiting" onInput={(e) => p.onChange({ ...s, severity: Number((e.currentTarget as HTMLInputElement).value) })} />
          <output>{s.severity}</output>
        </label>
        <label title="How sure you are the feeling was repeatable, not traffic or one unusual lap: 1 unsure, 5 repeatable.">
          <span>Confidence</span>
          <input type="range" min={1} max={5} step={1} value={s.confidence} aria-label="Confidence, from uncertain to repeatable" onInput={(e) => p.onChange({ ...s, confidence: Number((e.currentTarget as HTMLInputElement).value) })} />
          <output>{s.confidence}</output>
        </label>
      </div>
      <footer class="editor-foot" aria-live="polite">
        <span>{p.status}</span>
        {p.saved > 0 ? <small>{p.saved} saved</small> : null}
      </footer>
    </section>
  );
}

function CorrectionPanel(p: { turn: MapTurn; handles: { entry: number; apex: number; exit: number; active: "entry" | "apex" | "exit" }; label: string; error: string; onLabel(v: string): void; onHandles(h: { entry: number; apex: number; exit: number; active: "entry" | "apex" | "exit" }): void; onSave(): void; onClose(): void }) {
  const h = p.handles;
  const slider = (key: "entry" | "apex" | "exit", name: string) => (
    <label>
      <span>{name}</span>
      <input type="range" min={0} max={99.9} step={0.1} value={h[key] * 100} onInput={(e) => p.onHandles({ ...h, [key]: Number((e.currentTarget as HTMLInputElement).value) / 100, active: key })} />
      <output>{(h[key] * 100).toFixed(1)}%</output>
    </label>
  );
  return (
    <form
      class="correction"
      onSubmit={(e) => {
        e.preventDefault();
        p.onSave();
      }}
    >
      <div class="correction-head">
        <strong>Verify this turn</strong>
        <button type="button" class="icon-btn" aria-label="Close correction" onClick={p.onClose}>
          ×
        </button>
      </div>
      <p class="sub">
        Click the track to set <b>{h.active}</b>, or use the sliders.
      </p>
      <label>
        <span>Label</span>
        <input value={p.label} maxLength={48} autocomplete="off" onInput={(e) => p.onLabel((e.currentTarget as HTMLInputElement).value)} />
      </label>
      <div class="bounds">
        {slider("entry", "Entry")}
        {slider("apex", "Apex")}
        {slider("exit", "Exit")}
      </div>
      {p.error ? (
        <p class="field-error" role="alert">
          {p.error}
        </p>
      ) : null}
      <button type="submit" class="btn btn-accent" disabled={!p.label.trim()}>
        Save correction
      </button>
    </form>
  );
}

function ResultCard(p: { result: TuningResult; outcomeTargets: Ctx["open_targets"]; analysisId: string; customerId: number; onEdit(): void }) {
  const r = p.result;
  const c = r.selected;
  const outcome = useJobRun<unknown>();
  const [target, setTarget] = useState("");
  const save = (kind: Schemas["TuningOutcomeRequest"]["outcome"]) =>
    outcome.run(() => api.recordOutcome(p.customerId, r.experimentId, { outcome: kind, notes: "", result_analysis_id: target || null }), "Saving result");
  return (
    <section class="result-card" aria-label="Controlled test" data-testid="tuning-result">
      <header>
        <div>
          <span class="eyebrow">Controlled test</span>
          <h2>{c ? humanize(c.system) : "No supported change"}</h2>
        </div>
        <Chip>One system</Chip>
      </header>
      {r.summary ? <p>{r.summary}</p> : null}
      {c ? (
        <>
          <dl class="facts">
            <div>
              <dt>Try</dt>
              <dd>{c.change}</dd>
            </div>
            <div>
              <dt>Expected</dt>
              <dd>{c.effect}</dd>
            </div>
            <div>
              <dt>Watch</dt>
              <dd>{c.risk}</dd>
            </div>
          </dl>
          {c.verify.length ? (
            <ul class="verify">
              {c.verify.map((v) => (
                <li key={v}>{v}</li>
              ))}
            </ul>
          ) : null}
          {r.protocol?.sequence.length ? (
            <ol class="protocol" aria-label="Test sequence">
              {r.protocol.sequence.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ol>
          ) : null}
          <div class="outcome">
            <label class="select-field">
              <span>Comparison run</span>
              <select value={target} onChange={(e) => setTarget((e.currentTarget as HTMLSelectElement).value)}>
                <option value="">Driver result only</option>
                {p.outcomeTargets.map((t) => (
                  <option key={t.analysis_id} value={t.analysis_id}>
                    {formatDate(t.start_time_utc)} · {t.identity.car ?? t.identity.car_path}
                  </option>
                ))}
              </select>
            </label>
            <div class="seg" role="group" aria-label="Save comparison outcome">
              {(
                [
                  ["improved", "Improved"],
                  ["worse", "Worse"],
                  ["no-change", "No change"],
                  ["inconclusive", "Inconclusive"],
                ] as const
              ).map(([k, label]) => (
                <button key={k} type="button" disabled={outcome.state.phase === "running" || !r.experimentId} onClick={() => void save(k)}>
                  {label}
                </button>
              ))}
            </div>
            {outcome.state.phase === "done" ? <p role="status">Result saved.</p> : null}
            {outcome.state.phase === "error" ? <Notice tone="warn">{outcome.state.message}</Notice> : null}
          </div>
        </>
      ) : (
        <p>{[...r.missing, ...r.limitations].join(" ") || "This recording does not support a setup-change recommendation."}</p>
      )}
      <button type="button" class="btn" onClick={p.onEdit}>
        Edit feedback
      </button>
    </section>
  );
}

function Workbench({ customerId, analysisId }: { customerId: number; analysisId: string }) {
  const ctxRes = useResource<Ctx>(paths.tuning(analysisId), () => api.tuning(analysisId));
  useServerEvent("analysis_ready", (e) => e.analysis_id === analysisId && ctxRes.reload());
  const ctx = ctxRes.data;

  // ---- draft (feedback, goal, runs, note), saved through the outbox
  const draftKey = ctx ? paths.tuningDraft(customerId, ctx.draft_identity_key) : null;
  const draftRes = useResource<DraftRes>(draftKey, (signal) => getOrNull<Schemas["TuningDraft"]>(draftKey!, signal));
  const [draft, setDraft] = useState<DraftBody>(EMPTY_DRAFT);
  const [priorityId, setPriorityId] = useState<string | null>(null);
  const seeded = useRef<string | null>(null);
  useEffect(() => {
    if (!draftKey || draftRes.data === undefined || seeded.current === draftKey) return;
    seeded.current = draftKey;
    const seed = readDraft(draftRes.data?.body);
    setDraft(seed);
    setPriorityId(priorityCorner(seed));
    if (draftRes.data) outbox.observe(draftKey, draftRes.data.version);
  }, [draftKey, draftRes.data]);

  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const latest = useRef<{ key: string; body: DraftBody } | null>(null);
  const flush = () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    const l = latest.current;
    latest.current = null;
    if (l) void outbox.enqueue({ method: "PUT", path: l.key, body: { body: l.body }, label: "Tuning feedback", coalesceKey: `draft:${l.key}` }).catch(() => undefined);
  };
  useEffect(() => flush, []);
  const commit = (next: DraftBody, delay = 300) => {
    setDraft(next);
    if (!draftKey) return;
    latest.current = { key: draftKey, body: next };
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(flush, delay);
  };
  const pending = useStore(outbox.entries).some((e) => draftKey !== null && e.path === draftKey);
  const saveStatus = latest.current || pending ? "Saving" : "Saved";

  // ---- turn corrections
  const trackKey = ctx?.identity.track_configuration_key ?? null;
  const annKey = ctx && trackKey ? paths.turnAnnotations(customerId, trackKey, ctx.map.map_identity) : null;
  const annRes = useResource<Schemas["TurnAnnotations"] | null>(annKey, (signal) => getOrNull<Schemas["TurnAnnotations"]>(annKey!, signal));
  const [annBody, setAnnBody] = useState<unknown>(null);
  useEffect(() => {
    if (annRes.data) {
      setAnnBody(annRes.data.body);
      if (annKey) outbox.observe(annKey, annRes.data.version);
    }
  }, [annRes.data, annKey]);
  const turns = useMemo(() => (ctx ? mergeTurns(ctx.map.turns, annBody) : []), [ctx, annBody]);

  // ---- selection
  const [phase, setPhase] = useState<RunPhase>("early");
  const [turnId, setTurnId] = useState<string | null>(null);
  const turn = turns.find((t) => t.corner_id === turnId) ?? null;
  const [editor, setEditor] = useState<EditorState>(EMPTY_EDITOR);
  const [hideResult, setHideResult] = useState(true);
  useEffect(() => {
    if (turnId) setEditor(readEditorState(draft.feedback, turnId, phase));
  }, [turnId, phase]);

  const editState = (next: EditorState) => {
    if (!turn) return;
    setEditor(next);
    const items = buildFeedback(turn, phase, next, priorityId === turn.corner_id ? 5 : 3);
    commit(applyPriority(replaceFeedback(draft, turn.corner_id, phase, items), priorityId));
    setHideResult(true);
  };
  const setPriority = (on: boolean) => {
    if (!turn) return;
    const id = on ? turn.corner_id : null;
    setPriorityId(id);
    commit(applyPriority(draft, id));
  };
  const pick = (id: string) => {
    setTurnId(id);
    setCorrection(null);
    setHideResult(true);
  };

  // ---- correction mode
  const [correction, setCorrection] = useState<{ entry: number; apex: number; exit: number; active: "entry" | "apex" | "exit"; label: string; error: string } | null>(null);
  const openCorrection = () => turn && setCorrection({ entry: turn.start_pct, apex: turn.apex_pct, exit: turn.end_pct, active: "entry", label: turn.label, error: "" });
  function saveCorrection() {
    if (!turn || !correction || !annKey) return;
    const err = validBounds(correction.entry, correction.apex, correction.exit);
    if (err) return setCorrection({ ...correction, error: err });
    const fixed: MapTurn = { ...turn, label: correction.label.trim(), start_pct: correction.entry, apex_pct: correction.apex, end_pct: correction.exit, user_verified: true };
    const body = annotationBody(annBody, fixed);
    setAnnBody(body);
    setCorrection(null);
    void outbox.enqueue({ method: "PUT", path: annKey, body: { body }, label: "Turn correction", coalesceKey: `ann:${annKey}` }).catch(() => undefined);
  }

  // ---- analysis
  const run = useJobRun<unknown>();
  if (!ctx) {
    const notReady = ctxRes.error instanceof ApiError && ctxRes.error.status === 409;
    if (notReady) return <Notice>Preparing this race for tuning. It opens when ready.</Notice>;
    return ctxRes.error ? <ErrorLine error={ctxRes.error} /> : <Loading what="race" />;
  }
  const id = ctx.identity;
  const submission = buildMapSubmission(ctx.map, turns, draft.feedback, trackKey);
  const inputs = { ctx, draft, verified: submission.verified, hasSubmission: submission.submission !== null };
  const blocker = analyzeBlocker(inputs);
  const needsTarget = id.setup_type.toLowerCase() !== "open";
  const result: TuningResult | null = run.state.phase === "done" && !hideResult ? readResult(run.state.result) : null;
  const eligibleRuns = ctx.representative_runs;
  const selectedRuns = eligibleRuns.map(runFacts).filter((f) => draft.representative_run_ids.includes(f.number));

  return (
    <>
      <p class="crumbs">
        <a href={href({ page: "tuning", customerId, analysisId: null })}>Progressive tuning</a>
      </p>
      <PageHead title={trackLabel(id.track, id.layout)} sub={`${id.car ?? id.car_path ?? "Car unknown"} · ${sessionTypeLabel(id.session_type)}`}>
        <Chip tone={needsTarget ? "neutral" : "good"} title={needsTarget ? "Fixed setup: driving evidence only" : "Open setup: can receive changes"}>
          {needsTarget ? "Fixed setup" : "Open setup"}
        </Chip>
      </PageHead>
      {!ctx.eligible && !ctx.can_use_as_evidence ? <Notice tone="warn">{ctx.reasons.join(" ") || "This recording cannot be used for tuning."}</Notice> : null}

      <div class="tuning-grid">
        <section class="panel map-panel" aria-label="Track feedback">
          <header class="panel-head">
            <h2>Track</h2>
            <Chip tone={ctx.map.is_verified ? "good" : "neutral"} title={ctx.map.verification_message ?? ctx.map.source_label}>
              {ctx.map.is_verified ? "Map ready" : "Verify turns"}
            </Chip>
          </header>
          <TrackMap
            map={ctx.map}
            turns={turns}
            selectedId={turnId}
            feedback={draft.feedback}
            phase={phase}
            priorityId={priorityId}
            handles={correction ? { entry: correction.entry, apex: correction.apex, exit: correction.exit, active: correction.active } : null}
            onPick={pick}
            onSetPct={(pct) => {
              if (!correction) return;
              const next = correction.active === "entry" ? "apex" : correction.active === "apex" ? "exit" : "entry";
              setCorrection({ ...correction, [correction.active]: pct, active: next, error: "" });
            }}
          />
          <div class="turn-list" role="group" aria-label="Selectable turns">
            {turns.length === 0 ? <span class="sub">No selectable zones</span> : null}
            {turns.map((t) => {
              const items = draft.feedback.filter((f) => f.corner_id === t.corner_id && f.run_phase === phase);
              return (
                <button key={t.corner_id} type="button" class={t.corner_id === turnId ? "on" : ""} aria-pressed={t.corner_id === turnId} onClick={() => pick(t.corner_id)}>
                  <strong>{t.label}</strong>
                  <small>{turnDetail(t, items)}</small>
                </button>
              );
            })}
          </div>
          {!ctx.map.is_verified && turn && !correction ? (
            <button type="button" class="link-btn" onClick={openCorrection}>
              Correct selected zone
            </button>
          ) : null}
          {correction && turn ? (
            <CorrectionPanel
              turn={turn}
              handles={correction}
              label={correction.label}
              error={correction.error}
              onLabel={(label) => setCorrection({ ...correction, label, error: "" })}
              onHandles={(h) => setCorrection({ ...correction, ...h, error: "" })}
              onSave={saveCorrection}
              onClose={() => setCorrection(null)}
            />
          ) : null}
        </section>

        <aside class="panel toolbox" aria-label="Tuning evidence and controls">
          <header class="panel-head">
            <h2>{RUN_PHASES.find(([p]) => p === phase)?.[1]} run</h2>
            <label class="select-field inline">
              <span>Goal</span>
              <select value={draft.goal} onChange={(e) => commit({ ...draft, goal: (e.currentTarget as HTMLSelectElement).value as DraftBody["goal"] })}>
                {GOALS.map(([g, label]) => (
                  <option key={g} value={g}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
          </header>
          <div class="seg phases" role="tablist" aria-label="Run phase for all turns">
            {RUN_PHASES.map(([p, label]) => (
              <button key={p} type="button" role="tab" aria-selected={phase === p} class={phase === p ? "on" : ""} onClick={() => { setPhase(p); setHideResult(true); }}>
                {label}
                {phaseCount(draft, p) > 0 ? <b>{phaseCount(draft, p)}</b> : null}
              </button>
            ))}
          </div>

          {turn && !result ? (
            <FeedbackEditor
              turn={turn}
              phase={phase}
              state={editor}
              priority={priorityId === turn.corner_id}
              saved={draft.feedback.filter((f) => f.corner_id === turn.corner_id && f.run_phase === phase).length}
              status={saveStatus}
              onChange={editState}
              onPriority={setPriority}
              onClose={() => setTurnId(null)}
            />
          ) : null}

          {result ? (
            <ResultCard result={result} outcomeTargets={ctx.open_targets} analysisId={analysisId} customerId={customerId} onEdit={() => setHideResult(true)} />
          ) : (
            <>
              <section class="runs" aria-label="Representative runs">
                <h3>Representative runs</h3>
                {eligibleRuns.length === 0 ? <p class="sub">No run has enough clean laps.</p> : null}
                <div class="chip-grid" role="group" aria-label="Representative runs">
                  {eligibleRuns.map(runFacts).map((f) => {
                    const on = draft.representative_run_ids.includes(f.number);
                    return (
                      <button key={f.number} type="button" class={on ? "on" : ""} aria-pressed={on} disabled={!on && draft.representative_run_ids.length >= 3} title={`Run ${f.number}${f.slope !== null ? ` · ${formatSigned(f.slope, 3, "s/lap")}` : ""}`} onClick={() => commit(toggleRun(draft, f.number))}>
                        <strong>Run {f.number}</strong>
                        <span>{f.greenLaps ?? "-"} green</span>
                      </button>
                    );
                  })}
                </div>
                {selectedRuns.length ? (
                  <dl class="run-facts">
                    {selectedRuns.flatMap((f) => [
                      f.delta !== null ? [`Run ${f.number}`, formatSigned(f.delta, 3, "s"), "early to late"] : f.slope !== null ? [`Run ${f.number}`, formatSigned(f.slope, 3, "s/lap"), "pace trend"] : null,
                      f.fuel !== null ? [`Run ${f.number}`, formatNumber(f.fuel, 2, "gal"), "fuel used"] : null,
                      f.lowestTire !== null ? [`Run ${f.number}`, formatNumber(f.lowestTire, 1, "%"), "lowest tire remaining"] : null,
                    ]).filter((x): x is string[] => x !== null).map(([a, b, c]) => (
                      <div key={`${a}${c}`}>
                        <dt>{a} · {c}</dt>
                        <dd>{b}</dd>
                      </div>
                    ))}
                  </dl>
                ) : null}
              </section>
              {needsTarget ? (
                <label class="select-field">
                  <span>Open-setup target</span>
                  <select value={draft.open_target_analysis_id ?? ""} onChange={(e) => commit({ ...draft, open_target_analysis_id: (e.currentTarget as HTMLSelectElement).value || null })}>
                    <option value="">Choose matching race</option>
                    {ctx.open_targets.map((t) => (
                      <option key={t.analysis_id} value={t.analysis_id}>
                        {formatDate(t.start_time_utc)} · {t.identity.car ?? t.identity.car_path}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
            </>
          )}

          <footer class="toolbox-foot">
            <label class="note-field">
              <span>General feedback</span>
              <textarea rows={2} maxLength={1200} placeholder="Anything that applies across the race" value={draft.general_note} onInput={(e) => { commit({ ...draft, general_note: (e.currentTarget as HTMLTextAreaElement).value }, 450); setHideResult(true); }} />
            </label>
            {blocker && !result ? <p class="hint" role="status">{blocker}</p> : null}
            {run.state.phase === "error" ? <Notice tone="warn">{run.state.message}</Notice> : null}
            {run.state.phase === "running" ? <Loading what="analysis" /> : null}
            <button
              type="button"
              class="btn btn-accent"
              disabled={blocker !== "" || run.state.phase === "running"}
              onClick={() => {
                flush();
                if (!submission.submission) return;
                setTurnId(null);
                void run.run(() => api.recommend(analysisId, recommendRequest(inputs, submission.submission!)), "Analyzing").then(() => setHideResult(false));
              }}
            >
              Begin analysis
            </button>
          </footer>
        </aside>
      </div>
    </>
  );
}

export default function TuningPage({ customerId, analysisId }: { customerId: number; analysisId: string | null }) {
  return analysisId ? <Workbench key={analysisId} customerId={customerId} analysisId={analysisId} /> : <Entry customerId={customerId} />;
}
