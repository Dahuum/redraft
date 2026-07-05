// Cloud-saved templates ("projects") + signatures, in Supabase.
// RLS scopes every row/file to the owner; a DB trigger enforces the free cap
// (3), so the limit can't be bypassed here. All functions degrade to null/[]
// when Supabase isn't configured or nobody's signed in, so local dev is safe.

import { supabase, hasSupabase } from "./supabase.js";

export const cloudEnabled = hasSupabase;

async function uid() {
  if (!hasSupabase) return null;
  try {
    const { data } = await supabase.auth.getUser();
    return data?.user?.id ?? null;
  } catch {
    return null;
  }
}

// ---- Projects (a saved template PDF + its field setup) ----
export async function listProjects() {
  if (!hasSupabase) return [];
  const { data, error } = await supabase
    .from("projects")
    .select("id,name,kind,setup,storage_path,pages,updated_at")
    .order("updated_at", { ascending: false });
  return error ? [] : data || [];
}

// Saves the template PDF to Storage + a row with its setup. Throws with the
// friendly limit message when the free cap is hit (surfaced by the DB trigger).
export async function saveProject(file, { name, kind = "bulk", setup = {}, pages = 1 }) {
  if (!hasSupabase) throw new Error("Cloud save isn't available.");
  const owner = await uid();
  if (!owner) throw new Error("Please sign in to save to your account.");

  // Insert first → the trigger checks the plan limit before we upload anything.
  const { data: row, error: insErr } = await supabase
    .from("projects")
    .insert({ name, kind, setup, pages })
    .select()
    .single();
  if (insErr) throw new Error(insErr.message || "Couldn't save.");

  const path = `${owner}/${row.id}.pdf`;
  const { error: upErr } = await supabase.storage
    .from("templates")
    .upload(path, file, { upsert: true, contentType: "application/pdf" });
  if (upErr) {
    await supabase.from("projects").delete().eq("id", row.id); // no orphan row
    throw new Error(upErr.message || "Couldn't upload the template.");
  }
  await supabase.from("projects").update({ storage_path: path }).eq("id", row.id);
  return { ...row, storage_path: path };
}

// Downloads a saved template's PDF as a File.
export async function openProjectFile(project) {
  if (!hasSupabase || !project?.storage_path) throw new Error("Template file missing.");
  const { data, error } = await supabase.storage.from("templates").download(project.storage_path);
  if (error) throw new Error(error.message || "Couldn't open the template.");
  return new File([data], `${project.name || "template"}.pdf`, { type: "application/pdf" });
}

export async function deleteProject(project) {
  if (!hasSupabase) return;
  if (project.storage_path) {
    try {
      await supabase.storage.from("templates").remove([project.storage_path]);
    } catch {
      /* best effort */
    }
  }
  await supabase.from("projects").delete().eq("id", project.id);
}

// ---- Signatures (synced to the account) ----
// Returns null on failure (e.g. table not created yet) so the caller can keep
// its local list instead of wiping it; [] means "signed in, none saved".
export async function listSignatures() {
  if (!hasSupabase) return null;
  const { data, error } = await supabase
    .from("signatures")
    .select("id,data_url,ratio,created_at")
    .order("created_at", { ascending: false });
  if (error) return null;
  return (data || []).map((s) => ({ id: s.id, url: s.data_url, ratio: s.ratio }));
}

export async function saveSignature({ url, ratio = 3 }) {
  if (!hasSupabase) throw new Error("Cloud save isn't available.");
  const { data, error } = await supabase
    .from("signatures")
    .insert({ data_url: url, ratio })
    .select()
    .single();
  if (error) throw new Error(error.message || "Couldn't save the signature.");
  return { id: data.id, url: data.data_url, ratio: data.ratio };
}

export async function deleteSignature(id) {
  if (!hasSupabase) return;
  await supabase.from("signatures").delete().eq("id", id);
}
