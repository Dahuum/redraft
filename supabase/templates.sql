-- Redraft — cloud-saved templates + signatures. Run in Supabase → SQL Editor.
-- Two tables (projects = a saved template + its setup; signatures) and a private
-- Storage bucket for the template PDFs. All RLS-locked to the owner. A trigger
-- enforces the per-plan cap (free = 3, pro = unlimited) so it can't be bypassed
-- from the client. Depends on public.profiles (from profiles.sql) for the plan.

-- ── per-plan cap helper ──
create or replace function public.plan_limit(uid uuid, free_lim int)
returns int language plpgsql stable security definer set search_path = public as $$
declare pl text;
begin
  select plan into pl from public.profiles where id = uid;
  return case when pl = 'pro' then 1000000 else free_lim end;
end; $$;

-- ════════════ projects (a saved template + its field setup) ════════════
create table if not exists public.projects (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
  name        text not null,
  kind        text not null default 'bulk',          -- 'bulk' | 'editor' | 'annex'
  setup       jsonb not null default '{}'::jsonb,     -- picked fields, splits, mapping…
  storage_path text,                                  -- templates/{uid}/{id}.pdf
  pages       int,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

alter table public.projects enable row level security;
drop policy if exists "projects: select own" on public.projects;
drop policy if exists "projects: insert own" on public.projects;
drop policy if exists "projects: update own" on public.projects;
drop policy if exists "projects: delete own" on public.projects;
create policy "projects: select own" on public.projects for select using (auth.uid() = user_id);
create policy "projects: insert own" on public.projects for insert with check (auth.uid() = user_id);
create policy "projects: update own" on public.projects for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "projects: delete own" on public.projects for delete using (auth.uid() = user_id);

create or replace function public.enforce_project_limit()
returns trigger language plpgsql security definer set search_path = public as $$
declare cnt int; lim int;
begin
  lim := public.plan_limit(new.user_id, 3);
  select count(*) into cnt from public.projects where user_id = new.user_id;
  if cnt >= lim then
    raise exception 'You have reached your saved-template limit (%). Upgrade to Pro for more.', lim;
  end if;
  return new;
end; $$;
drop trigger if exists projects_limit on public.projects;
create trigger projects_limit before insert on public.projects
  for each row execute function public.enforce_project_limit();

-- ════════════ signatures (draw/type, synced to the account) ════════════
create table if not exists public.signatures (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null default auth.uid() references auth.users (id) on delete cascade,
  data_url   text not null,                           -- transparent PNG data-URL
  ratio      real not null default 3,
  created_at timestamptz not null default now()
);

alter table public.signatures enable row level security;
drop policy if exists "signatures: select own" on public.signatures;
drop policy if exists "signatures: insert own" on public.signatures;
drop policy if exists "signatures: delete own" on public.signatures;
create policy "signatures: select own" on public.signatures for select using (auth.uid() = user_id);
create policy "signatures: insert own" on public.signatures for insert with check (auth.uid() = user_id);
create policy "signatures: delete own" on public.signatures for delete using (auth.uid() = user_id);

create or replace function public.enforce_signature_limit()
returns trigger language plpgsql security definer set search_path = public as $$
declare cnt int; lim int;
begin
  lim := public.plan_limit(new.user_id, 3);
  select count(*) into cnt from public.signatures where user_id = new.user_id;
  if cnt >= lim then
    raise exception 'You have reached your saved-signature limit (%). Upgrade to Pro for more.', lim;
  end if;
  return new;
end; $$;
drop trigger if exists signatures_limit on public.signatures;
create trigger signatures_limit before insert on public.signatures
  for each row execute function public.enforce_signature_limit();

-- ════════════ Storage bucket for template PDFs (private, 5MB/file) ════════════
insert into storage.buckets (id, name, public, file_size_limit)
values ('templates', 'templates', false, 5242880)
on conflict (id) do update set file_size_limit = 5242880;

-- Each user can only touch files under their own folder: templates/{uid}/...
drop policy if exists "templates: read own"   on storage.objects;
drop policy if exists "templates: insert own" on storage.objects;
drop policy if exists "templates: update own" on storage.objects;
drop policy if exists "templates: delete own" on storage.objects;
create policy "templates: read own"   on storage.objects for select using (bucket_id = 'templates' and (storage.foldername(name))[1] = auth.uid()::text);
create policy "templates: insert own" on storage.objects for insert with check (bucket_id = 'templates' and (storage.foldername(name))[1] = auth.uid()::text);
create policy "templates: update own" on storage.objects for update using (bucket_id = 'templates' and (storage.foldername(name))[1] = auth.uid()::text);
create policy "templates: delete own" on storage.objects for delete using (bucket_id = 'templates' and (storage.foldername(name))[1] = auth.uid()::text);
