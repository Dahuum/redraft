// Per-account annex layout templates, stored in the Supabase `annex_templates`
// table (RLS: a user only ever sees/writes their own rows). Everything degrades
// gracefully: when Supabase isn't configured or no one is signed in, the
// functions return null / false so the caller falls back to its localStorage
// cache. That keeps local dev and offline use working unchanged.

import { supabase, hasSupabase } from "./supabase.js";

async function currentUserId() {
  if (!hasSupabase) return null;
  try {
    const { data } = await supabase.auth.getUser();
    return data?.user?.id ?? null;
  } catch {
    return null;
  }
}

// Fetch a saved layout template by annex name. RLS scopes this to the caller's
// own rows, so no user_id filter is needed. Returns the template JSON or null.
export async function getTemplate(name) {
  if (!hasSupabase || !name) return null;
  try {
    const { data, error } = await supabase
      .from("annex_templates")
      .select("template")
      .eq("name", name)
      .maybeSingle();
    if (error) return null;
    return data?.template ?? null;
  } catch {
    return null;
  }
}

// Upsert a layout template for the signed-in user (one row per name). Best
// effort — returns true on success, false if it couldn't be saved.
export async function saveTemplate(name, template) {
  if (!hasSupabase || !name || !template) return false;
  try {
    const uid = await currentUserId();
    if (!uid) return false;
    const { error } = await supabase.from("annex_templates").upsert(
      { user_id: uid, name, template, updated_at: new Date().toISOString() },
      { onConflict: "user_id,name" },
    );
    return !error;
  } catch {
    return false;
  }
}
