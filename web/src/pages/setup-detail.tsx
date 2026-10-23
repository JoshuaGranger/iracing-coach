import { useState } from "preact/hooks";
import { type Schemas, api, paths, request } from "../core/api";
import { formatDate, formatDateTime, plural } from "../core/format";
import { useJobRun } from "../core/jobs";
import { useResource } from "../core/resource";
import { uuid4 } from "../core/uuid";
import { href, navigate } from "../core/router";
import { Chip, ErrorLine, Loading, Notice } from "../ui/kit";
import { NotesPanel } from "../ui/notes";
import { carAccent } from "../core/palette";
import { racedCountOf } from "../setups/library";
import { RacedMark } from "../setups/glyphs";
import { VersionBoard } from "../setups/version-board";
import { type StoFile, stoFileFor } from "../setups/sto";
import { SOURCE_LABEL, type Setup, classifyCompare, comparePicks, groupSetupValues, sameNameAs, seenSpan, setupTitle, tuneTarget } from "../setups/model";
import "../setups/setups.css";

type Result = Schemas["SetupCompareResult"];
const SIGHTINGS_SHOWN = 25;

function ValueGroups({ groups }: { groups: ReturnType<typeof groupSetupValues>["settings"] }) {
  return (
    <div class="value-groups">
      {groups.map((g) => (
        <section key={g.group} class="value-group" aria-label={g.group}>
          <h3>{g.group}</h3>
          <dl>
            {g.items.map((i) => (
              <div key={i.key}>
                <dt>{i.label}</dt>
                <dd>{i.value}</dd>
              </div>
            ))}
          </dl>
        </section>
      ))}
    </div>
  );
}

function CompareTable({ result, left, right }: { result: Result; left: Setup; right: Setup }) {
  const [all, setAll] = useState(false);
  const rows = classifyCompare(result.rows);
  const row = (r: (typeof rows.changed)[number]) => (
    <tr key={r.key} data-kind={r.kind} data-readout={r.readout ? "true" : undefined}>
      <th scope="row">{r.label}</th>
      <td>{r.left}</td>
      <td>{r.right}</td>
    </tr>
  );
  const readouts = all ? rows.readouts : rows.readouts.filter((r) => r.kind !== "identical");
  const head = (s: Setup) => (
    <th scope="col">
      {setupTitle(s)}
      <small>{seenSpan(s)}</small>
    </th>
  );
  return (
    <div class="compare">
      <p class="compare-summary" data-testid="compare-summary">
        <b>{rows.changed.length} changed</b> · <b>{rows.unknown.length} unknown</b> · {rows.identical.length} identical
        {rows.readoutsMoved ? ` · ${plural(rows.readoutsMoved, "readout")} moved` : ""}
        {rows.identical.length + rows.readouts.filter((r) => r.kind === "identical").length > 0 ? (
          <button type="button" class="link-btn" aria-pressed={all} onClick={() => setAll(!all)}>
            {all ? "Hide identical" : "Show all values"}
          </button>
        ) : null}
      </p>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">Setting</th>
              {head(left)}
              {head(right)}
            </tr>
          </thead>
          <tbody>
            {rows.changed.map(row)}
            {rows.unknown.map(row)}
            {all ? rows.identical.map(row) : null}
            {readouts.length ? (
              <tr class="group-head">
                <th colSpan={3}>Garage readouts</th>
              </tr>
            ) : null}
            {readouts.map(row)}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/**
 * The file this setup came from, where it identifies the artifact: the stored path, or the name when no path is known. A path the
 * `.sto` library holds opens that file.
 */
function fileLabels(s: Setup, library: readonly StoFile[]): { label: string; file: StoFile | null }[] {
  const entries = s.sto_files ?? [];
  const stems = new Set(entries.map((f) => (f.relative_path.split(/[\\/]/).pop() ?? f.relative_path).toLowerCase()));
  return [
    ...entries.map((f) => ({ label: f.relative_path, file: f.format === "sto" ? stoFileFor(f, library) : null })),
    ...s.names.filter((n) => !stems.has(n.toLowerCase())).map((label) => ({ label, file: null })),
  ];
}

export default function SetupDetailPage({ customerId, setupId }: { customerId: number; setupId: number }) {
  const res = useResource(paths.setup(setupId), (signal) => api.setup(setupId, { signal }));
  const detail = res.data;
  const car = detail?.setup.car_path ?? null;
  const siblings = useResource(car ? paths.setups(customerId, { car_path: car, limit: 200 }) : null, () => api.setups(customerId, { car_path: car!, limit: 200 }));
  const [otherId, setOtherId] = useState<number | null>(null);
  const compare = useJobRun<Result>();
  const [tuneError, setTuneError] = useState<string | null>(null);
  const [allSightings, setAllSightings] = useState(false);
  const stoLibrary = useResource(paths.stoFiles(customerId), (signal) => api.stoFiles(customerId, { signal }));

  if (!detail) return res.error ? <ErrorLine error={res.error} /> : <Loading what="setup" />;
  const s = detail.setup;
  const { settings, readouts, readings, settingCount } = groupSetupValues(detail.values);
  const picks = comparePicks(s, siblings.data?.setups ?? []);
  const others = picks.map((p) => p.setup);
  const twins = sameNameAs(s, others);
  const other = others.find((x) => x.setup_id === otherId);
  const color = carAccent(s.car_path);

  function pick(id: number | null) {
    setOtherId(id);
    if (id === null) return compare.reset();
    void compare.run(() => request("POST", paths.setupCompare, { left_setup_id: setupId, right_setup_id: id }, { idempotencyKey: uuid4() }), "Comparing");
  }

  function tune() {
    const target = tuneTarget(detail!.observations);
    if ("blocked" in target) return setTuneError(target.blocked);
    setTuneError(null);
    navigate({ page: "tuning", customerId, analysisId: target.analysisId });
  }

  const linked = detail.observations.some((o) => o.session_id != null);
  const files = fileLabels(s, stoLibrary.data?.files ?? []);
  const sightings = allSightings ? detail.observations : detail.observations.slice(0, SIGHTINGS_SHOWN);
  const optionLabel = (o: Setup) => `${setupTitle(o)} · ${seenSpan(o)}`;
  return (
    <div class="setup-detail ds">
      <p class="crumbs">
        <a href={href({ page: "setups", customerId })}>Setups</a>
      </p>
      <header class="d-head" style={color ? `--car:${color}` : undefined}>
        <div>
          <h1>{setupTitle(s)}</h1>
          <p class="meta">
            <span>{s.car_name ?? s.car_path}</span>
            {s.raced ? <RacedMark count={racedCountOf(s)} /> : null}
            <span class="date">First seen {formatDate(s.first_seen_at)}</span>
          </p>
        </div>
        <button type="button" class="pill primary" disabled={!linked} title={linked ? undefined : "No recording yet"} onClick={tune}>
          Tune this setup
        </button>
      </header>
      {tuneError ? <Notice tone="warn">{tuneError}</Notice> : null}

      {twins.length > 0 ? <VersionBoard current={s} twins={twins} customerId={customerId} serverVersions={detail.versions} /> : null}

      {others.length > 0 ? (
        <section class="d-card" aria-label="Compare">
          <h2>Compare</h2>
          <label class="select-field">
            <span>With</span>
            <select value={otherId ?? ""} onChange={(e) => pick((e.currentTarget as HTMLSelectElement).value ? Number((e.currentTarget as HTMLSelectElement).value) : null)}>
              <option value="">No comparison</option>
              {picks.map(({ setup: o, blocked }) => (
                <option key={o.setup_id} value={o.setup_id} disabled={blocked !== null}>
                  {optionLabel(o)}
                  {blocked ? ` · ${blocked}` : ""}
                </option>
              ))}
            </select>
          </label>
          {compare.state.phase === "running" ? <Loading what="comparison" /> : null}
          {compare.state.phase === "error" ? <Notice tone="warn">{compare.state.message}</Notice> : null}
          {compare.state.phase === "done" && other ? <CompareTable result={compare.state.result} left={s} right={other} /> : null}
        </section>
      ) : null}

      {compare.state.phase !== "done" ? (
        <section class="d-card" aria-label="Setup values">
          <h2>
            Values <span class="count">{settingCount}</span>
          </h2>
          {settingCount === 0 ? (
            <p>
              <Chip variant="constraint">No values</Chip>
            </p>
          ) : (
            <ValueGroups groups={settings} />
          )}
          {readouts.length > 0 ? (
            <section aria-label="Garage readouts">
              <h3 class="readout-head">Garage readouts</h3>
              <ValueGroups groups={readouts} />
            </section>
          ) : null}
          {readings.length > 0 ? (
            <section aria-label="Run readings">
              <h3 class="readout-head">Run readings</h3>
              <ValueGroups groups={readings} />
            </section>
          ) : null}
        </section>
      ) : null}

      <section class="d-card" aria-label="Sightings">
        <h2>
          Sightings <span class="count">{detail.observations.length}</span>
        </h2>
        <ul class="sight-list">
          {sightings.map((o, i) => (
            <li key={i} class="sight">
              <span class="tag">{SOURCE_LABEL[o.source]}</span>
              <span>
                {o.setup_name ?? "Name unknown"}
                {o.is_modified != null ? <span class={`tag${o.is_modified ? " dashed" : ""}`}>{o.is_modified ? "Modified" : "Unmodified"}</span> : null}
              </span>
              {o.session_id != null ? <a href={href({ page: "investigation", customerId, sessionId: o.session_id, view: "race" })}>Session {o.session_id}</a> : <span />}
              <span class="date">{formatDateTime(o.observed_at)}</span>
            </li>
          ))}
        </ul>
        {detail.observations.length > SIGHTINGS_SHOWN && !allSightings ? (
          <button type="button" class="pill" onClick={() => setAllSightings(true)}>
            Show all {detail.observations.length}
          </button>
        ) : null}
      </section>

      {files.length > 0 ? (
        <section class="d-card d-file" aria-label="File">
          <h2>File</h2>
          <dl>
            {files.map((f) => (
              <div key={f.label}>
                <dt>File</dt>
                <dd>{f.file ? <a href={href({ page: "setupFile", customerId, fileId: f.file.id })}>{f.label}</a> : f.label}</dd>
              </div>
            ))}
          </dl>
        </section>
      ) : null}

      <NotesPanel customerId={customerId} kind="setup" subjectId={String(setupId)} />
    </div>
  );
}

