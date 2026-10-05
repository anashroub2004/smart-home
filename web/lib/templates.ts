// Device templates for "Add device". A template only pre-fills the form —
// everything (capabilities, permissions, rules, wiring) stays editable, and "Custom" starts from the minimum:
// on/off + energy reading. Adding a new template here needs no other code change.

import type { DeviceCaps, DeviceControl, DeviceHw, DeviceIconName, DeviceRules } from "./contract";

export interface DeviceTemplate {
  id: string;
  label: string;
  icon: DeviceIconName;
  hint: string;
  caps: DeviceCaps;
  control: DeviceControl;
  rules: DeviceRules;
  out: NonNullable<DeviceHw["out"]>;
  watts: number;
  /** level steps offered as an option ("Dimmable" / "Speed") */
  levelOption?: { label: string; steps: number[]; labels: string[] };
}

const ALL: DeviceControl = { app: true, button: true, rules: true, ai: true };
const NONE: DeviceControl = { app: false, button: false, rules: false, ai: false };
const SPEED = { steps: [40, 70, 100], labels: ["Low", "Medium", "High"] };
const DIM = { steps: [30, 60, 100], labels: ["Dim", "Medium", "Bright"] };

export const TEMPLATES: DeviceTemplate[] = [
  {
    id: "light", label: "Light", icon: "light", hint: "Relay. Turns on when it is dark and someone is there.",
    caps: { power: "write", energy: "ina" }, control: ALL,
    rules: { off_when_empty: true, on_when_dark: true }, out: "relay", watts: 1.2,
    levelOption: { label: "Dimmable", ...DIM },
  },
  {
    id: "fan", label: "Fan", icon: "fan", hint: "MOSFET (PWM) for speed. Follows the room temperature.",
    caps: { power: "write", level: SPEED, energy: "ina" }, control: ALL,
    rules: { off_when_empty: true, follow_temp: true }, out: "pwm", watts: 2.4,
    levelOption: { label: "Speed control", ...SPEED },
  },
  {
    id: "ac", label: "Air conditioner", icon: "ac", hint: "IR LED that imitates the remote: on/off and mode.",
    caps: { power: "write", mode: { options: ["Cool", "Heat", "Fan"] }, energy: "estimate" }, control: ALL,
    rules: { off_when_empty: true, follow_temp: true }, out: "ir", watts: 900,
  },
  {
    id: "heater", label: "Water heater", icon: "heater", hint: "Relay. The AI can warm it up before your usual time.",
    caps: { power: "write", energy: "ina" }, control: { app: true, button: true, rules: false, ai: true },
    rules: {}, out: "relay", watts: 5,
  },
  {
    id: "washer", label: "Washer", icon: "washer", hint: "Started by you only — never by rules or the AI.",
    caps: { power: "write", status: true, energy: "ina" }, control: { app: true, button: true, rules: false, ai: false },
    rules: {}, out: "pwm", watts: 3.6,
  },
  {
    id: "pump", label: "Water pump", icon: "pump", hint: "Relay. Manual or by your own rules.",
    caps: { power: "write", energy: "ina" }, control: { app: true, button: true, rules: false, ai: false },
    rules: {}, out: "relay", watts: 3,
  },
  {
    id: "plug", label: "Smart plug", icon: "plug", hint: "Any appliance behind a relay, with its energy measured.",
    caps: { power: "write", energy: "ina" }, control: { app: true, button: true, rules: true, ai: false },
    rules: { off_when_empty: false }, out: "relay", watts: 2,
  },
  {
    id: "tv", label: "TV", icon: "tv", hint: "Monitor only: on/off and energy read from the current it draws.",
    caps: { power: "read", energy: "ina" }, control: NONE, rules: {}, out: "none", watts: 3,
  },
  {
    id: "fridge", label: "Fridge", icon: "fridge", hint: "Monitor only. Alerts you if it stops.",
    caps: { power: "read", energy: "ina" }, control: NONE, rules: { alert_if_off_min: 10 }, out: "none", watts: 2,
  },
  {
    id: "custom", label: "Custom", icon: "generic", hint: "The minimum: on/off and energy. Choose everything else.",
    caps: { power: "write", energy: "ina" }, control: { app: true, button: false, rules: false, ai: false },
    rules: {}, out: "relay", watts: 1,
    levelOption: { label: "Level control", steps: [25, 50, 75, 100], labels: ["25%", "50%", "75%", "100%"] },
  },
];

export const templateById = (id?: string) => TEMPLATES.find((t) => t.id === id);
