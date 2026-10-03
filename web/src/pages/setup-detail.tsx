import { useState } from "preact/hooks";
import { type Schemas, api, paths, request } from "../core/api";
import { formatDateTime, plural } from "../core/format";
import { useJobRun } from "../core/jobs";
import { useResource } from "../core/resource";
import { uuid4 } from "../core/uuid";
import { href, navigate } from "../core/router";
import { Chip, ErrorLine, Loading, Notice, PageHead } from "../ui/kit";
import { NotesPanel } from "../ui/notes";
import { SOURCE_LABEL, classifyCompare, groupSetupValues, sameNameAs, seenSpan, setupTitle, tuneTarget, uniqueSources } from "../setups/model";

type Result = Schemas["SetupCompareResult"];

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

function CompareTable({ result, leftName, rightName }: { result: Result; leftName: string; rightName: string }) {
  const [all, setAll] = useState(false);
  const rows = classifyCompare(result.rows);
  const row = (r: (typeof rows.changed)[number]) => (
    <tr key={r.key} data-kind={r.kind} title={r.kind === "unknown" ? "Missing or unreadable on one side; never treated as equal." : undefined}>
      <th scope="row">{r.label}</th>
      <td>{r.left}</td>
      <td>{r.right}</td>
    </tr>
  );
  return (
    <div class="compare">
      <p class="compare-summary" data-testid="compare-summary">
        <b>{rows.changed.length} changed</b> · <b>{rows.unknown.length} unknown</b> · {result.identical_count} identical
        {rows.readout.length ? ` · ${plural(rows.readout.length, "garage readout")} moved` : ""}
        {rows.identical.length > 0 ? (
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
              <th scope="col">{leftName}</th>
              <th scope="col">{rightName}</th>
            </tr>
          </thead>
          <tbody>
            {rows.changed.map(row)}
            {rows.unknown.map(row)}
            {rows.readout.length ? (
              <tr class="group-head">
                <th colSpan={3}>Garage readouts (not counted as changes)</th>
              </tr>
            ) : null}
            {rows.readout.map(row)}
            {all ? rows.identical.map(row) : null}
          </tbody>
        </table>
      </div>
      {rows.changed.length === 0 && rows.unknown.length === 0 ? <p class="sub">Every readable setting matches.</p> : null}
    </div>
  );
}

export default function SetupDetailPage({ customerId, setupId }: { customerId: number; setupId: number }) {
  const res = useResource(paths.setup(setupId), (signal) => api.setup(setupId, { signal }));
  const detail = res.data;
  const car = detail?.setup.car_path ?? null;
  const siblings = useResource(car ? paths.setups(customerId, { car_path: car, limit: 200 }) : null, () => api.setups(customerId, { car_path: car!, limit: 200 }));
  const [otherId, setOtherId] = useState<number | null>(null);
  const compare = useJobRun<Result>();
  const [tuneError, setTuneError] = useState<string | null>(null);

  if (!detail) return res.error ? <ErrorLine error={res.error} /> : <Loading what="setup" />;
  const s = detail.setup;
  const { settings, readouts, settingCount } = groupSetupValues(detail.values, detail.identity_excluded_keys);
  const others = (siblings.data?.setups ?? []).filter((x) => x.setup_id !== setupId);
  const twins = sameNameAs(s, others);
  const recordings = new Set(detail.observations.filter((o) => o.session_id != null).map((o) => o.session_id)).size;
  const visits = new Set(detail.observations.filter((o) => o.visit_id).map((o) => o.visit_id)).size;
  const other = others.find((x) => x.setup_id === otherId);

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
  return (
    <>
      <p class="crumbs">
        <a href={href({ page: "setups", customerId })}>Setups</a>
      </p>
      <PageHead title={setupTitle(s)} sub={`${s.car_name ?? s.car_path} · first seen ${formatDateTime(s.first_seen_at)}`}>
        <button type="button" class="btn btn-accent" disabled={!linked} title={linked ? "Tune from the recording that used this setup" : "Needs a recording with this setup"} onClick={tune}>
          Tune this setup
        </button>
      </PageHead>
      {tuneError ? <Notice tone="warn">{tuneError}</Notice> : null}
      <p class="chips">
        {s.raced ? <Chip tone="good">Raced</Chip> : null}
        {uniqueSources(s.sources).map((x) => (
          <Chip key={x}>{x}</Chip>
        ))}
        {s.names.map((n) => (
          <Chip key={n} title="Setup file name">
            {n}
          </Chip>
        ))}
      </p>

      {twins.length > 0 ? (
        <p class="sub" data-testid="same-name">
          {twins.length === 1 ? "Another setup carries" : `${twins.length} other setups carry`} the file name {s.names.find((n) => twins.some((t) => t.names.includes(n)))}: the file was changed between sessions, so each version has its own values. This one was seen {seenSpan(s)}, in{" "}
          {[recordings ? plural(recordings, "recording") : "", visits ? plural(visits, "garage visit") : ""].filter(Boolean).join(" and ") || "no recording or garage visit"}. Compare with the others below.
        </p>
      ) : null}

      <section class="panel" aria-label="Compare">
        <h2>Compare</h2>
        {others.length === 0 ? (
          <p class="sub">No other setup of this car to compare with.</p>
        ) : (
          <label class="select-field">
            <span>With</span>
            <select value={otherId ?? ""} onChange={(e) => pick((e.currentTarget as HTMLSelectElement).value ? Number((e.currentTarget as HTMLSelectElement).value) : null)}>
              <option value="">No comparison</option>
              {others.map((o) => (
                <option key={o.setup_id} value={o.setup_id}>
                  {setupTitle(o)} · {sameNameAs(o, [s, ...others]).length > 0 ? seenSpan(o) : formatDateTime(o.last_seen_at)}
                </option>
              ))}
            </select>
          </label>
        )}
        {compare.state.phase === "running" ? <Loading what="comparison" /> : null}
        {compare.state.phase === "error" ? <Notice tone="warn">{compare.state.message}</Notice> : null}
        {compare.state.phase === "done" && other ? <CompareTable result={compare.state.result} leftName={setupTitle(s)} rightName={setupTitle(other)} /> : null}
      </section>

      {compare.state.phase !== "done" ? (
        <section class="panel" aria-label="Setup values">
          <h2>
            Values <span class="count">{settingCount}</span>
          </h2>
          {settingCount === 0 ? <p class="sub">Values unknown for this setup.</p> : <ValueGroups groups={settings} />}
          {readouts.length > 0 ? (
            <>
              <h3 class="readout-head">Readouts, not setup values</h3>
              <p class="sub">Computed by the garage or read from the last run. They change without a setup change and are never compared.</p>
              <ValueGroups groups={readouts} />
            </>
          ) : null}
        </section>
      ) : null}

      <section class="panel" aria-label="Sightings">
        <h2>
          Sightings <span class="count">{detail.observations.length}</span>
        </h2>
        <ul class="rows compact">
          {detail.observations.slice(0, 25).map((o, i) => (
            <li key={i} class="row-link row-static">
              <span class="row-main">
                <Chip>{SOURCE_LABEL[o.source]}</Chip>
                <span class="row-title">{o.setup_name ?? "Name unknown"}</span>
                {o.is_modified != null ? <Chip tone={o.is_modified ? "warn" : undefined}>{o.is_modified ? "Modified" : "Unmodified"}</Chip> : null}
                {o.session_id != null ? <a href={href({ page: "investigation", customerId, sessionId: o.session_id, view: "race" })}>Session {o.session_id}</a> : null}
              </span>
              <span class="row-meta">{formatDateTime(o.observed_at)}</span>
            </li>
          ))}
        </ul>
      </section>

      <NotesPanel customerId={customerId} kind="setup" subjectId={String(setupId)} />
    </>
  );
}
