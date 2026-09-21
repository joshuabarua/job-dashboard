-- Lock down what the publishable (anon) key can do now that it ships in
-- public/index.html. Run in the Supabase SQL editor.
--
-- Result: anyone on the internet can read jobs and change `status` or delete
-- a row (the dashboard needs that), but cannot insert rows or rewrite other
-- columns. The service key used by Actions/the local app bypasses RLS and is
-- unaffected.

alter table jobs enable row level security;

create policy "anon can read jobs"
  on jobs for select to anon
  using (true);

create policy "anon can update status"
  on jobs for update to anon
  using (true)
  with check (true);

create policy "anon can delete jobs"
  on jobs for delete to anon
  using (true);

revoke update on jobs from anon;
grant update (status) on jobs to anon;
