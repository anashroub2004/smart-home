import Link from "next/link";

export default function NotFound() {
  return (
    <div className="grid min-h-dvh place-items-center p-6 text-center">
      <div>
        <p className="text-muted">Page not found</p>
        <Link href="/" className="mt-3 inline-block text-accent">Back home</Link>
      </div>
    </div>
  );
}
