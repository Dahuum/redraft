-- Redraft — plans + usage metering. Run in Supabase → SQL Editor.
-- One profile row per user holding their plan and monthly document usage.
-- The BACKEND (service_role, which bypasses RLS) is the only writer; clients
-- may read their own row to display usage. A signup trigger auto-creates rows.

create table if not exists public.profiles (
  id         uuid primary key references auth.users (id) on delete cascade,
  plan       text not null default 'free',        -- 'free' | 'pro'
  used       int  not null default 0,             -- documents this period
  period     text not null default to_char(now(), 'YYYY-MM'),
  updated_at timestamptz not null default now()
);

alter table public.profiles enable row level security;

-- Read-only for the owner. NO insert/update/delete policies on purpose → a user
-- can never change their own plan or reset their usage; only the backend's
-- service_role (which bypasses RLS) writes here.
drop policy if exists "profiles: select own" on public.profiles;
create policy "profiles: select own" on public.profiles
  for select using (auth.uid() = id);

-- Auto-create a profile whenever a new user signs up.
create or replace function public.handle_new_user()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id) values (new.id) on conflict (id) do nothing;
  return new;
end; $$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users for each row execute function public.handle_new_user();

-- Backfill anyone who signed up before this table existed.
insert into public.profiles (id)
  select id from auth.users on conflict (id) do nothing;

-- To upgrade someone to Pro (after they pay via Wise/etc.), run:
--   update public.profiles set plan = 'pro' where id = '<their-user-uuid>';
-- Find the uuid in Supabase → Authentication → Users.
