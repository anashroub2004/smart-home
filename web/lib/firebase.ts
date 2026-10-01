"use client";

import { getApp, getApps, initializeApp, type FirebaseApp } from "firebase/app";
import { connectAuthEmulator, getAuth, type Auth } from "firebase/auth";
import { connectDatabaseEmulator, getDatabase, type Database } from "firebase/database";

export const USE_EMULATOR = process.env.NEXT_PUBLIC_USE_EMULATOR === "1";

const projectId = process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID || "demo-smart-home";

const firebaseConfig = {
  apiKey: process.env.NEXT_PUBLIC_FIREBASE_API_KEY || "demo-key",
  authDomain: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN || `${projectId}.firebaseapp.com`,
  // The emulator namespace is taken from this URL: "demo-smart-home-default-rtdb"
  databaseURL:
    process.env.NEXT_PUBLIC_FIREBASE_DATABASE_URL ||
    `https://${projectId}-default-rtdb.firebaseio.com`,
  projectId,
  appId: process.env.NEXT_PUBLIC_FIREBASE_APP_ID || undefined,
};

let app: FirebaseApp | undefined;
let auth: Auth | undefined;
let db: Database | undefined;

// Lazy so nothing runs during the static build (pages are pre-rendered in Node).
function ensure() {
  if (app) return;
  app = getApps().length ? getApp() : initializeApp(firebaseConfig);
  auth = getAuth(app);
  db = getDatabase(app);
  if (USE_EMULATOR) {
    const g = globalThis as { __fbEmu?: boolean };
    if (!g.__fbEmu) {
      connectAuthEmulator(auth, "http://127.0.0.1:9099", { disableWarnings: true });
      connectDatabaseEmulator(db, "127.0.0.1", 9000);
      g.__fbEmu = true;
    }
  }
}

export function fbAuth(): Auth {
  ensure();
  return auth!;
}

export function fbDb(): Database {
  ensure();
  return db!;
}
