import { useEffect, useState } from "react";

export interface Provider {
  name: string;
  label: string;
}

export type Providers = Provider[] | "unknown" | "failed";

let asked: Promise<Provider[] | "failed"> | null = null;

// The sign in providers this server offers, asked once per page load. "unknown" until the answer, as the page was
// prerendered; "failed" when the server could not say.
export function useProviders(): Providers {
  const [providers, setProviders] = useState<Providers>("unknown");
  useEffect(() => {
    asked ??= fetch("/api/auth/providers", { credentials: "same-origin" })
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(String(res.status)))))
      .then((data: { providers?: Provider[] }) => (Array.isArray(data?.providers) ? data.providers : []))
      .catch(() => "failed" as const);
    let gone = false;
    void asked.then((p) => {
      if (!gone) setProviders(p);
    });
    return () => {
      gone = true;
    };
  }, []);
  return providers;
}

// No way to sign in on this server, or it could not say: sign up buttons offer the demo instead of a dead end.
export function noSignIn(providers: Providers): boolean {
  return providers === "failed" || (Array.isArray(providers) && providers.length === 0);
}

type Visitor = "unknown" | "signed_out" | "member" | "demo";

let visitor: Promise<Visitor> | null = null;

// Who is looking at a public page: signed out, a member signed in to their society, or a demo visitor. Until the
// answer the page renders as for a visitor signed out, as it was prerendered.
export function useVisitor(): Visitor {
  const [who, setWho] = useState<Visitor>("unknown");
  useEffect(() => {
    visitor ??= fetch("/api/demo/visitor", { credentials: "same-origin" })
      .then(async (res) => {
        const kind = res.ok ? (await res.json().catch(() => null))?.kind : null;
        if (kind === "member" || kind === "seller") return "member";
        return kind === "demo" ? "demo" : "signed_out";
      })
      .catch(() => "signed_out" as const);
    let gone = false;
    void visitor.then((v) => {
      if (!gone) setWho(v);
    });
    return () => {
      gone = true;
    };
  }, []);
  return who;
}
