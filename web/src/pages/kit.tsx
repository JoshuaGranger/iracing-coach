import { useState } from "preact/hooks";
import { carColor } from "../core/identity";
import { RUN_COLORS, raceProgressColour, runColour, runDash } from "../core/palette";
import { Chip, Dot, EventHeader, FlagGlyph, IncidentChip, PageHead, PhaseTile, PositionChange, SetupBadge, Stat, StatusChip, Stripe, TrackSilhouette } from "../ui/kit";
import { PitStopCard, PitStopPictogram } from "../ui/pictograms";
import { FilterBar, type FilterControl } from "../ui/filter-bar";
import { MultiSelect } from "../ui/multi-select";
import { Segmented } from "../ui/segmented";

/** The component gallery (`#/__kit`): every shared mark, selector and pictogram, for the screens that use them and for the screenshots that check them. */
const LOOP: [number, number][] = Array.from({ length: 64 }, (_, i) => {
  const a = (i / 64) * Math.PI * 2;
  return [Math.cos(a) * (1.6 + 0.35 * Math.cos(2 * a)), Math.sin(a) * (0.9 + 0.25 * Math.sin(3 * a))];
});

export default function KitPage() {
  const [phase, setPhase] = useState<string | null>("r");
  const [view, setView] = useState<string | null>("a");
  const [retries, setRetries] = useState(0);
  const [cars, setCars] = useState<string[]>([]);
  const [tracks, setTracks] = useState<string[]>([]);
  const [series, setSeries] = useState<string[]>([]);
  const controls: FilterControl[] = [
    {
      id: "car",
      label: "Car",
      node: <MultiSelect label="Car" noun="cars" options={[{ key: "a", label: "Dallara IR-18" }, { key: "b", label: "Global Mazda MX-5 Cup" }]} selected={cars} onChange={setCars} />,
    },
    {
      id: "track",
      label: "Track",
      node: <MultiSelect label="Track" noun="tracks" options={[{ key: "a", label: "Lime Rock Park" }, { key: "b", label: "Bristol Motor Speedway" }, { key: "c", label: "New Hampshire" }]} selected={tracks} onChange={setTracks} />,
    },
    {
      id: "series",
      label: "Series",
      node: <MultiSelect label="Series" noun="series" options={[{ key: "a", label: "IndyCar Series" }, { key: "b", label: "Global Mazda MX-5 Cup" }]} selected={series} onChange={setSeries} />,
    },
  ];
  return (
    <>
      <FilterBar title="Components" controls={controls} activeCount={cars.length + tracks.length + series.length} />
      <section class="panel" aria-label="Marks">
        <h2>Marks</h2>
        <div class="chips" style={{ alignItems: "center" }}>
          <Dot kind="green" label="Green laps" />
          <Dot kind="caution" label="Caution laps" />
          <Dot kind="pit" label="Pit" />
          <Dot kind="zero" label="None" />
          <FlagGlyph kind="white" />
          <FlagGlyph kind="checkered" />
          <Stripe color={carColor("kit-a")} />
          <Stripe color={carColor("kit-b")} />
          <Stripe color={carColor("kit-c")} />
          <SetupBadge type="Fixed" />
          <SetupBadge type="Open" />
          <PositionChange delta={8} />
          <PositionChange delta={-2} />
          <IncidentChip points={0} />
          <IncidentChip points={1} />
          <IncidentChip points={2} />
          <IncidentChip points={4} />
          <StatusChip kind="excl" title="caution" />
          <StatusChip kind="partial" />
          <Chip variant="exception" level="wait">
            Queued
          </Chip>
          <Chip variant="exception" action={{ label: "Retry", onClick: () => setRetries((n) => n + 1) }}>
            Failed · worker crash
          </Chip>
          <Chip variant="constraint">Not used in pace fit</Chip>
          <Chip variant="action" onClick={() => setRetries((n) => n + 1)}>
            Retry {retries}
          </Chip>
        </div>
      </section>
      <section class="panel" aria-label="Selectors and buttons">
        <h2>Selectors</h2>
        <div class="chips" style={{ alignItems: "center" }}>
          <Segmented
            variant="phase"
            label="Session phase"
            value={phase}
            onChange={setPhase}
            options={[
              { value: "p", label: "P", band: "p", ariaLabel: "Practice" },
              { value: "q", label: "Q", band: "q", ariaLabel: "Qualifying" },
              { value: "r", label: "R", band: "r", ariaLabel: "Race" },
              { value: "x", label: "R", band: "r", ariaLabel: "Race, not analyzed", disabled: true, title: "Not analyzed" },
            ]}
          />
          <Segmented
            label="View"
            value={view}
            onChange={setView}
            options={[
              { value: "a", label: "All" },
              { value: "b", label: "Green" },
              { value: "c", label: "Clean" },
            ]}
          />
          <div role="tablist" aria-label="Plain tabs" class="chips">
            <button type="button" role="tab" aria-selected="true" class="kit-tab-on">
              Selected tab
            </button>
            <button type="button" role="tab" aria-selected="false" class="kit-tab-off">
              Other tab
            </button>
          </div>
          <button type="button" class="btn btn-accent">
            Analyze
          </button>
          <button type="button" class="btn">
            Clear
          </button>
          <button type="button" class="btn" disabled>
            Disabled
          </button>
        </div>
      </section>
      <section class="panel" aria-label="Run and race colours">
        <h2>Run and race colours</h2>
        <div class="chips" style={{ alignItems: "center" }} data-testid="run-palette">
          {Array.from({ length: RUN_COLORS }, (_, i) => (
            <svg key={i} width="64" height="14" viewBox="0 0 64 14" role="img" aria-label={`Run ${i + 1}`}>
              <line x1="2" y1="7" x2="62" y2="7" stroke={runColour(i + 1)} stroke-width="3" stroke-dasharray={runDash(i + 1) || undefined} />
            </svg>
          ))}
        </div>
        <div class="chips" data-testid="progress-strip" style={{ gap: 2 }}>
          {Array.from({ length: 40 }, (_, i) => (
            <i key={i} style={{ width: 10, height: 18, background: raceProgressColour(i, 40) }} />
          ))}
        </div>
      </section>
      <section class="panel" aria-label="Pit stops">
        <h2>Pit stops</h2>
        <div class="chips" style={{ alignItems: "center", gap: "var(--space-6)" }}>
          <PitStopPictogram stop={{ lap: 37, tires: ["RF", "RR"], gallons: 11.2, capacityGal: 18.5, durationS: 13.8 }} />
          <PitStopPictogram stop={{ lap: 82, tires: [], gallons: 0, durationS: 9.4 }} />
          <PitStopPictogram stop={{ lap: 120, tires: ["LF", "RF", "LR", "RR"], gallons: 18, capacityGal: 18.5 }} />
          <PitStopCard stop={{ lap: 37, tires: ["RF", "RR"], gallons: 11.2, capacityGal: 18.5, durationS: 13.8 }} />
          <PitStopCard stop={{ lap: 46, tires: ["LF", "RF", "LR", "RR"], gallons: 0, durationS: 12.1 }} />
        </div>
      </section>
      <section class="bc-card bc-event" aria-label="Event">
        <EventHeader
          title="Bristol Motor Speedway"
          meta="Oval · Sep 28, 7:30 pm"
          carName="Dallara IR-18"
          carColorValue={carColor("kit-a")}
          series="Indy Oval Series"
          setupType="Fixed"
          silhouette={<TrackSilhouette points={LOOP} />}
        />
        <div class="bc-tiles">
          <PhaseTile phase="p" label="Practice" time="6:15 pm">
            <div class="bc-hero">
              <span class="bc-hero-figure">P4</span>
            </div>
            <div class="bc-stats">
              <Stat label="Laps">22</Stat>
              <Stat label="Best" tone="best">14.912</Stat>
              <Stat label="Green">
                <Dot kind="green" />
                20
              </Stat>
              <Stat label="Caution">
                <Dot kind="zero" />0
              </Stat>
            </div>
          </PhaseTile>
          <PhaseTile phase="q" label="Qualifying" time="6:40 pm">
            <div class="bc-hero">
              <span class="bc-hero-figure">P2</span>
            </div>
            <div class="bc-stats">
              <Stat label="Laps">3</Stat>
              <Stat label="Best">14.804</Stat>
              <Stat label="Stops">{null}</Stat>
              <Stat label="Fuel" tone="fuel">3.1 gal</Stat>
            </div>
          </PhaseTile>
          <PhaseTile phase="r" label="Race" time="7:30 pm" result="gain">
            <div class="bc-hero">
              <span class="bc-hero-figure">P4</span>
              <span class="bc-hero-from">from P12</span>
              <PositionChange delta={8} />
            </div>
            <div class="bc-stats">
              <Stat label="Green">
                <Dot kind="green" />
                118
              </Stat>
              <Stat label="Caution">
                <Dot kind="caution" />
                9
              </Stat>
              <Stat label="Pace" tone="gain">-0.012</Stat>
              <Stat label="Stops" tone="pit">3</Stat>
            </div>
          </PhaseTile>
        </div>
      </section>
      <section class="panel" aria-label="Scrolling panel">
        <h2>Scrolling</h2>
        <div data-testid="kit-scroll" style={{ height: "6rem", overflow: "auto" }}>
          {Array.from({ length: 30 }, (_, i) => (
            <p key={i}>Row {i + 1}</p>
          ))}
        </div>
      </section>
    </>
  );
}
