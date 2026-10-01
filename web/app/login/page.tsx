"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { Home } from "lucide-react";
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
    <main className="mx-auto flex min-h-dvh max-w-sm flex-col justify-center px-6">
      <div className="mb-10 grid h-14 w-14 place-items-center rounded-2xl bg-surface">
        <Home className="text-accent" size={28} />
      </div>
      <h1 className="text-3xl font-bold">Welcome home</h1>
      <p className="mt-2 text-muted">Sign in to control your home.</p>

      <form onSubmit={submit} className="mt-8 space-y-3">
        <input
          type="email" required autoComplete="email" placeholder="Email"
          value={email} onChange={(e) => setEmail(e.target.value)}
          className="w-full rounded-2xl border border-border bg-tile px-4 py-3.5 outline-none focus:border-accent"
        />
        <input
          type="password" required autoComplete="current-password" placeholder="Password"
          value={password} onChange={(e) => setPassword(e.target.value)}
          className="w-full rounded-2xl border border-border bg-tile px-4 py-3.5 outline-none focus:border-accent"
        />
        {error && <p className="text-sm text-bad">{error}</p>}
        <button
          disabled={busy}
          className="w-full rounded-2xl bg-text py-3.5 font-semibold text-bg disabled:opacity-60"
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>

      {USE_EMULATOR && (
        <p className="mt-6 text-xs text-muted">
          Emulator mode — the simulator creates owner@home.test / password123.
        </p>
      )}
    </main>
  );
}
