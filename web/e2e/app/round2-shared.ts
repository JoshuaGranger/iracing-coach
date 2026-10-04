import { type Device, DEVICES } from "./helpers";

/** Round 2 acceptance viewports (web-round-2-plan.md): desktop 1440x900, tablet 1024x768, phone 390x844. */
export const ROUND2_DEVICES: Device[] = [
  { name: "desktop 1440x900", viewport: { width: 1440, height: 900 }, touch: false },
  DEVICES.find((d) => d.name.startsWith("tablet"))!,
  DEVICES.find((d) => d.name.startsWith("phone"))!,
];
