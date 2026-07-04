// Supabase client — auth + per-account data (templates, history).
// URL + anon key come from env (VITE_SUPABASE_*). The anon key is public by
// design (it ships in the browser bundle); Row-Level Security protects the data.
// NEVER put the service_role / secret key here.
import { createClient } from "@supabase/supabase-js";

const url = import.meta.env.VITE_SUPABASE_URL;
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY;

export const hasSupabase = Boolean(url && anonKey);

export const supabase = hasSupabase
  ? createClient(url, anonKey, {
      auth: { persistSession: true, autoRefreshToken: true },
    })
  : null;
