"use client";

import { RequireAuth } from "@/lib/auth";
import { AlertOverlay } from "@/components/AlertOverlay";
import { DeviceSheetProvider } from "@/components/DeviceSheet";
import { Nav } from "@/components/Nav";
import { ToastProvider } from "@/components/Toast";

// Same structure as the prototype: .app = nav (bottom on phones, left on desktop) + scrolling content.
export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <RequireAuth>
      <ToastProvider>
        <DeviceSheetProvider>
          <div className="app">
            <div className="scroll">
              <div className="wrap">{children}</div>
            </div>
            {/* after the content so it sits at the bottom on phones; CSS moves it to the left on desktop */}
            <Nav />
          </div>
          <AlertOverlay />
        </DeviceSheetProvider>
      </ToastProvider>
    </RequireAuth>
  );
}
