-- Flashpoint: WUI Command — Supabase schema
-- Run this in the Supabase SQL Editor to provision tables, seed data, and RPCs.

-- ---------------------------------------------------------------------------
-- Extensions
-- ---------------------------------------------------------------------------
create extension if not exists "pgcrypto";

-- ---------------------------------------------------------------------------
-- Global game state (single-row table)
-- ---------------------------------------------------------------------------
create table if not exists public.global_state (
  id            int primary key default 1 check (id = 1),
  phase         text not null default 'lobby'
                check (phase in ('lobby', 'round_1', 'round_2', 'round_3', 'ended')),
  round_number  int  not null default 0 check (round_number between 0 and 3),
  weather_text  text not null default 'Calm conditions. Awaiting ignition…',
  updated_at    timestamptz not null default now()
);

insert into public.global_state (id, phase, round_number, weather_text)
values (1, 'lobby', 0, 'Calm conditions. Awaiting ignition…')
on conflict (id) do nothing;

-- ---------------------------------------------------------------------------
-- Grid tiles (7 columns × 8 rows = 56)
-- Columns A–G, rows 1–8. Tile IDs look like "C4".
-- ---------------------------------------------------------------------------
create table if not exists public.grid (
  tile_id     text primary key,
  col_idx     int  not null check (col_idx between 0 and 6),
  row_idx     int  not null check (row_idx between 0 and 7),
  col_label   text not null,
  row_label   int  not null,
  land_use    text not null
              check (land_use in (
                'Dense Forest',
                'Shrubland',
                'Suburban WUI',
                'Urban Center',
                'Reservoir'
              )),
  is_assigned boolean not null default false,
  unique (col_idx, row_idx)
);

-- ---------------------------------------------------------------------------
-- Players
-- ---------------------------------------------------------------------------
create table if not exists public.players (
  player_id       uuid primary key default gen_random_uuid(),
  tile_id         text unique references public.grid (tile_id),
  display_name    text,
  capital         numeric(12, 2) not null default 10000.00,
  trust           numeric(5, 2)  not null default 100.00
                  check (trust >= 0 and trust <= 100),
  current_action  text
                  check (
                    current_action is null
                    or current_action in ('do_nothing', 'mitigate_smoke', 'harden_evacuate')
                  ),
  action_round    int,          -- round the current_action was submitted for
  last_resolved   int not null default 0,  -- last round whose hazards were applied
  connected_at    timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

create index if not exists players_tile_id_idx on public.players (tile_id);
create index if not exists players_connected_at_idx on public.players (connected_at);

-- ---------------------------------------------------------------------------
-- Seed the 7×8 land-use map
-- Layout (west → east): Dense Forest | Shrubland | Suburban WUI | Urban | Reservoir pocket
-- ---------------------------------------------------------------------------
truncate public.players cascade;
truncate public.grid cascade;

with cols as (
  select * from (values
    (0, 'A'), (1, 'B'), (2, 'C'), (3, 'D'), (4, 'E'), (5, 'F'), (6, 'G')
  ) as t(col_idx, col_label)
),
rows as (
  select generate_series(0, 7) as row_idx
),
cells as (
  select
    c.col_idx,
    c.col_label,
    r.row_idx,
    r.row_idx + 1 as row_label,
    c.col_label || (r.row_idx + 1)::text as tile_id,
    case
      -- Reservoir pocket (blue)
      when c.col_label in ('F', 'G') and r.row_idx + 1 in (4, 5) then 'Reservoir'
      -- Urban Center (grey) — eastern core
      when c.col_label in ('F', 'G') then 'Urban Center'
      when c.col_label = 'E' and r.row_idx + 1 between 3 and 6 then 'Urban Center'
      -- Suburban WUI (orange) — mid-east fringe
      when c.col_label in ('D', 'E') then 'Suburban WUI'
      when c.col_label = 'C' and r.row_idx + 1 between 2 and 7 then 'Suburban WUI'
      -- Shrubland (light green) — transition belt
      when c.col_label in ('B', 'C') then 'Shrubland'
      -- Dense Forest (dark green) — western wildland
      else 'Dense Forest'
    end as land_use
  from cols c
  cross join rows r
)
insert into public.grid (tile_id, col_idx, row_idx, col_label, row_label, land_use, is_assigned)
select tile_id, col_idx, row_idx, col_label, row_label, land_use, false
from cells
order by row_idx, col_idx;

-- ---------------------------------------------------------------------------
-- Atomic tile assignment (safe under ~50 concurrent joins)
-- ---------------------------------------------------------------------------
create or replace function public.assign_random_tile(p_player_id uuid)
returns table (
  tile_id   text,
  land_use  text,
  capital   numeric,
  trust     numeric
)
language plpgsql
security definer
as $$
declare
  v_tile_id  text;
  v_land_use text;
begin
  -- Already assigned? Return existing row.
  if exists (select 1 from public.players pl where pl.player_id = p_player_id and pl.tile_id is not null) then
    return query
      select pl.tile_id, g.land_use, pl.capital, pl.trust
      from public.players pl
      join public.grid g on g.tile_id = pl.tile_id
      where pl.player_id = p_player_id;
    return;
  end if;

  select g.tile_id, g.land_use
    into v_tile_id, v_land_use
  from public.grid g
  where g.is_assigned = false
  order by random()
  for update skip locked
  limit 1;

  if v_tile_id is null then
    raise exception 'No available tiles remaining';
  end if;

  update public.grid
     set is_assigned = true
   where public.grid.tile_id = v_tile_id;

  insert into public.players (player_id, tile_id, capital, trust)
  values (p_player_id, v_tile_id, 10000.00, 100.00)
  on conflict (player_id) do update
    set tile_id = excluded.tile_id,
        updated_at = now();

  return query
    select v_tile_id, v_land_use, 10000.00::numeric, 100.00::numeric;
end;
$$;

-- ---------------------------------------------------------------------------
-- Reset game to lobby (moderator "new session" helper)
-- ---------------------------------------------------------------------------
create or replace function public.reset_game()
returns void
language plpgsql
security definer
as $$
begin
  -- Supabase/PostgREST-safe: always include a WHERE clause.
  delete from public.players
   where player_id is not null;

  update public.grid
     set is_assigned = false
   where tile_id is not null;

  update public.global_state
     set phase = 'lobby',
         round_number = 0,
         weather_text = 'Calm conditions. Awaiting ignition…',
         updated_at = now()
   where id = 1;
end;
$$;

-- ---------------------------------------------------------------------------
-- Row Level Security (open for conference demo; tighten for production)
-- ---------------------------------------------------------------------------
alter table public.global_state enable row level security;
alter table public.grid enable row level security;
alter table public.players enable row level security;

drop policy if exists "anon read global_state" on public.global_state;
drop policy if exists "anon write global_state" on public.global_state;
drop policy if exists "anon read grid" on public.grid;
drop policy if exists "anon write grid" on public.grid;
drop policy if exists "anon read players" on public.players;
drop policy if exists "anon write players" on public.players;

create policy "anon read global_state"  on public.global_state for select to anon, authenticated using (true);
create policy "anon write global_state" on public.global_state for all    to anon, authenticated using (true) with check (true);

create policy "anon read grid"  on public.grid for select to anon, authenticated using (true);
create policy "anon write grid" on public.grid for all    to anon, authenticated using (true) with check (true);

create policy "anon read players"  on public.players for select to anon, authenticated using (true);
create policy "anon write players" on public.players for all    to anon, authenticated using (true) with check (true);

grant usage on schema public to anon, authenticated;
grant select, insert, update, delete on public.global_state, public.grid, public.players to anon, authenticated;
grant execute on function public.assign_random_tile(uuid) to anon, authenticated;
grant execute on function public.reset_game() to anon, authenticated;
