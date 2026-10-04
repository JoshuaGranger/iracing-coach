import { TEXT_SIZES, chooseTextSize, textSize } from "../core/text-size";
import { useStore } from "../core/store";

/**
 * The one text-size control: Default, Larger, Largest. It sits in the top bar, which every page shows. Picking a step resizes the
 * whole app at once and saves it with the driver's prefs; `customerId` is null before a driver is chosen, when only this device
 * remembers it.
 */
export function TextSizeControl({ customerId }: { customerId: number | null }) {
  const current = useStore(textSize);
  return (
    <div class="seg text-size" role="group" aria-label="Text size">
      {TEXT_SIZES.map((step) => (
        <button
          key={step.id}
          type="button"
          class={`step-${step.id}${current === step.id ? " on" : ""}`}
          aria-pressed={current === step.id}
          aria-label={step.label}
          title={`${step.label} text`}
          onClick={() => chooseTextSize(step.id, customerId)}
        >
          A
        </button>
      ))}
    </div>
  );
}
