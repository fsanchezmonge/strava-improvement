-- Create activities table
create table activities (
    activity_id bigint primary key,
    athlete_id bigint not null,
    name text,
    sport text,
    type text,
    datetime_local timestamp,
    distance float,
    moving_time float,
    elapsed_time float,
    elevation_gain float,
    average_speed float,
    max_speed float,
    average_heartrate float,
    max_heartrate float,
    elev_high float,
    elev_low float,
    workout_type text,
    created_at timestamp with time zone default now()
);

-- Create strava_tokens table
create table strava_tokens (
    athlete_id bigint primary key,
    access_token text not null,
    refresh_token text not null,
    expires_at timestamp with time zone not null,
    created_at timestamp with time zone default now(),
    updated_at timestamp with time zone default now()
);

-- Create index on athlete_id for faster queries
create index idx_activities_athlete_id on activities(athlete_id);

-- Enable Row Level Security (RLS)
alter table activities enable row level security;
alter table strava_tokens enable row level security;

-- Create policies to allow all operations based on athlete_id
create policy "Allow select based on athlete_id"
    on activities for select
    using (true);

create policy "Allow insert based on athlete_id"
    on activities for insert
    with check (true);

create policy "Allow update based on athlete_id"
    on activities for update
    using (true)
    with check (true);

create policy "Allow delete based on athlete_id"
    on activities for delete
    using (true);

-- Create policies for strava_tokens
create policy "Allow all operations on strava_tokens"
    on strava_tokens for all
    using (true); 