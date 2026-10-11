import { surfaceTempColour } from "../../ui/pictograms";
import { bandNames, type RunTires } from "./runs-model";

/** Recorded tread and heat in physical left-to-right order, nose up. */
export function RunTireDetails({ tires }: { tires: RunTires }) {
  return <div class="ri-tire-details" role="tooltip" data-run-tire-details>
    <b>Tires at L{tires.atLap}</b>
    <small>Front ↑ · tread remaining % · surface °F</small>
    <div class="ri-tire-detail-grid">
      {tires.corners.map((corner) => <div class="ri-tire-detail-corner" key={corner.id} data-detail-tire={corner.id}>
        <b>{corner.id}</b>
        <div class="ri-tire-detail-tread">
          {corner.bands.map((band, index) => <div key={index} class="ri-tire-detail-band">
            <small>{bandNames(corner.id)[index]}</small>
            <div class="ri-tire-detail-bar"><i style={{ height: `${band.remaining ?? 0}%`, background: surfaceTempColour(band.surfaceF) }} /></div>
            <b>{band.remaining === null ? "—" : Math.round(band.remaining)}</b>
            <small>{band.surfaceF === null ? "—" : Math.round(band.surfaceF)}°</small>
          </div>)}
        </div>
        <small>{corner.pressure ? `${corner.pressure.psi.toFixed(1)} psi${corner.pressure.kind ? ` · ${corner.pressure.kind}` : ""}` : "Pressure unavailable"}</small>
      </div>)}
    </div>
    <small>I inside · M middle · O outside · — unavailable</small>
  </div>;
}
