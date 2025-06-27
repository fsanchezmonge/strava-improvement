import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timezone
import os
from supabase import create_client, Client
import plotly.graph_objects as go
import time
from plotly.subplots import make_subplots
import pathlib
import uuid
from typing import Optional
import base64
import openai

st.set_page_config(
    page_title="Analitza el teu entrenament",
    page_icon=":running:",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Add custom CSS for background colors and fonts
st.markdown("""
    <style>
        .stApp {
            max-width: 100% !important;
            padding: 0 !important;
            background-color: #fcfcfc !important;
        }
        /* Hide sidebar */
        section[data-testid="stSidebar"] {
            display: None;
        }
        /* Hide the sidebar expander arrow */
        [data-testid="collapsedControl"] {
            display: none !important;
        }
        /* Adjust main content to use full width */
        section[data-testid="stSidebar"] + div {
            width: 100% !important;
        }
        h1 {
            font-family: 'Helvetica Neue', sans-serif;
            font-size: 40px;
            color: #222831;
            margin-bottom: 10px;
        }
        h2 {
            font-family: 'Helvetica Neue', sans-serif;
            font-size: 28px;
            color: #393E46;
            font-weight: bold;
            margin-bottom: 10px;
        }
        h3 {
            font-family: 'Helvetica Neue', sans-serif;
            font-size: 20px;
            color: #393E46;
            font-weight: normal;
            margin-bottom: 10px;
        }
        h4 {
            font-family: 'Helvetica Neue', sans-serif;
            font-size: 20px;
            color: #393E46;
            font-weight: bold;
            margin-top: 10px;
            margin-bottom: 10px;
        }
        h5 {
            font-family: 'Helvetica Neue', sans-serif;
            font-size: 16px;
            color: #393E46;
            font-weight: normal;
            margin-bottom:10px;
        }
        p {
            font-family: 'Helvetica Neue', sans-serif;
            font-size: 14px;
            color: #393E46;
            font-weight: normal;
            margin-bottom: 20px;
        }
    </style>
""", unsafe_allow_html=True)


# Initialize Supabase client
url: str = st.secrets.get("SUPABASE_URL")
key: str = st.secrets.get("SUPABASE_KEY")

current_dir = pathlib.Path(__file__).parent.parent.resolve()

if not url or not key:
    st.error("Missing Supabase credentials. Please check your environment variables or secrets.")
    st.stop()

supabase: Client = create_client(url, key)

# Strava API credentials
STRAVA_CLIENT_ID = st.secrets.get("STRAVA_CLIENT_ID", os.getenv("STRAVA_CLIENT_ID"))
STRAVA_CLIENT_SECRET = st.secrets.get("STRAVA_CLIENT_SECRET", os.getenv("STRAVA_CLIENT_SECRET"))

if not STRAVA_CLIENT_ID or not STRAVA_CLIENT_SECRET:
    st.error("Missing Strava API credentials. Please check your environment variables or secrets.")
    st.stop()

# OpenAI API configuration
OPENAI_API_KEY = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
if not OPENAI_API_KEY:
    st.error("Missing OpenAI API key. Please check your environment variables or secrets.")
    st.stop()

openai.api_key = OPENAI_API_KEY

# Update the REDIRECT_URI logic
if 'REDIRECT_URI' in st.secrets:
    REDIRECT_URI = st.secrets['REDIRECT_URI']
else:
    REDIRECT_URI = os.getenv("REDIRECT_URI", "http://localhost:8501")  # Local development fallback

def highlight_high_percentage(val):
    try:
        # Extract numeric value from percentage string (e.g., "35.5%" -> 35.5)
        numeric_val = float(val.replace('%', ''))
        return 'background-color: #ffcdd2' if numeric_val > 40 else ''
    except:
        return ''

def get_token(code):
    """Exchange authorization code for access token"""
    token_url = "https://www.strava.com/oauth/token"
    data = {
        'client_id': STRAVA_CLIENT_ID,
        'client_secret': STRAVA_CLIENT_SECRET,
        'code': code,
        'grant_type': 'authorization_code'
    }
    response = requests.post(token_url, data=data)
    return response.json()

def refresh_token(refresh_token):
    """Refresh the access token using the refresh token"""
    token_url = "https://www.strava.com/oauth/token"
    data = {
        'client_id': STRAVA_CLIENT_ID,
        'client_secret': STRAVA_CLIENT_SECRET,
        'refresh_token': refresh_token,
        'grant_type': 'refresh_token'
    }
    response = requests.post(token_url, data=data)
    return response.json()

def save_token_to_supabase(token_data):
    """Save or update token in Supabase"""
    try:
        token_record = {
            'athlete_id': token_data['athlete']['id'],
            'access_token': token_data['access_token'],
            'refresh_token': token_data['refresh_token'],
            'expires_at': datetime.fromtimestamp(token_data['expires_at'], tz=timezone.utc).isoformat(),
            'updated_at': datetime.now(timezone.utc).isoformat()
        }
        
        supabase.table('strava_tokens').upsert(
            token_record,
            on_conflict='athlete_id'
        ).execute()
        
        # Verify the token was saved
        stored_token = get_stored_token(token_record['athlete_id'])

    except Exception as e:
        st.error(f"Error saving token to Supabase: {str(e)}")
        raise

def get_stored_token(athlete_id):
    """Get stored token from Supabase"""
    if athlete_id is None:
        return None
        
    try:
        response = supabase.table('strava_tokens').select('*').eq('athlete_id', athlete_id).execute()
        if response.data:
            return response.data[0]
        return None
    except Exception as e:
        st.error(f"Error getting stored token: {str(e)}")
        return None

def ensure_fresh_token():
    """Ensure we have a valid token"""
    if 'athlete_id' not in st.session_state or st.session_state.athlete_id is None:
        return None
        
    stored_token = get_stored_token(st.session_state.athlete_id)
    if not stored_token:
        return None
        
    # Check if token is expired or about to expire (within 5 minutes)
    expires_at = datetime.fromisoformat(stored_token['expires_at'].replace('Z', '+00:00'))
    if expires_at <= datetime.now(timezone.utc):
        # Token is expired, refresh it
        try:
            new_token = refresh_token(stored_token['refresh_token'])
            new_token['athlete_id'] = stored_token['athlete_id']

            if 'access_token' in new_token:
                save_token_to_supabase(new_token)
                return new_token['access_token']
            return None
        except Exception as e:
            st.error(f"Error refreshing token: {str(e)}")
            return None
        
    return stored_token['access_token']

@st.cache_data(show_spinner="S'estan carregant les teves activitats...")
def get_activities(access_token):
    """Fetch athlete's activities from Strava"""
    activities_url = "https://www.strava.com/api/v3/athlete/activities"
    headers = {'Authorization': f'Bearer {access_token}'}
    activities = []
    page = 1
    
    # Initialize rate limiting parameters
    requests_in_window = 0
    window_start = datetime.now(timezone.utc)
    daily_requests = 0
    daily_start = window_start.replace(hour=0, minute=0, second=0, microsecond=0)
    
    while True:
        # Check rate limits
        current_time = datetime.now(timezone.utc)
        
        # Reset 15-minute window counter if needed
        if (current_time - window_start).total_seconds() > 900:  # 15 minutes
            requests_in_window = 0
            window_start = current_time
            
        # Reset daily counter if needed
        if current_time.date() > daily_start.date():
            daily_requests = 0
            daily_start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
            
        # Check if we're within limits
        if requests_in_window >= 100:
            wait_time = 900 - (current_time - window_start).total_seconds()
            st.warning(f"S'ha arribat al límit de peticions. Esperant {int(wait_time)} segons...")
            time.sleep(wait_time)
            requests_in_window = 0
            window_start = datetime.now(timezone.utc)
            
        if daily_requests >= 1000:
            st.error("S'ha arribat al límit diari de peticions. Torna-ho a provar demà.")
            break
            
        params = {'page': page, 'per_page': 200}
        try:
            response = requests.get(activities_url, headers=headers, params=params)
            requests_in_window += 1
            daily_requests += 1
            
            if response.status_code == 429:  # Rate limit exceeded
                retry_after = int(response.headers.get('Retry-After', 60))
                st.warning(f"S'ha arribat al límit de peticions. Esperant {retry_after} segons...")
                time.sleep(retry_after)
                continue
                
            if response.status_code != 200:
                st.error(f"Error en obtenir les activitats: {response.status_code}")
                break
                
            response_data = response.json()
            if not response_data:
                break
                
            activities.extend(response_data)
            page += 1
            
        except Exception as e:
            st.error(f"Error en connectar amb Strava: {str(e)}")
            break

    activity_data = []
    for activity in activities:
        activity_data.append({
            "athlete_id": activity["athlete"]["id"],
            "activity_id": activity["id"],
            "name": activity["name"],
            "sport": activity["type"],
            "type": activity["sport_type"],
            "datetime_local": activity["start_date_local"],
            "distance": activity["distance"]/1000,
            "moving_time": activity["moving_time"]/60,
            "elapsed_time": activity["elapsed_time"]/60,
            "elevation_gain": activity["total_elevation_gain"],
            "average_speed": activity["average_speed"] * 3.6,
            "max_speed": activity["max_speed"] * 3.6,
            "average_heartrate": activity.get("average_heartrate", None),
            "max_heartrate": activity.get("max_heartrate", None),
            "elev_high": activity.get("elev_high", None),
            "elev_low": activity.get("elev_low", None),
            "average_temp": activity.get("average_temp", None),
            "workout_type": activity.get("workout_type", None)
        })
    
    return activity_data

@st.cache_data(show_spinner="Guardant les activitats...")
def save_activities_to_supabase(activities, athlete_id):
    """Save activities to Supabase"""
    for activity in activities:
        activity['athlete_id'] = athlete_id
        activity['datetime_local'] = activity['datetime_local'].replace('Z', '')
        supabase.table('activities').upsert(
            activity,
            on_conflict='activity_id'
        ).execute()

def pace_to_speed(minutes, seconds=0):
    # Convert pace (min/km) to speed (km/h)
    total_minutes = minutes + seconds/60
    speed = 60 / total_minutes  # 60 minutes per hour
    return speed

def decimal_pace_to_str(decimal_pace: float) -> str:
    """
    Converts decimal pace (min/km as float) to a string format mm:ss min/km.
    
    Example:
    5.5 -> "5:30 min/km"
    4.25 -> "4:15 min/km"
    """
    minutes = int(decimal_pace)
    seconds = round((decimal_pace - minutes) * 60)
    return f"{minutes}:{seconds:02d} min/km"

# Label intensity
def label_intensity(index):
    if index <= 0.95:
        return "Alta"
    elif index <= 1.15:
        return "Moderada"
    else:
        return "Baixa"

def add_hr_intensity_index(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds an intensity labelto the DataFrame based on average heart rate.
    If average heart rate is below average heart rate of df -5% it is easy, if it is between -5% and 5% it is moderate, if it is above 5% it is hard.
    
    Parameters:
    - df: DataFrame with a 'average_heartrate' column.
    """
    average_hr = df['average_heartrate'].mean()
    df['hr_intensity'] = df['average_heartrate'].apply(lambda x: 'Easy' if x < average_hr * 0.95 else 'Moderate' if x < average_hr * 1.05 else 'Hard')
    return df   

def compute_easy_percentage(df):
    df = df.copy()
    total_sessions = len(df)
    
    if total_sessions == 0:
        return 0.0
    
    easy_sessions = len(df[df["intensity_zone_pace"] == "Baixa"])
    return round(100 * easy_sessions / total_sessions, 1)

def add_intensity_index(df: pd.DataFrame, reference_pace: float, race_distance: float) -> pd.DataFrame:
    """
    Adds an intensity index and zone to the DataFrame based on a distance-adjusted reference pace.
    
    Parameters:
    - df: DataFrame with a 'pace_decimal' column (min/km).
    - reference_pace: User's race pace (min/km).
    - race_distance: Distance of the race (in km), may be imprecise due to GPS error.

    Returns:
    - The same df with two new columns: 'intensity_index' and 'intensity_zone'
    """


    if "average_pace" not in df.columns:
        raise ValueError("DataFrame must contain a 'average_pace' column in min/km")

    # Known race distances and their corresponding adjustment factors
    distance_factors = {
        5.0: 1.05,
        10.0: 1.03,
        15.0: 1.00,
        21.1: 0.98,
        42.2: 0.95
    }

    # Find the closest race distance
    closest_dist = min(distance_factors.keys(), key=lambda d: abs(d - race_distance))
    factor = distance_factors[closest_dist]

    # Adjust race pace
    adjusted_reference_pace = reference_pace * factor
    adjusted_reference_pace_str = decimal_pace_to_str(adjusted_reference_pace)
    

    # Calculate intensity index
    df["intensity_index"] = df["average_pace"] / adjusted_reference_pace

    df["intensity_zone_pace"] = df["intensity_index"].apply(label_intensity)

    return df, adjusted_reference_pace_str

def speed_to_pace(speed_kmh):
    """Convert speed (km/h) to pace (min/km)"""
    if pd.isna(speed_kmh) or speed_kmh == 0:
        return None
    minutes_per_km = 60 / speed_kmh
    return minutes_per_km

def get_activity_details(activity_id, access_token):
    url = f"https://www.strava.com/api/v3/activities/{activity_id}"
    headers = {
        "Authorization": f"Bearer {access_token}"
    }
    response = requests.get(url, headers=headers)
    return response.json()

def get_segment_details(segment_id, access_token):
    url = f"https://www.strava.com/api/v3/segments/{segment_id}"
    headers = {
        "Authorization": f"Bearer {access_token}"
    }
    response = requests.get(url, headers=headers)
    response.raise_for_status()  # Raise an error for bad responses
    return response.json()

def get_starred_segments(access_token):
    """
    Get all starred segments for the authenticated athlete.
    
    Parameters:
    - access_token: Strava API access token
    
    Returns:
    - List of starred segment IDs
    """
    url = "https://www.strava.com/api/v3/segments/starred"
    headers = {
        "Authorization": f"Bearer {access_token}"
    }
    
    starred_segments = []
    page = 1
    
    while True:
        params = {'page': page, 'per_page': 200}
        response = requests.get(url, headers=headers, params=params)
        
        if response.status_code != 200:
            st.error(f"Error getting starred segments: {response.status_code}")
            break
            
        data = response.json()
        if not data:
            break
            
        starred_segments.extend([segment['id'] for segment in data])
        page += 1
    
    return starred_segments

def analyze_volume_progression(weekly_distance):
    """
    Analyze weekly volume progression to check if it follows good practices:
    - Weekly changes between 8-12% (ideal progression range)
    - Recovery weeks every 3-4 weeks
    """
    # Calculate percentage changes
    pct_changes = weekly_distance['Distance'].pct_change() * 100
    
    # Check if changes are within the ideal range (8-12%) or too large
    ideal_changes = (abs(pct_changes) >= 6) & (abs(pct_changes) <= 15)
    too_large_changes = abs(pct_changes) > 15

    # Calculate percentage of weeks with ideal and too large changes
    pct_ideal_changes = (ideal_changes.sum() / len(pct_changes)) * 100
    pct_too_large = (too_large_changes.sum() / len(pct_changes)) * 100
    
    # Look for recovery weeks (weeks with volume decrease > 20%)
    recovery_weeks = pct_changes < -20
    recovery_freq = len(weekly_distance) / (recovery_weeks.sum() if recovery_weeks.sum() > 0 else 1)

    return {
        'pct_ideal_changes': pct_ideal_changes,
        'pct_too_large': pct_too_large,
        'recovery_freq': recovery_freq,
        'has_recovery': recovery_weeks.sum() > 0
    }

def analyze_frequency_consistency(weekly_sessions):
    """
    Analyze training frequency consistency
    """
    # Calculate coefficient of variation (CV) to measure consistency
    cv = weekly_sessions['Sessions'].std() / weekly_sessions['Sessions'].mean() * 100
    
    # Calculate the most common number of sessions
    mode_sessions = weekly_sessions['Sessions'].mode()[0]
    
    # Calculate percentage of weeks that match the mode
    pct_consistent = (weekly_sessions['Sessions'] == mode_sessions).mean() * 100
    
    return {
        'cv': cv,
        'mode_sessions': mode_sessions,
        'pct_consistent': pct_consistent
    }

def analyze_intensity_distribution(df_intensity):
    """
    Analyze training intensity distribution
    """
    total_sessions = len(df_intensity)
    easy_sessions = len(df_intensity[df_intensity['intensity_zone_pace'] == 'Baixa'])
    easy_percentage = (easy_sessions / total_sessions) * 100
    
    # Calculate deviation from 80/20 rule
    deviation_from_ideal = abs(easy_percentage - 80)
    
    return {
        'easy_percentage': easy_percentage,
        'deviation': deviation_from_ideal
    }

# Add this just before the References section
def display_training_summary(weekly_distance, weekly_sessions, df_intensity):
    """
    Display a summary of training analysis with recommendations
    """
    st.markdown("## Resum de l'anàlisi")
    
    # Analyze each component
    volume_analysis = analyze_volume_progression(weekly_distance)
    frequency_analysis = analyze_frequency_consistency(weekly_sessions)
    intensity_analysis = analyze_intensity_distribution(df_intensity)
    
    # Create three columns for the success/warning boxes
    col1, col2, col3 = st.columns(3)
    
    # Lists to collect all messages
    messages = []
    
    with col1:
        with st.container(border=True, height=300):
            st.markdown("### **Volum**")
            if volume_analysis['pct_too_large'] > 50:
                st.warning("⚠️ Variació setmanal massa gran")
                messages.append("##### - Intenta que els canvis setmanals siguin més suaus (±10%) per reduir el risc de lesió.")
            else:
                st.success("✅ Progressió gradual del volum")
                messages.append("##### - La progressió setmanal de volumn és adequada, al voltant del 10%.")
                
            if not volume_analysis['has_recovery']:
                st.warning("⚠️ No es detecten setmanes de recuperació")
                messages.append("##### - Considera incloure setmanes de recuperació (reduïr entre 20%-30% el volum) cada 3-4 setmanes.")
            elif volume_analysis['recovery_freq'] < 2:
                st.warning("⚠️ Falta de consistència del volum")
                messages.append("##### - El volum no incrementa gradualment, intenta mantenir un volum factible i pujar-lo gradualment.")
            elif volume_analysis['recovery_freq'] > 5:
                st.warning("⚠️ Falta de setmanes de recuperació")
                messages.append("##### - Considera fer setmanes de recuperació més sovint.")
            else:
                st.success("✅ S'inclouen setmanes de recuperació") 
            
    with col2:
        with st.container(border=True, height=300):
            st.markdown("### **Freqüència**")
            if frequency_analysis['cv'] < 25:
                st.success("✅ Freqüència consistent")
                messages.append(f"##### - Mantens una freqüència constant al voltant de {frequency_analysis['mode_sessions']} sessions/setmana.")
            else:
                st.warning("⚠️ Freqüència irregular")
                messages.append("##### - Intenta mantenir una freqüència més constant d'entrenaments.")
                  
    with col3:
        with st.container(border=True, height=300):
            st.markdown("### **Intensitat**")
            if abs(intensity_analysis['deviation']) <= 10:
                st.success("✅ Bona distribució d'intensitat")
                messages.append("##### - La teva distribució d'intensitat s'apropa al 80 (baixa) / 20 (mitja-alta) recomanat.")
            else:
                if intensity_analysis['easy_percentage'] < 70:
                    st.warning("⚠️ Massa sessions intenses")
                    messages.append("##### - Considera fer més sessions a baixa intensitat.")
                else:
                    st.warning("⚠️ Distribució d'intensitat desequilibrada")
                    messages.append("##### - Intenta ajustar la distribució d'intensitats al 80 (baixa) / 20 (mitja-alta).")

    # Display all messages together below the columns
    #st.markdown("### Recomanacions:")
    # with st.container(border=False):
    #     # Convert messages to a single string
    #     messages_text = "\n".join(messages)
        
    #     # Call OpenAI API to generate a coherent message
    #     try:
    #         client = openai.OpenAI()
    #         response = client.chat.completions.create(
    #             model="gpt-3.5-turbo",
    #             messages=[
    #                 {"role": "system", "content": "Ets un entrenador de running que parla català. El teu estil és motivador i constructiu. Rebràs tres missatges referents a l'entrenament d'un atleta per una prova i que fan referència al volum, freqüència i la intensitat de les setmanes prèvies a la prova."},
    #                 {"role": "user", "content": f"Converteix aquestes recomanacions en un missatge coherent i instructiu en català que resumeixi les recomanacions i aporti informació útil:\n\n{messages_text}. Fes un text curt i senzill, no més de 100 paraules.No utilitzis valors concrets, només recomanacions basades en els missatges rebuts i informació general sobre entrenament."}
    #             ],
    #             temperature=0.7,
    #             max_tokens=500
    #         )
            
    #         # Display the generated message
    #         st.markdown(f"""
    #         <div style="background-color: #ffffff; padding: 20px; border-radius: 0px; box-shadow: 0 0 10px rgba(0,0,0,0.1);">
    #             <h5>{response.choices[0].message.content}</h5>
    #         </div>
    #         """, unsafe_allow_html=True)
    #     except Exception as e:
    #         # Fallback to original messages if API call fails
    #         st.markdown("\n".join(messages))

def get_segments_data(activities, get_activity_details, get_segment_details, access_token):
    """
    Extract segment data from activities and return as a DataFrame.
    Only includes efforts on starred segments.
    
    Parameters:
    - activities: List of activities from Strava
    - get_activity_details: Function to get detailed activity information
    - get_segment_details: Function to get detailed segment information
    - access_token: Strava API access token
    
    Returns:
    - pandas DataFrame containing segment information for starred segments
    """
    # Get starred segments first
    starred_segments = get_starred_segments(access_token)
    
    if not starred_segments:
        st.warning("No starred segments found. Star some segments on Strava to track them here!")
        return pd.DataFrame()
    
    segment_data = []
    
    for activity in activities:
        activity_id = activity['activity_id']
        activity_details = get_activity_details(activity_id, access_token)
        
        if 'segment_efforts' not in activity_details:
            continue
            
        for segment_effort in activity_details['segment_efforts']:
            segment_id = segment_effort['segment']['id']
            
            # Skip if segment is not starred
            if segment_id not in starred_segments:
                continue
                
            segment_details = get_segment_details(segment_id, access_token)
            
            # Extract basic effort data
            elapsed_time = segment_effort['elapsed_time']
            distance = segment_effort['distance']
            average_speed = segment_effort.get('average_speed', distance / elapsed_time)
            
            # Calculate pace
            pace_per_km = elapsed_time / (distance / 1000)
            pace_minutes = int(pace_per_km // 60)
            pace_seconds = int(pace_per_km % 60)
            pace_str = f"{pace_minutes}:{pace_seconds:02d}"
            
            # Get PR time if available
            pr_elapsed_time = None
            pr_pace_str = None
            if 'athlete_segment_stats' in segment_details:
                pr_elapsed_time = segment_details['athlete_segment_stats'].get('pr_elapsed_time')
                if pr_elapsed_time:
                    pr_pace = pr_elapsed_time / (distance / 1000)
                    pr_minutes = int(pr_pace // 60)
                    pr_seconds = int(pr_pace % 60)
                    pr_pace_str = f"{pr_minutes}:{pr_seconds:02d}"
            
            # Create dictionary with segment information
            segment_info = {
                'activity_id': activity_id,
                'activity_name': activity['name'],
                'activity_date': activity['datetime_local'],
                'segment_id': segment_id,
                'segment_name': segment_details['name'],
                'distance_km': round(distance / 1000, 2),
                'elapsed_time_sec': elapsed_time,
                'elapsed_time_str': f"{int(elapsed_time // 60)}:{int(elapsed_time % 60):02d}",
                'average_speed_kmh': round(average_speed * 3.6, 2),
                'pace_min_km': pace_str,
                'pr_elapsed_time': pr_elapsed_time,
                'pr_pace_min_km': pr_pace_str
            }
            
            segment_data.append(segment_info)
    
    # Create DataFrame
    df_segments = pd.DataFrame(segment_data)
    
    if not df_segments.empty:
        # Sort by segment name and date
        df_segments = df_segments.sort_values(['segment_name', 'activity_date'])
        
        # Format the date
        df_segments['activity_date'] = pd.to_datetime(df_segments['activity_date']).dt.strftime('%d/%m/%Y')
    
    return df_segments

# After the supabase client initialization, add this function:
def log_user_session(athlete_id: Optional[int], event_type: str, event_data: Optional[dict] = None):
    """
    Log user session data to Supabase.
    
    Parameters:
    - athlete_id: Strava athlete ID (0 for unauthenticated users)
    - event_type: Type of event (e.g., 'app_open', 'auth_start', 'data_load', etc.)
    - event_data: Optional dictionary with additional event data
    """
    try:
        log_entry = {
            'athlete_id': athlete_id if athlete_id is not None else 0,  # Use 0 for unauthenticated users
            'event_type': event_type,
            'event_data': event_data,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        supabase.table('app_logs').insert(log_entry).execute()
    except Exception as e:
        st.error(f"Error logging event: {str(e)}")

# Initialize session state variables at the very beginning of the script, right after the imports
if 'access_token' not in st.session_state:
    st.session_state.access_token = None
if 'athlete_id' not in st.session_state:
    st.session_state.athlete_id = None
if 'session_id' not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

def main():
    # Log app open at the start of main with athlete_id=0 if not authenticated
    log_user_session(
        athlete_id=st.session_state.get('athlete_id', 0),  # Default to 0 if not authenticated
        event_type='app_open',
        event_data={'session_id': st.session_state.get('session_id')}
    )
    
    # Check for authorization code in URL parameters
    query_params = st.query_params
    if 'code' in query_params:
        code = query_params.get("code", [])
        try:
            token_data = get_token(code)
            if 'access_token' in token_data:
                st.session_state.access_token = token_data['access_token']
                st.session_state.athlete_id = token_data['athlete']['id']
                save_token_to_supabase(token_data)
                st.query_params.clear()
                st.rerun()
        except Exception as e:
            st.error(f"Error during token exchange: {str(e)}")
    
    # Try to get stored token if we don't have one in session
    if not st.session_state.access_token and st.session_state.athlete_id is not None:
        # Try to get a fresh token for this athlete
        fresh_token = ensure_fresh_token()
        if fresh_token:
            st.session_state.access_token = fresh_token
            st.rerun()
        else:
            st.warning("Si us plau, connecta amb Strava primer a la pàgina d'inici.")
            st.markdown("[Connecta amb Strava](/Inici)")
            st.stop()
    elif not st.session_state.access_token:
        st.warning("Si us plau, connecta amb Strava primer a la pàgina d'inici.")
        st.markdown("[Connecta amb Strava](/Inici)")
        st.stop()

    activities = get_activities(st.session_state.access_token)
    if activities:
        # Log successful data load
        log_user_session(
            st.session_state.athlete_id,
            'data_load',
            {
                'activities_count': len(activities),
                'date_range': [
                    min(a['datetime_local'] for a in activities),
                    max(a['datetime_local'] for a in activities)
                ]
            }
        )
        
        # Convert activities to DataFrame
        df = pd.DataFrame(activities)
    else:
        # Log failed data load
        log_user_session(
            st.session_state.athlete_id,
            'data_load_failed'
        )
        st.warning("No s'han trobat activitats.")

    if df is not None:
        # Add these session state initializations
        if 'date_range' not in st.session_state:
            st.session_state.date_range = (
                pd.to_datetime('now').date() - pd.DateOffset(days=60),
                pd.to_datetime('now').date()
            )
        if 'selected_activity_type' not in st.session_state:
            st.session_state.selected_activity_type = []
        if 'form_submitted' not in st.session_state:
            st.session_state.form_submitted = False
        st.title("Anàlisi de l'entrenament:chart_with_upwards_trend:")
        st.write("")
        st.markdown("""
        <div style="background-color: #ffffff; padding: 20px; border-radius: 0px; box-shadow: 0 0 10px rgba(0,0,0,0.1); margin-bottom: 20px;">
            <div style="display: flex; gap: 20px;">
                <div style="flex: 1;">
                    <h5>🔍 Per començar, selecciona el període de temps i els esports que vols incloure a l'anàlisi.</h5>
                </div>
                <div style="flex: 1;">
                    <h5>📆 Recomanem un període d'<strong>entre 2 i 4 mesos</strong>, on l'últim dia seleccionat és el de la cursa que vols analitzar.</h5>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)
        with st.container(border=False):
            # Modify the form to update session state
            with st.form("date_selection_form", border=True):
                col1, col2, col3 = st.columns([1,2,1])
                with col1:
                    selected_dates = st.date_input(
                        "",
                        value=st.session_state.date_range,
                        min_value=pd.to_datetime(df['datetime_local'].min()).date(),
                        max_value=pd.to_datetime('now').date(),
                        label_visibility="collapsed"
                    )
                with col2:
                    running_types = df['type'].unique().tolist()
                    selected_type = st.multiselect(
                        "Selecciona el tipus de cursa:",
                        options=running_types,
                        label_visibility="collapsed",
                        key="activity_type_select",
                        placeholder="Totes les activitats"
                    )
                with col3:
                    submit_dates = st.form_submit_button("Guardar")
                
                if submit_dates:
                    st.session_state.date_range = selected_dates
                    st.session_state.selected_activity_type = selected_type
                    st.session_state.form_submitted = True
                    st.rerun()

            # Only show analysis if form has been submitted or if we have valid session state
            if not st.session_state.form_submitted and not st.session_state.selected_activity_type:
                st.info("Selecciona el període de temps, els esports que vols incloure i fes clic a 'Guardar' per començar l'anàlisi.")
                st.stop()

            # Use session state values for filtering
            df['datetime_local'] = pd.to_datetime(df['datetime_local'])
            
            if not st.session_state.selected_activity_type:  # If no types selected, show all
                mask = (
                    (df['datetime_local'].dt.date >= pd.to_datetime(st.session_state.date_range[0]).date()) & 
                    (df['datetime_local'].dt.date <= pd.to_datetime(st.session_state.date_range[1]).date())
                )
            else:  # Filter for selected types
                mask = (
                    (df['datetime_local'].dt.date >= pd.to_datetime(st.session_state.date_range[0]).date()) & 
                    (df['datetime_local'].dt.date <= pd.to_datetime(st.session_state.date_range[1]).date()) &
                    (df['type'].isin(st.session_state.selected_activity_type))
                )
            df_filtered = df[mask]

        st.markdown("## Volum")
        
        st.markdown("""
        <div style="background-color: #ffffff; padding: 20px; border-radius: 0px; box-shadow: 0 0 10px rgba(0,0,0,0.1); margin-bottom: 20px;">
            <div style="display: flex; gap: 20px;">
                <div style="flex: 1;">
                    <h5>🔁 Incrementar <strong>gradualment</strong> i ser <strong>consistent</strong> amb el volum setmanal és un senyal de millora del nivell de forma.</h5>
                </div>
                <div style="flex: 1;">
                    <h5>💡 Es recomana estar al voltant del <span style="background-color: #FFD700; padding: 5px; border-radius: 5px;">10% de variació setmanal</span>.</h5>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        col1v, col2v = st.columns([0.7,0.3])
        with col1v:
            # Create tabs for distance and time charts
            tab1, tab2 = st.tabs(["📏 Distància", "⏱️ Temps"])

            # Group by year-week and sum distances
            weekly_distance = df_filtered.groupby([
                df_filtered['datetime_local'].dt.isocalendar().year,
                df_filtered['datetime_local'].dt.isocalendar().week
            ]).agg({
                'distance': 'sum',
                'moving_time': 'sum'
            }).reset_index()
            weekly_distance.columns = ['Year', 'Week', 'Distance', 'Time']

            # Create a combined year-week label for x-axis
            weekly_distance['Week_Label'] = weekly_distance.apply(lambda x: f"S{int(x['Week']):02d}", axis=1)
            
            # Calculate percentage changes
            weekly_distance['Distance_pct'] = weekly_distance['Distance'].pct_change() * 100
            weekly_distance['Time_pct'] = weekly_distance['Time'].pct_change() * 100

            # Add date column for x-axis labels
            # Create Catalan month mapping
            catalan_months = {
                'Jan': 'Gen',
                'Feb': 'Feb',
                'Mar': 'Mar',
                'Apr': 'Abr',
                'May': 'Mai',
                'Jun': 'Jun',
                'Jul': 'Jul',
                'Aug': 'Ago',
                'Sep': 'Set',
                'Oct': 'Oct',
                'Nov': 'Nov',
                'Dec': 'Des'
            }

            weekly_distance['Week_Start_Date'] = pd.to_datetime(weekly_distance['Year'].astype(str) + '-' + 
                                                              weekly_distance['Week'].astype(str) + '-1', 
                                                              format='%Y-%W-%w')
            
            # Format date with Catalan months
            weekly_distance['Date_Label'] = weekly_distance['Week_Start_Date'].dt.strftime('%d-%b-%y')
            weekly_distance['Date_Label'] = weekly_distance['Date_Label'].apply(
                lambda x: x.replace(x[3:6], catalan_months[x[3:6]])
            )

            with tab1:
                # Create the distance bar chart
                fig_distance = go.Figure()
                mean_distance = weekly_distance['Distance'].mean()

                # Add main bars with formatted distance labels
                fig_distance.add_trace(
                    go.Bar(
                        x=weekly_distance['Date_Label'],
                        y=weekly_distance['Distance'],
                        text=weekly_distance['Distance'].round(0).astype(int).astype(str) + 'km',  # Format as "10km"
                        textposition='inside',
                        marker_color='rgb(207, 240, 17)',
                        opacity=0.6,
                        textfont=dict(
                            size=14
                        )
                    )
                )

                # Add percentage change labels for distance chart
                fig_distance.add_trace(
                    go.Scatter(
                        x=weekly_distance['Date_Label'],
                        y=weekly_distance['Distance'],
                        text=weekly_distance['Distance_pct'].apply(
                            lambda x: f"{x:+.0f}%" if pd.notnull(x) else ""
                        ),
                        textposition='top center',
                        mode='text',
                        showlegend=False,
                        textfont=dict(
                            size=14,
                            color=weekly_distance['Distance_pct'].apply(
                                lambda x: '#DAA520' if pd.notnull(x) and (x > 10 or x < -10) else 'green'
                            )
                        )
                    )
                )

                # Update layout with rotated x-axis labels for better readability
                fig_distance.update_layout(
                    title='Distància setmanal (km)',
                    xaxis_title='Setmana',
                    yaxis_title='Distància (km)',
                    showlegend=False,
                    plot_bgcolor='#fcfcfc',
                    paper_bgcolor='#fcfcfc',
                    xaxis=dict(
                        tickangle=45  # Rotate labels for better readability
                    )
                )
                
                # Update axes
                fig_distance.update_xaxes(
                    showgrid=False,
                    gridwidth=1,
                    gridcolor='#fcfcfc'
                )
                fig_distance.update_yaxes(
                    showgrid=False,
                    gridwidth=1,
                    gridcolor='#fcfcfc',
                    zeroline=True,
                    zerolinewidth=1,
                    zerolinecolor='#fcfcfc'
                )
                
                st.plotly_chart(fig_distance, use_container_width=True)

            with tab2:
                # Convert minutes to hours for better readability
                weekly_distance['Time'] = weekly_distance['Time'] / 60  # Convert to hours
                mean_time = weekly_distance['Time'].mean()

                # Create the time bar chart
                fig_time = go.Figure()
                
                # Format time labels as "3h50min"
                def format_time_label(hours):
                    total_minutes = int(hours * 60)
                    h = total_minutes // 60
                    m = total_minutes % 60
                    return f"{h}h{m:02d}min"

                # Add main bars with formatted time labels
                fig_time.add_trace(
                    go.Bar(
                        x=weekly_distance['Date_Label'],
                        y=weekly_distance['Time'],
                        text=weekly_distance['Time'].apply(format_time_label),
                        textposition='auto',
                        marker_color='rgb(207, 240, 17)',
                        opacity=0.6,
                        textfont=dict(
                            size=14
                        )
                    )
                )

                # Add percentage change labels
                fig_time.add_trace(
                    go.Scatter(
                        x=weekly_distance['Date_Label'],
                        y=weekly_distance['Time'],
                        text=weekly_distance['Time_pct'].apply(
                            lambda x: f"{x:+.0f}%" if pd.notnull(x) else ""
                        ),
                        textposition='top center',
                        mode='text',
                        showlegend=False,
                        textfont=dict(
                            size=14,
                            color=weekly_distance['Time_pct'].apply(
                                lambda x: '#DAA520' if pd.notnull(x) and (x > 10 or x < -10) else 'green'
                            )
                        )
                    )
                )

                # Update layout with rotated x-axis labels for better readability
                fig_time.update_layout(
                    title='Temps setmanal (hores)',
                    xaxis_title='Setmana',
                    yaxis_title='Temps (h)',
                    showlegend=False,
                    plot_bgcolor='#fcfcfc',
                    paper_bgcolor='#fcfcfc',
                    xaxis=dict(
                        tickangle=45  # Rotate labels for better readability
                    )
                )
                
                # Update axes
                fig_time.update_xaxes(
                    showgrid=False,
                    gridwidth=1,
                    gridcolor='#fcfcfc'
                )
                fig_time.update_yaxes(
                    showgrid=False,
                    gridwidth=1,
                    gridcolor='#fcfcfc',
                    zeroline=True,
                    zerolinewidth=1,
                    zerolinecolor='#fcfcfc'
                )
                
                st.plotly_chart(fig_time, use_container_width=True)
        with col2v:
            st.markdown("""
            <div style="background-color: #ffffff; padding: 20px; border-radius: 0px; box-shadow: 0 0 10px rgba(0,0,0,0.1);">
                <h5><strong>Com interpretar el gràfic</strong> 📊</h5>
                <p>• Les columnes mostren la distància o el temps setmanal total dels esports que hagis seleccionat a l'inici.</p>
                <p>• Els percentatges en <span style="color: green;">verd</span> indiquen canvis graduals (entre <span style="color: green;">-10% i +10%</span>).</p>
                <p>• Els percentatges en <span style="color: #DAA520;">groc</span> indiquen canvis significatius (<span style="color: #DAA520;">>10% o <-10%</span>).</p>
                <br>
            </div>
            """, unsafe_allow_html=True)

        
        st.markdown("### Sortides llargues")
        
        st.markdown("""
        <div style="background-color: #ffffff; padding: 20px; border-radius: 0px; box-shadow: 0 0 10px rgba(0,0,0,0.1); margin-bottom: 20px;">
            <div style="display: flex; gap: 20px;">
                <div style="flex: 1;">
                    <h5>❓ La distància dependrà del teu nivell i objectiu, però el més important és començar amb una distància que et permeti progressar setmana a setmana.</h5>
                    <h5>🧭Una forma de comprovar això és mantenir la distància d'aquesta sortida <strong>entre el 30% i el 40% del total setmanal</strong>.</h5>
                </div>
                <div style="flex: 1;">
                    <h5>🏃 Incrementar la distància setmana a setmana amb ritmes semblants és un bon indicador de que estàs millorant.</h5>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)
        # Get longest activity per week and weekly totals
        weekly_totals = df_filtered[df_filtered['sport'] == 'Run'].groupby([
            df_filtered['datetime_local'].dt.isocalendar().year,
            df_filtered['datetime_local'].dt.isocalendar().week
        ])['distance'].sum().reset_index()
        weekly_totals.columns = ['year', 'week', 'weekly_total']
        
        longest_runs = df_filtered[df_filtered['sport'] == 'Run'].groupby([
            df_filtered['datetime_local'].dt.isocalendar().year,
            df_filtered['datetime_local'].dt.isocalendar().week
        ]).apply(
            lambda x: x.nlargest(1, 'distance')
        ).reset_index(drop=True)

        # Add weekly totals to longest runs
        longest_runs['year'] = longest_runs['datetime_local'].dt.isocalendar().year
        longest_runs['week'] = longest_runs['datetime_local'].dt.isocalendar().week
        longest_runs = longest_runs.merge(weekly_totals, on=['year', 'week'], how='left')
        
        # Calculate percentage
        longest_runs['percentage'] = (longest_runs['distance'] / longest_runs['weekly_total'] * 100)

        # Select columns for display - keep numeric percentage for styling
        longest_runs_display = longest_runs[[
            'datetime_local', 'name', 'distance', 'moving_time', 'average_speed', 'percentage'
        ]].copy()

        # Create a mapping of activity names to workout types for styling
        activity_workout_types = df_filtered.set_index('name')['workout_type'].to_dict()

        # Sort by datetime first (while it's still in datetime format)
        longest_runs_display = longest_runs_display.sort_values('datetime_local', ascending=False)

        # Format display columns (except percentage)
        longest_runs_display['datetime_local'] = longest_runs_display['datetime_local'].dt.strftime('%d/%m/%Y')
        longest_runs_display['moving_time'] = longest_runs_display['moving_time'].apply(
            lambda x: f"{int(x//60)}h{int(x%60)}min" if x >= 60 else f"{int(x)}min"
        )
        longest_runs_display['distance'] = longest_runs_display['distance'].apply(lambda x: f"{x:.1f} km")
        longest_runs_display['average_speed'] = longest_runs_display['average_speed'].apply(
            lambda x: f"{int((60/x))}:{int((60/x)%1 * 60):02d} min/km"
        )
        # The 'percentage' column is still numeric here

        # Rename columns for final display
        longest_runs_display.columns = ['Data', 'Nom', 'Distància', 'Temps', 'Ritme', '% del total']

        # Define styling function for race activities
        def style_race_activities(val):
            if val in activity_workout_types and activity_workout_types[val] == 1:
                return 'background-color: #FFB6C1'  # Light red color
            return ''

        # Define styling function for percentage background
        def style_percentage_background(val):
            if pd.isna(val):
                return '' # No style for NaN
            elif 30 <= val <= 40:
                return 'background-color: lightgreen'
            else:
                return 'background-color: #FFFFE0' # Light Yellow hex

        # Create two columns for the dataframe and description
        col1_long, col2_long = st.columns([0.7, 0.3])
        
        with col1_long:
            st.write("**Sessió més llarga per setmana i % del total de distància setmanal**")
            st.dataframe(
                longest_runs_display.style
                .apply(lambda col: col.map(style_race_activities) if col.name == 'Nom' else [''] * len(col))
                .apply(
                    lambda col: col.map(style_percentage_background),
                    subset=['% del total']
                )
                .format(
                    {'% del total': lambda x: f"{x:.1f}%" if pd.notna(x) else "-"}
                ),
                use_container_width=True,
                hide_index=True
            )

        with col2_long:
            st.markdown("""
            <div style="background-color: #ffffff; padding: 20px; border-radius: 0px; box-shadow: 0 0 10px rgba(0,0,0,0.1);">
                <h5><strong>Com interpretar la taula</strong> 📊</h5>
                <p>• La taula mostra l'entrenament més llarg de cada setmana per activitats de running i el percentatge que la distància d'aquesta sortida representa respecte el total setmanal.</p>
                <p>• El fons <span style="color: green;">verd</span> indica que la sortida llarga està dintre del rang recomanat (30-40% del total), mentre que el fons <span style="color:#DAA520;">groc</span> indica està fora.</p>
                <p>• Si es detecta una activitat marcada com a cursa a Strava, es mostrarà de color <span style="color: red;">vermell</span>.</p>
                <br>
            </div>
            """, unsafe_allow_html=True)

        # Create line chart for longest runs with weekly distance bars
        fig_longest = go.Figure()
        
        # Format dates for x-axis
        longest_runs['Week_Start_Date'] = pd.to_datetime(longest_runs['year'].astype(str) + '-' + 
                                                       longest_runs['week'].astype(str) + '-1', 
                                                       format='%Y-%W-%w')
        weekly_totals['Week_Start_Date'] = pd.to_datetime(weekly_totals['year'].astype(str) + '-' + 
                                                        weekly_totals['week'].astype(str) + '-1', 
                                                        format='%Y-%W-%w')

        # Format date labels with Catalan months
        longest_runs['Date_Label'] = longest_runs['Week_Start_Date'].dt.strftime('%d-%b-%y')
        longest_runs['Date_Label'] = longest_runs['Date_Label'].apply(
            lambda x: x.replace(x[3:6], catalan_months[x[3:6]])
        )
        weekly_totals['Date_Label'] = weekly_totals['Week_Start_Date'].dt.strftime('%d-%b-%y')
        weekly_totals['Date_Label'] = weekly_totals['Date_Label'].apply(
            lambda x: x.replace(x[3:6], catalan_months[x[3:6]])
        )

        # Add weekly distance bars
        fig_longest.add_trace(
            go.Bar(
                x=weekly_totals['Date_Label'],
                y=weekly_totals['weekly_total'],
                name='Distància setmanal',
                marker_color='rgb(207, 240, 17)',
                opacity=0.6,
                hovertemplate='Setmana: %{x}<br>Distància total: %{y:.1f} km<extra></extra>'
            )
        )
        
        # Add longest run line
        fig_longest.add_trace(
            go.Scatter(
                x=longest_runs['Date_Label'],
                y=longest_runs['distance'],
                mode='lines+markers+text',
                name='Sortida més llarga',
                marker_color='rgba(34, 40, 49, 0.6)',  # Converted from #222831 to rgba
                text=longest_runs['distance'].round(1).astype(str) + 'km',
                textposition='top center',
                hovertemplate='Setmana: %{x}<br>Distància: %{y:.1f} km<extra></extra>'
            )
        )

        # Update layout
        fig_longest.update_layout(
            title='Long runs vs distància total setmanal',
            xaxis_title='Setmana',
            yaxis_title='Distància (km)',
            showlegend=False,
            plot_bgcolor='#fcfcfc',
            paper_bgcolor='#fcfcfc',
            yaxis=dict(
                range=[0, max(longest_runs['distance'].max(), weekly_totals['weekly_total'].max()) * 1.2]
            ),
            legend=dict(
                yanchor="top",
                y=0.99,
                xanchor="left",
                x=0.01
            ),
            xaxis=dict(
                tickangle=45  # Rotate labels for better readability
            )
        )

        # Update axes
        fig_longest.update_xaxes(
            showgrid=False,
            gridwidth=1,
            gridcolor='#fcfcfc'
        )
        fig_longest.update_yaxes(
            showgrid=True,
            gridwidth=1,
            gridcolor='#fcfcfc',
            zeroline=True,
            zerolinewidth=1,
            zerolinecolor='#fcfcfc'
        )

        st.plotly_chart(fig_longest, use_container_width=True)
        
        st.divider()
        st.markdown("## Freqüència")
        
        st.markdown("""
        <div style="background-color: #ffffff; padding: 20px; border-radius: 0px; box-shadow: 0 0 10px rgba(0,0,0,0.1); margin-bottom: 20px;">
            <div style="display: flex; gap: 20px;">
                <div style="flex: 1;">
                    <h5>🎯 Una major freqüència d'entrenament pot ser beneficiosa perquè produeix estímuls més constants i <span style="text-decoration: underline; text-decoration-color: #FFD700; text-decoration-thickness: 3px;">distribueix millor la fatiga</span>, evitant sessions amb càrrega excessiva.</h5>
                </div>
                <div style="flex: 1;">
                    <h5>📈 Busca la freqüència que et permeti <span style="background-color: #FFD700;">ser consistent</span> i trobar l'organització per <span style="background-color: #FFD700;">entrenar de forma continuada en el temps</span>.</h5>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        # Count sessions per week
        weekly_sessions = df_filtered.groupby([
            df_filtered['datetime_local'].dt.isocalendar().year,
            df_filtered['datetime_local'].dt.isocalendar().week
        ]).size().reset_index()
        weekly_sessions.columns = ['Year', 'Week', 'Sessions']

        # Create a combined year-week label for x-axis
        weekly_sessions['Week_Label'] = weekly_sessions.apply(lambda x: f"S{int(x['Week']):02d}", axis=1)

        # Calculate metrics for all activities
        mode_sessions = weekly_sessions['Sessions'].mode()[0]  # [0] because mode can return multiple values
        avg_sessions = weekly_sessions['Sessions'].mean()

        # Calculate metrics for Run activities only
        weekly_runs = df_filtered[df_filtered['sport'] == 'Run'].groupby([
            df_filtered['datetime_local'].dt.isocalendar().year,
            df_filtered['datetime_local'].dt.isocalendar().week
        ]).size().reset_index()
        weekly_runs.columns = ['Year', 'Week', 'Runs']
        avg_runs = weekly_runs['Runs'].mean()

        # Create three columns for the metrics
        col1, col2 = st.columns(2)

        with col1:
            st.markdown(f"""
                <div style="background-color: #ffffff; padding: 20px; border-radius: 0px; box-shadow: 0 0 10px rgba(0,0,0,0.1);">
                    <div style="font-size: 14px; font-family: 'Helvetica Neue', sans-serif; color: #666666;">Nombre de sessions més repetit</div>
                    <div style="font-size: 24px; font-weight: bold;">{mode_sessions:.0f}</div>
                </div>
            """, unsafe_allow_html=True)
        with col2:
            st.markdown(f"""
                <div style="background-color: #ffffff; padding: 20px; border-radius: 0px; box-shadow: 0 0 10px rgba(0,0,0,0.1);">
                    <div style="font-size: 14px; font-family: 'Helvetica Neue', sans-serif; color: #666666;">Nombre mitjà d'activitats de running</div>
                    <div style="font-size: 24px; font-weight: bold;">{avg_runs:.0f}</div>
                </div>
            """, unsafe_allow_html=True)

        # Create the sessions bar chart
        # Add date column for x-axis labels
        weekly_sessions['Week_Start_Date'] = pd.to_datetime(weekly_sessions['Year'].astype(str) + '-' + 
                                                          weekly_sessions['Week'].astype(str) + '-1', 
                                                          format='%Y-%W-%w')
        
        weekly_sessions['Date_Label'] = weekly_sessions['Week_Start_Date'].dt.strftime('%d-%b-%Y')
        weekly_sessions['Date_Label'] = weekly_sessions['Date_Label'].apply(
            lambda x: x.replace(x[3:6], catalan_months[x[3:6]])
        )

        # Create two columns for the chart and description
        col1_chart, col2_desc = st.columns([0.7, 0.3])

        with col1_chart:
            st.write("")
            fig_sessions = go.Figure(data=go.Scatter(
                x=weekly_sessions['Date_Label'],
                y=weekly_sessions['Sessions'],
                mode='markers+text',
                marker=dict(
                    size=weekly_sessions['Sessions'] * 5,
                    color=weekly_sessions['Sessions'],
                    colorscale='Reds',
                    showscale=False
                ),
                text=weekly_sessions['Sessions'],
                textposition='top center'
            ))

            fig_sessions.update_layout(
                title='Sessions per setmana',
                xaxis_title='Setmana',
                yaxis_title='',
                plot_bgcolor='#fcfcfc',
                paper_bgcolor='#fcfcfc',
                yaxis=dict(
                    showgrid=False,
                    showticklabels=False,
                    showline=False
                ),
                xaxis=dict(
                    showgrid=False,
                    showline=False,
                    tickangle=45
                )
            )

            st.plotly_chart(fig_sessions, use_container_width=True)

        with col2_desc:
            st.write("")
            st.markdown("""
            <div style="background-color: #ffffff; padding: 20px; border-radius: 0px; box-shadow: 0 0 10px rgba(0,0,0,0.1);">
                <h5>📊 <strong>Com interpretar el gràfic</strong></h5>
                <p>• Les bombolles mostren el nombre de sessions per setmana, la mida de cada bombolla és proporcional al nombre de sessions.</p>
                <p>• A la part superior es mostra el nombre més freqüent de sessions setmanals (la moda) i la mitjana d'entrenaments de running.</p>
                <br>
            </div>
            """, unsafe_allow_html=True)

        st.divider()
        st.markdown("## Intensitat")
        
        st.markdown("""
        <div style="background-color: #ffffff; padding: 20px; border-radius: 0px; box-shadow: 0 0 10px rgba(0,0,0,0.1); margin-bottom: 20px;">
            <div style="display: flex; gap: 20px;">
                <div style="flex: 1;">
                    <h5>🔋 El grau d'esforç (intensitat) de cada sessió té un gran impacte en el tipus d'estímul que l'entrenament produeix al teu cos.</h5>
                </div>
                <div style="flex: 1;">
                    <h5>❗️ Per a la majoria de corredors la distribució recomanada és de <span style="background-color: #90EE90;">80% del volum a intensitat baixa</span> i <span style="background-color: #FFB6C1;">20% a intensitat alta</span>.</h5>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)
        st.warning(":warning: A la versió actual només es tenen compte activitats de carrera i senderisme per aquesta secció.")
        st.markdown("""
                    ##### Per estimar la intensitat dels teus entrenaments farem servir el ritme de la cursa rápida detectada dintre del període seleccionat o el que introdueixis manualment si prefereixes fer servir un diferent.
         """)
        
        with st.expander("*Com puc marcar una activitat com a cursa a Strava?*"):
            st.write("""
            Quan vagis a pujar la teva activitat (o l'editis), selecciona el primer desplegable sota 'Detalls' i canvia el tipus a 'Prueba' (per defecte està marcat com 'Entrenamiento').
            """)
            col1img, col2img = st.columns(2)
            with col1img:
                st.image(f"{current_dir}/assets/IMG_1238.jpg", width=300)
            with col2img:
                st.image(f"{current_dir}/assets/IMG_1237.jpg", width=300)
        # Get reference race speed (maximum speed from workout_type = 1)
        race_activities = df_filtered[df_filtered['workout_type'] == 1].sort_values('average_speed', ascending=False).head(1)
        
        # Initialize detected race variables
        race_pace_detected = None
        race_distance_detected = None

        # Format race activities for display if any exist
        if not race_activities.empty:
            races_display = race_activities[[
                'name', 'type', 'datetime_local', 'distance', 'moving_time', 'average_speed'
            ]].copy()
            
            # Format the columns
            races_display['datetime_local'] = races_display['datetime_local'].dt.strftime('%d/%m/%Y')
            races_display['distance'] = races_display['distance'].apply(lambda x: f"{int(x)} km")
            races_display['moving_time'] = races_display['moving_time'].apply(
                lambda x: f"{int(x//60)}:{int(x%60):02d}"
            )
            races_display['average_speed'] = races_display['average_speed'].apply(
                lambda x: f"{int((60/x))}:{int((60/x)%1 * 60):02d} min/km"
            )
            
            # Rename columns
            races_display.columns = ['Nom', 'Tipus', 'Data', 'Distància (km)', 'Temps (hh:min)', 'Ritme (min/km)']
            st.markdown("""
                        ##### Aquesta és la cursa amb ritme més alt detectada en el període:
                        """)
            st.dataframe(
                races_display,
                use_container_width=True,
                hide_index=True
            )
            # Assign detected values
            race_speed = race_activities['average_speed'].iloc[0]
            race_pace_detected = speed_to_pace(race_speed)
            race_distance_detected = race_activities['distance'].iloc[0]

            st.markdown("""
                        ##### Introdueix un altre ritme i distància d'una cursa anterior o un entrenament si ho prefereixes:
                        """)
        else:
            st.warning("No s'ha detectat cap cursa al periode seleccionat. Introdueix un ritme i distància de referència manualment:")


        # Manual entry section (always shown)
        with st.container(border=True):
            cols1, cols2, cols3 = st.columns(3)
            with cols1:
                race_minutes = st.number_input("Minuts:",step= 1, value=5, min_value=2, max_value=10, key="manual_min")
            with cols2:
                race_seconds = st.number_input("Segons:", step= 1, value=30, min_value=0, max_value=59, key="manual_sec")

            race_pace_manual = (race_minutes + race_seconds/60)
            race_speed_manual = round(pace_to_speed(race_pace_manual),2)
            with cols3:
                race_distance_manual = st.number_input("Distància (km):", step= 1, value=10, min_value=5, max_value=100, key="manual_dist")

        # Conditional radio button selection
        radio_options = []
        default_index = 0
        if race_pace_detected is not None:
            radio_options.append("Ritme detectat de cursa")
            radio_options.append("Ritme manual")
            # Keep default index 0 (detected) if available
        else:
            radio_options.append("Ritme manual")
            # default_index remains 0 (manual) as it's the only option

        selection = st.radio(
            'Selecciona el ritme de referència:',
            options=radio_options,
            index=default_index
        )

        # Assign final race pace and distance based on selection
        if selection == "Ritme detectat de cursa":
            # This option is only possible if race_pace_detected is not None
            race_distance = race_distance_detected
            race_pace = race_pace_detected
        else: # selection == "Ritme manual"
            race_distance = race_distance_manual
            race_pace = race_pace_manual

        # After creating df_filtered, add the pace column
        df_filtered['average_pace'] = df_filtered['average_speed'].apply(speed_to_pace)
        df_intensity, adjusted_reference_pace_str = add_intensity_index(df_filtered[df_filtered['sport'].isin(['Run', 'Hike'])], race_pace, race_distance)

        #st.dataframe(df_intensity[['datetime_local', 'average_pace', 'intensity_index', 'intensity_zone_pace', 'average_heartrate']])
        easy_percentage = compute_easy_percentage(df_intensity)
        st.markdown("""
        <div style="background-color: #ffffff; padding: 20px; border-radius: 0px; box-shadow: 0 0 10px rgba(0,0,0,0.1);">
            <div style="display: flex; gap: 20px;">
                <div style="flex: 1;">
                    <h5>⏱️ Amb el ritme introduït, l'aplicació calcula un ritme que seria una <span style="background-color: #FFD700;">estimació</span> del ritme que pots sostenir aproxiadament durant una hora.</h5>
                </div>
                <div style="flex: 1;">
                    <h5>🏃‍♂️ Aquesta estimació ens servirà per classificar els entrenaments en <span style="background-color: #2ecc71;">baixa</span>, <span style="background-color: #f1c40f;">mitjana</span> o <span style="background-color: #e74c3c;">alta</span> intensitat fent servir el seu ritme mitjà segons a quin percentatge d'aquest es trobin.</h5>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)
        st.write("")

        col1_int, col2_int = st.columns(2)
        with col1_int:
            st.markdown(f"""
                <div style="background-color: #ffffff; padding: 20px; border-radius: 0px; box-shadow: 0 0 10px rgba(0,0,0,0.1);">
                    <div style="font-size: 14px; font-family: 'Helvetica Neue', sans-serif; color: #666666;">Ritme llindar estimat</div>
                    <div style="font-size: 24px; font-weight: bold;">{adjusted_reference_pace_str}</div>
                </div>
            """, unsafe_allow_html=True)
        with col2_int:
            st.markdown(f"""
                <div style="background-color: #ffffff; padding: 20px; border-radius: 0px; box-shadow: 0 0 10px rgba(0,0,0,0.1);">
                    <div style="font-size: 14px; font-family: 'Helvetica Neue', sans-serif; color: #666666;">% de dies d'intensitat baixa</div>
                    <div style="font-size: 24px; font-weight: bold; color: {'#2ecc71' if easy_percentage >= 80 else '#f1c40f'};">{easy_percentage:.0f}%</div>
                </div>
            """, unsafe_allow_html=True)
        st.write("")
        st.dataframe(df_intensity)
        # Group by week and intensity zone to get counts
        intensity_by_week = df_intensity.groupby([
            df_intensity['datetime_local'].dt.isocalendar().year,
            df_intensity['datetime_local'].dt.isocalendar().week,
            'intensity_zone_pace'
        ]).size().reset_index()
        intensity_by_week.columns = ['Year', 'Week', 'Intensity', 'Count']

        # Add date column for x-axis labels
        intensity_by_week['Week_Start_Date'] = pd.to_datetime(intensity_by_week['Year'].astype(str) + '-' + 
                                                            intensity_by_week['Week'].astype(str) + '-1', 
                                                            format='%Y-%W-%w')
        
        # Format date with Catalan months
        intensity_by_week['Date_Label'] = intensity_by_week['Week_Start_Date'].dt.strftime('%d-%b-%Y')
        intensity_by_week['Date_Label'] = intensity_by_week['Date_Label'].apply(
            lambda x: x.replace(x[3:6], catalan_months[x[3:6]])
        )
        col1_int_chart, col2_int_desc = st.columns([0.7, 0.3])
        with col1_int_chart:
            # Create stacked bar chart
            fig_intensity = go.Figure()

            # Define colors for each intensity zone
            intensity_colors = {
                'Baixa': '#2ecc71',    # Green
                'Moderada': '#f1c40f', # Yellow
                'Alta': '#e74c3c'      # Red
            }

            # Add bars for each intensity zone
            for intensity in ['Baixa', 'Moderada', 'Alta']:
                mask = intensity_by_week['Intensity'] == intensity
                fig_intensity.add_trace(
                    go.Bar(
                        name=intensity,
                        x=intensity_by_week[mask]['Date_Label'].unique(),
                        y=intensity_by_week[mask]['Count'],
                        text=intensity_by_week[mask]['Count'],
                        textposition='auto',
                        marker_color=intensity_colors[intensity],
                        textfont=dict(
                            size=14,
                            color='white'
                        )
                    )
                )

            # Update layout
            fig_intensity.update_layout(
                title='Distribució de la intensitat: sessions per setmana',
                xaxis_title='Setmana',
                yaxis_title='Nombre de sessions',
                barmode='stack',
                plot_bgcolor='#fcfcfc',
                paper_bgcolor='#fcfcfc',
                showlegend=False,
                legend=dict(
                    yanchor="top",
                    y=0.99,
                    xanchor="left",
                    x=0.01
                ),
                xaxis=dict(
                    tickangle=45  # Rotate labels for better readability
                )
            )

            # Update axes
            fig_intensity.update_xaxes(
                showgrid=False,
                gridwidth=1,
                gridcolor='LightGray'        
            )
            fig_intensity.update_yaxes(
                showgrid=False,
                gridwidth=1,
                gridcolor='LightGray',
                zeroline=True,
                zerolinewidth=1,
                zerolinecolor='LightGray'
            )

            st.plotly_chart(fig_intensity, use_container_width=True)
            
        with col2_int_desc:
            st.markdown("""
                <div style="background-color: #ffffff; padding: 20px; border-radius: 0px; box-shadow: 0 0 10px rgba(0,0,0,0.1);">
                    <h5><strong>Com interpretar el gràfic</strong> 📊</h5>
                    <p>• Les barres mostren la distribució de sessions per setmana segons la seva intensitat.</p>
                    <p>• El color <span style="color: #2ecc71;">verd</span> indica baixa intensitat, <span style="color: #f1c40f;">groc</span> mitjana i <span style="color: #e74c3c;">vermell</span> alta intensitat.</p>
                    <p>• Per millorar a llarg termini, intenta mantenir una proporció aproximada del <span style="background-color: #2ecc71; color: white; padding: 2px 5px;">80%</span> de sessions a baixa intensitat i <span style="background-color: #e74c3c; color: white; padding: 2px 5px;">20%</span> a alta intensitat.</p>
                    <br>
                </div>
                """, unsafe_allow_html=True)

        st.divider()

        display_training_summary(weekly_distance, weekly_sessions, df_intensity)
        
    # After analysis is performed (e.g., after processing selected dates), add:
    if df is not None and st.session_state.form_submitted:
        log_user_session(
            st.session_state.athlete_id,
            'analysis_performed',
            {
                'date_range': [str(st.session_state.date_range[0]), str(st.session_state.date_range[1])],
                'activity_type': st.session_state.selected_activity_type,
                'activities_analyzed': len(df_filtered)
            }
        )

    # Add session state check for app exit
    if 'was_running' not in st.session_state:
        st.session_state.was_running = True
    elif st.session_state.was_running:
        # This will run when the script is about to stop
        log_user_session(
            athlete_id=st.session_state.get('athlete_id'),
            event_type='app_exit',
            event_data={'session_id': st.session_state.get('session_id')}
        )
        st.session_state.was_running = False


if __name__ == "__main__":
    main()
