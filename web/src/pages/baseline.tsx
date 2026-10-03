import { useMemo, useState } from "preact/hooks";
import { api, paths } from "../core/api";
import { humanize } from "../core/format";
import { obj, str, strings } from "../core/json";
import { useJobRun } from "../core/jobs";
import { useResource } from "../core/resource";
import { href } from "../core/router";
import { Chip, ErrorLine, Loading, Notice, PageHead } from "../ui/kit";
import { currentSeason } from "../setups/model";

/** The fields of `setup-package-v1` this page shows; everything else in the result is ignored, not dropped. */
export function readPackage(result: unknown) {
  const r = obj(result);
  const baseline = obj(r.baseline);
  const confirmation = obj(r.baseline_confirmation);
  const donor = obj(r.donor);
  const context = obj(r.context);
  const caution = [str(donor.warning), confirmation.confirmed === true ? "" : str(confirmation.reason), ...strings(baseline.identity_warnings)].filter(Boolean);
  return {
    packageId: str(r.package_id),
    status: str(r.status),
    setupName: str(baseline.stem),
    carFolder: str(baseline.car_folder),
    fingerprint: str(baseline.fingerprint),
    donor: str(donor.donor),
    donorReason: str(donor.reason),
    matched: strings(donor.matched_characteristics),
    confirmed: confirmation.confirmed === true,
    confirmationStatus: str(confirmation.status),
    cautions: [...new Set(caution)],
    checks: strings(r.baseline_checks).concat(strings(baseline.builder_notes ? [str(baseline.builder_notes)] : [])),
    context: Object.entries(context).filter(([, v]) => typeof v === "string") as [string, string][],
    sourceUntouched: r.source_setup_files_modified !== true,
    produced: r.simulator_loadable_setup_produced === true,
  };
}

export default function BaselinePage({ customerId }: { customerId: number }) {
  const options = useResource(paths.carsAndTracks(customerId), () => api.carsAndTracks(customerId));
  const [season, setSeason] = useState(() => currentSeason(new Date()));
  const [car, setCar] = useState("");
  const [track, setTrack] = useState("");
  const run = useJobRun<unknown>();
  const cars = useMemo(() => (options.data?.cars ?? []).map((c) => c.car_name ?? c.car_path), [options.data]);
  const tracks = useMemo(() => (options.data?.tracks ?? []).map((t) => (t.track_config ? `${t.track} - ${t.track_config}` : t.track)), [options.data]);
  const known = (value: string, choices: string[]) => choices.length === 0 || choices.some((c) => c.toLowerCase() === value.trim().toLowerCase());
  const ready = season.trim() && car.trim() && track.trim() && known(car, cars) && known(track, tracks);
  const pkg = run.state.phase === "done" ? readPackage(run.state.result) : null;

  return (
    <>
      <p class="crumbs">
        <a href={href({ page: "setups", customerId })}>Setups</a>
      </p>
      <PageHead title="Find a baseline" sub="A starting setup for a car and track, from setups you already have or have driven." />
      {options.error && !options.data ? <ErrorLine error={options.error} /> : null}
      <form
        class="panel form-grid"
        onSubmit={(e) => {
          e.preventDefault();
          if (!ready) return;
          void run.run(() => api.findBaseline(customerId, { season: season.trim(), car: car.trim(), track: track.trim() }), "Finding a starting point");
        }}
      >
        <label>
          <span>Season</span>
          <input value={season} maxLength={32} onInput={(e) => setSeason((e.currentTarget as HTMLInputElement).value)} />
        </label>
        <label>
          <span>Car</span>
          <input list="baseline-cars" autocomplete="off" value={car} aria-invalid={car !== "" && !known(car, cars)} onInput={(e) => setCar((e.currentTarget as HTMLInputElement).value)} />
          <datalist id="baseline-cars">
            {cars.map((c) => (
              <option key={c} value={c} />
            ))}
          </datalist>
          {car !== "" && !known(car, cars) ? <small class="field-error">Pick a car from your recordings.</small> : null}
        </label>
        <label>
          <span>Track and layout</span>
          <input list="baseline-tracks" autocomplete="off" value={track} aria-invalid={track !== "" && !known(track, tracks)} onInput={(e) => setTrack((e.currentTarget as HTMLInputElement).value)} />
          <datalist id="baseline-tracks">
            {tracks.map((t) => (
              <option key={t} value={t} />
            ))}
          </datalist>
          {track !== "" && !known(track, tracks) ? <small class="field-error">Pick a track from your recordings.</small> : null}
        </label>
        <div class="form-actions">
          <button type="submit" class="btn btn-accent" disabled={!ready || run.state.phase === "running"}>
            {run.state.phase === "running" ? "Finding" : "Find starting point"}
          </button>
        </div>
      </form>
      {run.state.phase === "running" ? <Loading what="a starting point" /> : null}
      {run.state.phase === "error" ? <Notice tone="warn">{run.state.message}</Notice> : null}
      {pkg ? (
        <section class="panel" aria-label="Starting point" data-testid="baseline-result">
          <h2>{pkg.setupName || pkg.donor || "Starting point"}</h2>
          <p class="chips">
            <Chip tone={pkg.confirmed ? "good" : "warn"}>{pkg.confirmed ? "Confirmed" : humanize(pkg.confirmationStatus) === "-" ? "Provisional" : humanize(pkg.confirmationStatus)}</Chip>
            {pkg.status ? <Chip>{humanize(pkg.status)}</Chip> : null}
            {pkg.sourceUntouched ? <Chip title="Source setup files are never changed">Source files unchanged</Chip> : null}
          </p>
          <dl class="facts">
            {pkg.carFolder ? (
              <div>
                <dt>Car</dt>
                <dd>{pkg.carFolder}</dd>
              </div>
            ) : null}
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
          <p class="sub">Drive the unchanged setup, including a full green run if you test late-run pace, then tune from that recording.</p>
          <a class="btn" href={href({ page: "tuning", customerId, analysisId: null })}>
            Progressive tuning
          </a>
        </section>
      ) : null}
    </>
  );
}
