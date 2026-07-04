-- Redraft — Supabase schema. Run in Supabase → SQL Editor.
-- First RLS-protected table: per-account annex templates (scan-once, synced
-- across devices; replaces the browser-local localStorage template).

create table if not exists public.annex_templates (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null default auth.uid() references auth.users (id) on delete cascade,
  name       text not null,          -- annex file name / friendly label
  template   jsonb not null,         -- the layout template detect_template() produced
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- One saved template per (user, name).
create unique index if not exists annex_templates_user_name_idx
  on public.annex_templates (user_id, name);

-- Row-Level Security: a user can only ever touch their own rows. Enforced by
-- Postgres, so no authorization code is needed in the app/backend.
alter table public.annex_templates enable row level security;

create policy "annex_templates: select own" on public.annex_templates
  for select using (auth.uid() = user_id);
create policy "annex_templates: insert own" on public.annex_templates
  for insert with check (auth.uid() = user_id);
create policy "annex_templates: update own" on public.annex_templates
  for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "annex_templates: delete own" on public.annex_templates
  for delete using (auth.uid() = user_id);
