"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { IHouse } from "@/components/Icons";
import { useAuth } from "@/lib/auth";
import { USE_EMULATOR } from "@/lib/firebase";

export default function LoginPage() {
  const { user, signIn } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState(USE_EMULATOR ? "owner@home.test" : "");
  const [password, setPassword] = useState(USE_EMULATOR ? "password123" : "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (user) router.replace("/");
  }, [user, router]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(email, password);
    } catch {
      setError("Wrong email or password.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app">
      <div className="scroll">
        <div className="wrap">
          <form onSubmit={submit} style={{ minHeight: "76vh", display: "flex", flexDirection: "column", justifyContent: "center", gap: 22, maxWidth: 400, width: "100%", margin: "0 auto" }}>
            <div style={{ width: 56, height: 56, borderRadius: 18, background: "#ECEEF1", color: "#141518", display: "flex", alignItems: "center", justifyContent: "center" }}>
              <IHouse size={28} sw={2} />
            </div>
            <div>
              <h1 style={{ margin: 0, fontSize: 28, fontWeight: 700 }}>Welcome home</h1>
              <p style={{ margin: "6px 0 0", color: "#9BA1AA", fontSize: 15 }}>Sign in with the owner account to control your studio.</p>
            </div>
            <label className="field">
              Email
              <input type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />
            </label>
            <label className="field">
              Password
              <input type="password" required autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
            </label>
            {error && <p style={{ margin: 0, color: "#FF8A84", fontSize: 14 }}>{error}</p>}
            <button type="submit" className="btn btn-light btn-block" disabled={busy}>
              {busy && <span className="spin" />}
              {busy ? "Signing in…" : "Sign in"}
            </button>
            <p style={{ margin: 0, fontSize: 12.5, color: "#9BA1AA", textAlign: "center" }}>
              {USE_EMULATOR ? "Local emulator · owner@home.test / password123" : "Secured with Firebase Authentication"}
            </p>
          </form>
        </div>
      </div>
    </div>
  );
}
