import type { DeviceType } from "@/lib/contract";
import { IFan, ILight, ILockClosed, IWasher } from "./Icons";

export function DeviceIcon({ type, size = 20 }: { type: DeviceType; size?: number }) {
  if (type === "fan") return <IFan size={size} />;
  if (type === "light") return <ILight size={size} />;
  if (type === "washer") return <IWasher size={size} />;
  return <ILockClosed size={size} />;
}
