import { DoorClosed, Fan, Lightbulb, WashingMachine, type LucideIcon } from "lucide-react";
import type { DeviceType } from "@/lib/contract";

export const DEVICE_ICON: Record<DeviceType, LucideIcon> = {
  fan: Fan,
  light: Lightbulb,
  washer: WashingMachine,
  lock: DoorClosed,
};

/** CSS variable for each device type's accent colour. */
export const DEVICE_COLOR: Record<DeviceType, string> = {
  fan: "var(--color-fan)",
  light: "var(--color-light)",
  washer: "var(--color-washer)",
  lock: "var(--color-lock)",
};
