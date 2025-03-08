# Strava Activity Tracker

A Streamlit application that connects to your Strava account and displays your activities in a clean, organized dashboard. The app uses Supabase for data storage.

## Setup Instructions

1. **Create a Strava API Application**
   - Go to https://www.strava.com/settings/api
   - Create an API application
   - Note down your Client ID and Client Secret

2. **Set up Supabase**
   - Create a new project at https://supabase.com
   - Get your project URL and anon key
   - Run the SQL commands from `supabase_schema.sql` in the Supabase SQL editor

3. **Configure Environment Variables**
   - Copy `.env.example` to `.env`
   - Fill in your credentials:
     ```
     STRAVA_CLIENT_ID=your_client_id
     STRAVA_CLIENT_SECRET=your_client_secret
     SUPABASE_URL=your_supabase_url
     SUPABASE_KEY=your_supabase_anon_key
     ```

4. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

5. **Run the Application**
   ```bash
   streamlit run app.py
   ```

## Features

- OAuth2 authentication with Strava
- Fetches all your Strava activities
- Displays activities in a clean, sortable table
- Stores activities in Supabase for faster subsequent loads
- Converts units to more readable formats (km, km/h, etc.)
- Shows key metrics like distance, time, speed, and elevation gain

## Usage

1. Click the "Connect with Strava" button
2. Authorize the application
3. Click "Fetch Activities" to load your activities
4. Your activities will be displayed in a table and stored in Supabase

## Data Storage

Activities are stored in Supabase with the following structure:
- Primary key: Activity ID from Strava
- Athlete ID for data segregation
- Basic activity metrics (distance, time, speed, etc.)
- Timestamps for activity start
- Additional metrics (heart rate, elevation, etc.) 