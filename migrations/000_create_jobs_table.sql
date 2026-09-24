create table if not exists jobs (
  id uuid primary key default gen_random_uuid(),
  notion_id text default '',
  job_title text default '',
  company text default '',
  location text default '',
  url text default '',
  track text default '',
  status text default 'New',
  date text default '',
  match_score text default '',
  recommended_cv text default '',
  why_fit text default '',
  application_strategy text default '',
  tags text default '',
  created_at timestamptz default now()
);
