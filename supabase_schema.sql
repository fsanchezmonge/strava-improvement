-- Create strava_tokens table
create table strava_tokens (
    athlete_id bigint primary key,
    access_token text not null,
    refresh_token text not null,
    expires_at timestamp with time zone not null,
    created_at timestamp with time zone default now(),
    updated_at timestamp with time zone default now()
);

-- Enable Row Level Security (RLS)
alter table strava_tokens enable row level security;

-- Create policies for strava_tokens
create policy "Allow all operations on strava_tokens"
    on strava_tokens for all
    using (true); 