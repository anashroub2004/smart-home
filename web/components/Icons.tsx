// Icons copied from the design prototype so the app looks exactly like it.
import type { SVGProps } from "react";

type P = SVGProps<SVGSVGElement> & { size?: number; sw?: number };

function Svg({ size = 20, sw = 1.8, children, ...rest }: P) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={sw}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...rest}
    >
      {children}
    </svg>
  );
}

export const IHouse = (p: P) => (
  <Svg {...p}>
    <path d="M3 10.5 12 3l9 7.5" />
    <path d="M5 9.5V20h14V9.5" />
  </Svg>
);
export const IHome = (p: P) => (
  <Svg {...p}>
    <path d="M3 10.5 12 3l9 7.5" />
    <path d="M5 9.5V20h14V9.5" />
    <path d="M10 20v-6h4v6" />
  </Svg>
);
export const IEnergy = (p: P) => (
  <Svg {...p}>
    <path d="M13 2 4 14h7l-1 8 9-12h-7z" />
  </Svg>
);
export const IShield = (p: P) => (
  <Svg {...p}>
    <path d="M12 3 4 6v6c0 4.5 3.4 8.3 8 9 4.6-.7 8-4.5 8-9V6z" />
  </Svg>
);
export const ISparkles = (p: P) => (
  <Svg {...p}>
    <path d="M12 3l1.8 4.7L18.5 9l-4.7 1.8L12 15.5l-1.8-4.7L5.5 9l4.7-1.3z" />
    <path d="M18 15l.8 2.2L21 18l-2.2.8L18 21l-.8-2.2L15 18l2.2-.8z" />
  </Svg>
);
export const ISparkle = (p: P) => (
  <Svg {...p}>
    <path d="M12 3l1.8 4.7L18.5 9l-4.7 1.8L12 15.5l-1.8-4.7L5.5 9l4.7-1.3z" />
  </Svg>
);
export const IHistory = (p: P) => (
  <Svg {...p}>
    <path d="M3 12a9 9 0 1 0 3-6.7L3 8" />
    <path d="M3 3v5h5M12 7v5l3 2" />
  </Svg>
);
export const IBell = (p: P) => (
  <Svg {...p}>
    <path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
    <path d="M10.3 21a1.9 1.9 0 0 0 3.4 0" />
  </Svg>
);
export const IGear = (p: P) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="3" />
    <path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1" />
  </Svg>
);
export const ILeave = (p: P) => (
  <Svg {...p}>
    <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
    <path d="m16 17 5-5-5-5M21 12H9" />
  </Svg>
);
export const IMoon = (p: P) => (
  <Svg {...p}>
    <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
  </Svg>
);
export const IChevron = (p: P) => (
  <Svg sw={2} {...p}>
    <path d="m9 6 6 6-6 6" />
  </Svg>
);
export const IBack = (p: P) => (
  <Svg sw={2} {...p}>
    <path d="m15 6-6 6 6 6" />
  </Svg>
);
export const IClose = (p: P) => (
  <Svg sw={2} {...p}>
    <path d="M6 6l12 12M18 6 6 18" />
  </Svg>
);
export const ICheck = (p: P) => (
  <Svg sw={2} {...p}>
    <path d="M20 6 9 17l-5-5" />
  </Svg>
);
export const IFan = (p: P) => (
  <Svg sw={1.9} {...p}>
    <circle cx="12" cy="12" r="1.6" />
    <path d="M12 10.4C11 7 11.5 3.5 14 3.5s2.5 4-2 6.9M13.6 12.5c3.4-.4 6.4 1.4 5.4 3.6s-4.6.9-5.4-3.6M10.6 12.9c-2 2.8-5.4 4-6.4 1.8s2.2-3.9 6.4-1.8" />
  </Svg>
);
export const ILight = (p: P) => (
  <Svg sw={1.9} {...p}>
    <path d="M9 18h6M10 21h4" />
    <path d="M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z" />
  </Svg>
);
export const IWasher = (p: P) => (
  <Svg sw={1.9} {...p}>
    <rect x="4" y="3" width="16" height="18" rx="2.5" />
    <circle cx="12" cy="13" r="4.5" />
    <path d="M8 6.5h.01M11 6.5h3" />
  </Svg>
);
export const ILockClosed = (p: P) => (
  <Svg {...p}>
    <rect x="5" y="11" width="14" height="10" rx="2" />
    <path d="M8 11V7a4 4 0 0 1 8 0v4" />
  </Svg>
);
export const ILockOpen = (p: P) => (
  <Svg {...p}>
    <rect x="5" y="11" width="14" height="10" rx="2" />
    <path d="M8 11V7a4 4 0 0 1 7.5-2" />
  </Svg>
);
export const IPlay = (p: P) => (
  <Svg sw={1.6} {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="m10 8.5 5 3.5-5 3.5z" />
  </Svg>
);
export const IFinger = (p: P) => (
  <Svg {...p}>
    <path d="M12 11v3a8 8 0 0 1-1.5 4.6M8.5 7.4A5 5 0 0 1 17 11v1.5M7 11a5 5 0 0 1 .3-1.7M7 14.5a12 12 0 0 1-1 3M16.8 16a14 14 0 0 1-.8 3M12 8a3 3 0 0 1 3 3v2" />
  </Svg>
);
export const IPin = ({ size = 18 }: P) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
    <circle cx="6" cy="6" r="1.6" />
    <circle cx="12" cy="6" r="1.6" />
    <circle cx="18" cy="6" r="1.6" />
    <circle cx="6" cy="12" r="1.6" />
    <circle cx="12" cy="12" r="1.6" />
    <circle cx="18" cy="12" r="1.6" />
    <circle cx="12" cy="18" r="1.6" />
  </svg>
);
export const IPhone = (p: P) => (
  <Svg {...p}>
    <rect x="7" y="2" width="10" height="20" rx="2" />
    <path d="M11 18h2" />
  </Svg>
);
export const IHand = (p: P) => (
  <Svg {...p}>
    <path d="M9 11V5.5a1.5 1.5 0 0 1 3 0V11M12 10V4.5a1.5 1.5 0 0 1 3 0V11M15 10.5V6a1.5 1.5 0 0 1 3 0v8a7 7 0 0 1-7 7h-.5a6 6 0 0 1-5-2.7L3.3 15a1.5 1.5 0 0 1 2.4-1.8L9 16V8.5a1.5 1.5 0 0 1 3 0" />
  </Svg>
);
export const IRule = (p: P) => (
  <Svg {...p}>
    <path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0" />
    <circle cx="16" cy="6" r="2" />
    <circle cx="10" cy="12" r="2" />
    <circle cx="18" cy="18" r="2" />
  </Svg>
);
export const IServer = (p: P) => (
  <Svg {...p}>
    <rect x="3" y="4" width="18" height="7" rx="2" />
    <rect x="3" y="13" width="18" height="7" rx="2" />
    <path d="M7 7.5h.01M7 16.5h.01" />
  </Svg>
);
export const IWarn = (p: P) => (
  <Svg sw={2.2} {...p}>
    <path d="M12 9v4M12 17h.01" />
    <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
  </Svg>
);
export const IInfo = (p: P) => (
  <Svg sw={2} {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 8v4M12 16h.01" />
  </Svg>
);
export const IMore = ({ size = 18 }: P) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
    <circle cx="5" cy="12" r="1.8" />
    <circle cx="12" cy="12" r="1.8" />
    <circle cx="19" cy="12" r="1.8" />
  </svg>
);

/** Same drawing as ILeave — used for the door exit button. */
export const IExitDoor = ILeave;

// ---------- extra device icons (same stroke style) ----------
export const ITv = (p: P) => (
  <Svg sw={1.9} {...p}>
    <rect x="3" y="5" width="18" height="12" rx="2" />
    <path d="M8 21h8M12 17v4" />
  </Svg>
);
export const IFridge = (p: P) => (
  <Svg sw={1.9} {...p}>
    <rect x="5" y="2.5" width="14" height="19" rx="2.5" />
    <path d="M5 10h14M9 6v2M9 13v3" />
  </Svg>
);
export const IAc = (p: P) => (
  <Svg sw={1.9} {...p}>
    <path d="M12 2v20M4.9 6l14.2 12M19.1 6 4.9 18" />
    <path d="m9.5 3.5 2.5 2 2.5-2M9.5 20.5l2.5-2 2.5 2" />
  </Svg>
);
export const IHeater = (p: P) => (
  <Svg sw={1.9} {...p}>
    <path d="M12 2.5c3 3.2 5.5 6.3 5.5 10a5.5 5.5 0 0 1-11 0c0-2.2 1-3.9 2.3-5.4.4 1.6 1.2 2.6 2.4 3.1C11 7.6 11.2 5 12 2.5z" />
  </Svg>
);
export const IPump = (p: P) => (
  <Svg sw={1.9} {...p}>
    <path d="M12 3s6 6.4 6 11a6 6 0 0 1-12 0c0-4.6 6-11 6-11z" />
  </Svg>
);
export const IPlug = (p: P) => (
  <Svg sw={1.9} {...p}>
    <path d="M9 2v5M15 2v5M6 7h12v4a6 6 0 0 1-12 0zM12 17v5" />
  </Svg>
);
export const IGeneric = (p: P) => (
  <Svg sw={1.9} {...p}>
    <path d="M12 3v8" />
    <path d="M6.3 6.8a8 8 0 1 0 11.4 0" />
  </Svg>
);
export const IPlus = (p: P) => (
  <Svg sw={2} {...p}>
    <path d="M12 5v14M5 12h14" />
  </Svg>
);
