-- v13 migration for persistent project/verification state
alter table public.points
  add column if not exists human_verified_at timestamptz,
  add column if not exists human_updated_at timestamptz;

alter table public.projects
  add column if not exists status text default 'Ready',
  add column if not exists updated_at timestamptz default now();

create index if not exists idx_points_project_human_result
  on public.points(project_id, human_result);

create table if not exists public.point_review_history (
  id uuid primary key default gen_random_uuid(),
  point_id uuid not null references public.points(id) on delete cascade,
  previous_human_result text,
  new_human_result text not null,
  reviewer_note text,
  created_at timestamptz default now()
);

create index if not exists idx_point_review_history_point
  on public.point_review_history(point_id, created_at desc);
