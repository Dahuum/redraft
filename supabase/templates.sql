create or replace function public.plan_limit(uid uuid, free_lim int)
returns int language plpgsql stable security definer set search_path = public as $$
declare pl text;
begin
  select plan into pl from public.profiles where id = uid;
  return case when pl = 'pro' then 1000000 else free_lim end;
end;
$$;

create table if not exists public.projects (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
  name text not null,
  kind text not null default 'bulk',
  setup jsonb not null default '{}'::jsonb,
  storage_path text,
  pages int,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
alter table public.projects enable row level security;
drop policy if exists proj_sel on public.projects;
drop policy if exists proj_ins on public.projects;
drop policy if exists proj_upd on public.projects;
drop policy if exists proj_del on public.projects;
create policy proj_sel on public.projects for select using (auth.uid() = user_id);
create policy proj_ins on public.projects for insert with check (auth.uid() = user_id);
create policy proj_upd on public.projects for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy proj_del on public.projects for delete using (auth.uid() = user_id);

create or replace function public.enforce_project_limit()
returns trigger language plpgsql security definer set search_path = public as $$
declare cnt int; lim int;
begin
  lim := public.plan_limit(new.user_id, 3);
  select count(*) into cnt from public.projects where user_id = new.user_id;
  if cnt >= lim then
    raise exception 'Saved-template limit reached (%). Upgrade to Pro for more.', lim;
  end if;
  return new;
end;
$$;
drop trigger if exists projects_limit on public.projects;
create trigger projects_limit before insert on public.projects
  for each row execute function public.enforce_project_limit();

create table if not exists public.signatures (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
  data_url text not null,
  ratio real not null default 3,
  created_at timestamptz not null default now()
);
alter table public.signatures enable row level security;
drop policy if exists sig_sel on public.signatures;
drop policy if exists sig_ins on public.signatures;
drop policy if exists sig_del on public.signatures;
create policy sig_sel on public.signatures for select using (auth.uid() = user_id);
create policy sig_ins on public.signatures for insert with check (auth.uid() = user_id);
create policy sig_del on public.signatures for delete using (auth.uid() = user_id);

create or replace function public.enforce_signature_limit()
returns trigger language plpgsql security definer set search_path = public as $$
declare cnt int; lim int;
begin
  lim := public.plan_limit(new.user_id, 3);
  select count(*) into cnt from public.signatures where user_id = new.user_id;
  if cnt >= lim then
    raise exception 'Saved-signature limit reached (%). Upgrade to Pro for more.', lim;
  end if;
  return new;
end;
$$;
drop trigger if exists signatures_limit on public.signatures;
create trigger signatures_limit before insert on public.signatures
  for each row execute function public.enforce_signature_limit();

insert into storage.buckets (id, name, public, file_size_limit)
values ('templates', 'templates', false, 5242880)
on conflict (id) do update set file_size_limit = 5242880;
drop policy if exists tmpl_read on storage.objects;
drop policy if exists tmpl_ins on storage.objects;
drop policy if exists tmpl_upd on storage.objects;
drop policy if exists tmpl_del on storage.objects;
create policy tmpl_read on storage.objects for select using (bucket_id = 'templates' and (storage.foldername(name))[1] = auth.uid()::text);
create policy tmpl_ins on storage.objects for insert with check (bucket_id = 'templates' and (storage.foldername(name))[1] = auth.uid()::text);
create policy tmpl_upd on storage.objects for update using (bucket_id = 'templates' and (storage.foldername(name))[1] = auth.uid()::text);
create policy tmpl_del on storage.objects for delete using (bucket_id = 'templates' and (storage.foldername(name))[1] = auth.uid()::text);
