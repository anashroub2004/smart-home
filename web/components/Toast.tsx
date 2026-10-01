"use client";

import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";
import { IInfo } from "./Icons";

const Ctx = createContext<(msg: string) => void>(() => {});

/** Bottom toast from the prototype — used when a device doesn't respond. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [msg, setMsg] = useState("");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const show = useCallback((m: string) => {
    setMsg(m);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setMsg(""), 3500);
  }, []);
  return (
    <Ctx.Provider value={show}>
      {children}
      {msg && (
        <div className="toast" role="status">
          <IInfo size={18} />
          <span style={{ flex: 1 }}>{msg}</span>
        </div>
      )}
    </Ctx.Provider>
  );
}

export const useToast = () => useContext(Ctx);
