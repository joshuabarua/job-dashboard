-- Add the missing tags column so jobs_tracker.csv can be imported
alter table jobs add column if not exists tags text;
