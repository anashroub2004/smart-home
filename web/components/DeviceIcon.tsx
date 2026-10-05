import type { ComponentType } from "react";
import type { DeviceIconName } from "@/lib/contract";
import { IAc, IFan, IFridge, IGeneric, IHeater, ILight, ILockClosed, IPlug, IPump, ITv, IWasher } from "./Icons";

const MAP: Record<DeviceIconName, ComponentType<{ size?: number }>> = {
  fan: IFan,
  light: ILight,
  washer: IWasher,
  lock: ILockClosed,
  tv: ITv,
  fridge: IFridge,
  ac: IAc,
  heater: IHeater,
  pump: IPump,
  plug: IPlug,
  generic: IGeneric,
};

/** Any unknown icon name falls back to the generic power symbol, so new device kinds never break the UI. */
export function DeviceIcon({ type, size = 20 }: { type?: string; size?: number }) {
  const I = MAP[(type ?? "generic") as DeviceIconName] ?? IGeneric;
  return <I size={size} />;
}

export const ICON_NAMES = Object.keys(MAP) as DeviceIconName[];
