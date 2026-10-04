import { useMemo, useState } from "preact/hooks";
import { api, paths } from "../core/api";
import { humanize } from "../core/format";
import { obj, str, strings } from "../core/json";
import { useJobRun } from "../core/jobs";
import { useResource } from "../core/resource";
import { href } from "../core/router";
import { ErrorLine, Loading, Notice, PageHead } from "../ui/kit";
import { currentSeason, normalizeSeason } from "../setups/model";
import "../setups/setups.css";

/** One setup the package names: the race baseline or the separate qualifying candidate. */
export interface Candidate {
  name: string;
  carFolder: string;
  fingerprint: string;
  /** The exported sheet's values can be read; an encrypted `.sto` alone is opaque. */
  readable: boolean;
  pairStatus: string;
}

function readCandidate(v: unknown): Candidate | null {
  const c = obj(v);
  if (Object.keys(c).length === 0) return null;
  return {
    name: str(c.stem),
    carFolder: str(c.car_folder),
    fingerprint: str(c.fingerprint),
    readable: Object.keys(obj(c.parsed_html)).length > 0,
    pairStatus: str(c.pair_status),
  };
}

/** The fields of `setup-package-v1` this page shows; everything else in the result is ignored, not dropped. */
export function readPackage(result: unknown) {
  const r = obj(result);
  const baseline = obj(r.baseline);
  const confirmation = obj(r.baseline_confirmation);
  const donor = obj(r.donor);
  const context = obj(r.context);
  const caution = [str(donor.warning), confirmation.confirmed === true ? "" : str(confirmation.reason), ...strings(baseline.identity_warnings)].filter(Boolean);
  const race = readCandidate(r.baseline);
  return {
    /** The backend found a setup to start from; false is "no matching file", never a starting point. */
    found: r.ok === true,
    packageId: str(r.package_id),
    status: str(r.status),
    setupName: str(baseline.stem),
    race,
    qualifying: readCandidate(r.qualifying),
    donor: str(donor.donor),
    donorReason: str(donor.reason),
    matched: strings(donor.matched_characteristics),
    confirmed: confirmation.confirmed === true,
    confirmationStatus: str(confirmation.status),
    cautions: [...new Set(caution)],
    context: Object.entries(context).filter(([, v]) => typeof v === "string") as [string, string][],
    /** Shown only when the package says so; an absent field is not "unchanged". */
    sourceUntouched: r.source_setup_files_modified === false,
  };
}

/** What a driver does with a baseline before relying on it. */
export const CHECKS = ["Pass tech", "Save under a new name", "Run a clean baseline", "Keep the fingerprint for rollback"] as const;

const short = (fingerprint: string) => (fingerprint.length > 16 ? `${fingerprint.slice(0, 8)}…${fingerprint.slice(-6)}` : fingerprint);

function CandidateTile({ title, c }: { title: string; c: Candidate | null }) {
  if (!c) {
    return (
      <div class="b-cand none" data-candidate={title.toLowerCase()}>
        <span class="caps">{title}</span>
        <span class="nm">None</span>
      </div>
    );
  }
  return (
    <div class="b-cand" data-candidate={title.toLowerCase()}>
      <span class="caps">{title}</span>
      <span class="nm">{c.name || "Unnamed"}</span>
      <span class="d-facts">{c.readable ? <span class="vk">Readable</span> : <span class="vu">Opaque</span>}</span>
      {c.fingerprint ? (
        <span class="fp" title={c.fingerprint}>
          {short(c.fingerprint)}
        </span>
      ) : null}
    </div>
  );
}

export default function BaselinePage({ customerId }: { customerId: number }) {
  const options = useResource(paths.carsAndTracks(customerId), () => api.carsAndTracks(customerId));
  const [season, setSeason] = useState(() => currentSeason(new Date()));
  const [car, setCar] = useState("");
  const [track, setTrack] = useState("");
  const run = useJobRun<unknown>();
  const cars = options.data?.cars ?? [];
  const tracks = useMemo(() => (options.data?.tracks ?? []).map((t) => (t.track_config ? `${t.track} - ${t.track_config}` : t.track)), [options.data]);
  /** The backend matches the car folder: a car picked by name is sent by its path; text of the driver's own goes as typed. */
  const carValue = (text: string) => {
    const t = text.trim().toLowerCase();
    const hit = cars.find((c) => (c.car_name ?? "").toLowerCase() === t || c.car_path.toLowerCase() === t);
    return hit ? hit.car_path : text.trim();
  };
  const ready = season.trim() !== "" && car.trim() !== "" && track.trim() !== "";
  const pkg = run.state.phase === "done" ? readPackage(run.state.result) : null;

  return (
    <div class="baseline-page ds">
      <p class="crumbs">
        <a href={href({ page: "setups", customerId })}>Setups</a>
      </p>
      <PageHead title="Find a baseline" />
      {options.error && !options.data ? <ErrorLine error={options.error} /> : null}
      <form
        class="d-card b-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (!ready) return;
          void run.run(() => api.findBaseline(customerId, { season: normalizeSeason(season), car: carValue(car), track: track.trim() }), "Finding a starting point");
        }}
      >
        <label>
          <span>Season</span>
          <input value={season} maxLength={32} onInput={(e) => setSeason((e.currentTarget as HTMLInputElement).value)} />
        </label>
        <label>
          <span>Car</span>
          <input list="baseline-cars" autocomplete="off" value={car} onInput={(e) => setCar((e.currentTarget as HTMLInputElement).value)} />
          <datalist id="baseline-cars">
            {cars.map((c) => (
              <option key={c.car_path} value={c.car_name ?? c.car_path} />
            ))}
          </datalist>
        </label>
        <label>
          <span>Track and layout</span>
          <input list="baseline-tracks" autocomplete="off" value={track} onInput={(e) => setTrack((e.currentTarget as HTMLInputElement).value)} />
          <datalist id="baseline-tracks">
            {tracks.map((t) => (
              <option key={t} value={t} />
            ))}
          </datalist>
        </label>
        <div class="form-actions">
          <button type="submit" class="pill primary" disabled={!ready || run.state.phase === "running"}>
            {run.state.phase === "running" ? "Finding" : "Find starting point"}
          </button>
        </div>
      </form>
      {run.state.phase === "running" ? <Loading what="a starting point" /> : null}
      {run.state.phase === "error" ? <Notice tone="warn">{run.state.message}</Notice> : null}
      {pkg && !pkg.found ? (
        <Notice tone="warn" role="status">
          No setup file for this car, track and season. Save or export a baseline in iRacing, then retry.
        </Notice>
      ) : null}
      {pkg?.found ? (
        <section class="d-card" aria-label="Starting point" data-testid="baseline-result">
          <h2>{pkg.setupName || pkg.donor || "Starting point"}</h2>
          <p class="d-facts">
            <span class={`tag${pkg.confirmed ? " solid" : " dashed"}`}>{pkg.confirmed ? "Confirmed" : humanize(pkg.confirmationStatus) === "-" ? "Provisional" : humanize(pkg.confirmationStatus)}</span>
            {pkg.status ? <span class="tag">{humanize(pkg.status)}</span> : null}
            {pkg.sourceUntouched ? (
              <span class="tag" title="Source setup files are never changed">
                Source files unchanged
              </span>
            ) : null}
          </p>
          <div class="b-cands">
            <CandidateTile title="Race" c={pkg.race} />
            <CandidateTile title="Qualifying" c={pkg.qualifying} />
          </div>
          <dl class="facts">
            {pkg.donor ? (
              <div>
                <dt>Donor track</dt>
                <dd>{pkg.donor}</dd>
              </div>
            ) : null}
            {pkg.donorReason ? (
              <div>
                <dt>Why</dt>
                <dd>{pkg.donorReason}</dd>
              </div>
            ) : null}
            {pkg.matched.length ? (
              <div>
                <dt>Matches</dt>
                <dd>{pkg.matched.join(", ")}</dd>
              </div>
            ) : null}
          </dl>
          {pkg.cautions.length ? (
            <ul class="cautions" aria-label="Cautions">
              {pkg.cautions.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
          ) : null}
          <h3>Checks</h3>
          <ol class="b-checks" aria-label="Checks">
            {CHECKS.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ol>
          <p>
            <a class="pill" href={href({ page: "tuning", customerId, analysisId: null })}>
              Progressive tuning
            </a>
          </p>
        </section>
      ) : null}
    </div>
  );
}
