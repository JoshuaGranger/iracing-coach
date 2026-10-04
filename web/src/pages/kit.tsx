import { useState } from "preact/hooks";
import { carColor } from "../core/identity";
import { Chip, Dot, EventHeader, FlagGlyph, IncidentChip, PageHead, PhaseTile, PositionChange, SetupBadge, Stat, StatusChip, Stripe, TrackSilhouette } from "../ui/kit";
import { PitStopCard, PitStopPictogram } from "../ui/pictograms";
import { Segmented } from "../ui/segmented";

/** The component gallery (`#/__kit`): every shared mark, selector and pictogram, for the screens that use them and for the screenshots that check them. */
const LOOP: [number, number][] = Array.from({ length: 64 }, (_, i) => {
  const a = (i / 64) * Math.PI * 2;
  return [Math.cos(a) * (1.6 + 0.35 * Math.cos(2 * a)), Math.sin(a) * (0.9 + 0.25 * Math.sin(3 * a))];
});

export default function KitPage() {
  const [phase, setPhase] = useState<string | null>("r");
  const [view, setView] = useState<string | null>("a");
  return (
    <>
      <PageHead title="Components" />
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
          <Chip tone="good">Raced</Chip>
          <Chip tone="warn">Caution</Chip>
          <Chip tone="bad">Failed</Chip>
          <Chip tone="info">Pit</Chip>
          <Chip>Neutral</Chip>
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
