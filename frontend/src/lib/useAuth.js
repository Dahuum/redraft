// useAuth — Supabase session state (UI-agnostic). Gates the app on login.
import { useEffect, useState } from "react";
import { supabase, hasSupabase } from "./supabase.js";

export function useAuth() {
  const [session, setSession] = useState(null);
  // If Supabase isn't configured, treat as "ready, no auth" so the app still runs.
  const [ready, setReady] = useState(!hasSupabase);

  useEffect(() => {
    if (!hasSupabase) return;
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setReady(true);
    });
    const { data: sub } = supabase.auth.onAuthStateChange((_evt, s) =>
      setSession(s),
    );
    return () => sub.subscription.unsubscribe();
  }, []);

  return {
    enabled: hasSupabase,
    ready,
    session,
    user: session?.user ?? null,
    signOut: () => supabase?.auth.signOut(),
  };
}
