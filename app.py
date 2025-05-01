import streamlit as st
import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timezone
import os
from dotenv import load_dotenv
from supabase import create_client, Client
import plotly.graph_objects as go
from scipy import stats
import time
from plotly.subplots import make_subplots
import pathlib
import uuid
from typing import Optional
import base64

st.set_page_config(
    page_title="Analitza el teu entrenament",
    page_icon=":running:",
    layout="centered",
    initial_sidebar_state="expanded"
)

# Load environment variables
load_dotenv()

# Initialize Supabase client
# Try to get from streamlit secrets first (for cloud deployment)
url: str = st.secrets.get("SUPABASE_URL")
key: str = st.secrets.get("SUPABASE_KEY")

current_dir = pathlib.Path(__file__).parent.resolve()

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

# Update the REDIRECT_URI logic
if 'REDIRECT_URI' in st.secrets:
    REDIRECT_URI = st.secrets['REDIRECT_URI']
else:
    REDIRECT_URI = "http://localhost:8501"  # Local development fallback

AUTH_URL = f"http://www.strava.com/oauth/authorize?client_id={STRAVA_CLIENT_ID}&response_type=code&redirect_uri={REDIRECT_URI}&scope=activity:read_all"

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

def get_stored_token(athlete_id):
    """Get stored token from Supabase"""
    response = supabase.table('strava_tokens').select('*').eq('athlete_id', athlete_id).execute()
    if response.data:
        return response.data[0]
    return None

def ensure_fresh_token():
    """Ensure we have a valid token"""
    if 'athlete_id' not in st.session_state:
        return None
        
    stored_token = get_stored_token(st.session_state.athlete_id)
    if not stored_token:
        return None
        
    # Check if token is expired or about to expire (within 5 minutes)
    expires_at = datetime.fromisoformat(stored_token['expires_at'].replace('Z', '+00:00'))
    if expires_at <= datetime.now(timezone.utc):
        # Token is expired, refresh it
        new_token = refresh_token(stored_token['refresh_token'])
        new_token['athlete_id'] = stored_token['athlete_id']

        if 'access_token' in new_token:
            save_token_to_supabase(new_token)
            return new_token['access_token']
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
    
    st.markdown(
    f"""
    ###### El ritme llindar estimat és {adjusted_reference_pace_str}.
    """
    )

    # Calculate intensity index
    df["intensity_index"] = df["average_pace"] / adjusted_reference_pace

    df["intensity_zone_pace"] = df["intensity_index"].apply(label_intensity)

    return df

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
    st.markdown("### **Resum del període**")
    
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
            st.markdown("#### Volum")
            if volume_analysis['pct_too_large'] > 50:
                st.warning("⚠️ Canvis setmanals massa grans")
                messages.append("- Intenta que els canvis setmanals siguin més suaus (±10%) per reduir el risc de lesió.")
            else:
                st.success("✅ Progressió gradual del volum")
                messages.append("- La progressió setmanal de volumn és adequada, al voltant del 10%.")
                
            if not volume_analysis['has_recovery']:
                st.warning("⚠️ No es detecten setmanes de recuperació")
                messages.append("- Considera incloure setmanes de recuperació (reduïr entre 20%-30% el volum) cada 3-4 setmanes.")
            elif volume_analysis['recovery_freq'] < 2:
                st.warning("⚠️ Falta de consistència del volum")
                messages.append("- El volum no incrementa gradualment, intenta mantenir un volum factible i pujar-lo gradualment.")
            elif volume_analysis['recovery_freq'] > 5:
                st.warning("⚠️ Falta de setmanes de recuperació")
                messages.append("- Considera fer setmanes de recuperació més sovint.")
            else:
                st.success("✅ Bona distribució de setmanes de recuperació") 
            
    with col2:
        with st.container(border=True, height=300):
            st.markdown("#### Freqüència")
            if frequency_analysis['cv'] < 25:
                st.success("✅ Freqüència consistent")
                messages.append(f"- Mantens una freqüència constant al voltant de {frequency_analysis['mode_sessions']} sessions/setmana.")
            else:
                st.warning("⚠️ Freqüència irregular")
                messages.append("- Intenta mantenir una freqüència més constant d'entrenaments.")
                  
    with col3:
        with st.container(border=True, height=300):
            st.markdown("#### Intensitat")
            if abs(intensity_analysis['deviation']) <= 10:
                st.success("✅ Bona distribució d'intensitat")
                messages.append("- La teva distribució d'intensitat s'apropa al 80 (baixa) / 20 (mitja-alta) recomanat.")
            else:
                if intensity_analysis['easy_percentage'] < 70:
                    st.warning("⚠️ Massa sessions intenses")
                    messages.append("- Considera fer més sessions a baixa intensitat.")
                else:
                    st.warning("⚠️ Distribució d'intensitat desequilibrada")
                    messages.append("- Intenta ajustar la distribució d'intensitats al 80 (baixa) / 20 (mitja-alta).")

    # Display all messages together below the columns
    st.markdown("#### Recomanacions:")
    st.markdown("\n".join(messages))

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

def main():
    # Log app open at the start of main with athlete_id=0 if not authenticated
    log_user_session(
        athlete_id=st.session_state.get('athlete_id', 0),  # Default to 0 if not authenticated
        event_type='app_open',
        event_data={'session_id': st.session_state.get('session_id')}
    )

    with st.sidebar:
        """
        Em pots contactar per mail o Strava amb qualsevol dubte o suggerència que tinguis.
        """
        col1sb, col2sb = st.columns(2)
        
        with col1sb:
            st.markdown("""
                <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
                <div style="display: flex; justify-content: center;">
                    <a href="https://www.strava.com/athletes/65375118" target="_blank" style="color: #FC4C02; text-decoration: none;">
                        <i class="fab fa-strava fa-2x"></i>
                    </a>
                </div>
            """, unsafe_allow_html=True)
        
        with col2sb:
            st.markdown("""
                <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
                <div style="display: flex; justify-content: center;">
                    <a href="mailto:fernandosanchezmp@gmail.com" style="color:rgb(210, 195, 194); text-decoration: none;">
                        <i class="fas fa-envelope fa-2x"></i>
                    </a>
                </div>
            """, unsafe_allow_html=True)

        st.write("")
        st.divider()
        st.markdown(
            """<svg width='180' height='30' viewBox='0 0 365 37' fill='none' xmlns='http://www.w3.org/2000/svg'>
                <path d='M0.905029 35.1577H3.31523V29.0503H7.34003C10.8266 29.0503 12.8858 27.1315 12.8858 23.9257C12.8858 20.7433 10.8266 18.7777 7.36343 18.7777H0.905029V35.1577ZM3.31523 26.8039V21.0241H7.26983C9.42263 21.0241 10.4756 21.9601 10.4756 23.9257C10.4756 25.8445 9.39923 26.8039 7.24643 26.8039H3.31523ZM23.6787 35.5087C27.8907 35.5087 30.6753 32.1157 30.6753 26.9677C30.6753 21.8197 27.8907 18.4267 23.6787 18.4267C19.4667 18.4267 16.7055 21.8197 16.7055 26.9677C16.7055 32.1157 19.4667 35.5087 23.6787 35.5087ZM23.6787 33.2623C20.8473 33.2623 19.1157 30.8755 19.1157 26.9677C19.1157 23.0599 20.8473 20.6731 23.6787 20.6731C26.5335 20.6731 28.2651 23.0599 28.2651 26.9677C28.2651 30.8755 26.5335 33.2623 23.6787 33.2623ZM37.4562 35.1577H40.2174L43.9848 22.5919H44.0316L47.7756 35.1577H50.5602L53.6958 18.7777H51.2388L48.8988 31.3201H48.852L45.1314 18.7777H42.8616L39.1644 31.3201H39.1176L36.7776 18.7777H34.2972L37.4562 35.1577ZM58.3356 35.1577H68.3742V32.9113H60.7458V27.8101H67.719V25.6573H60.7458V21.0241H68.3742V18.7777H58.3356V35.1577ZM81.6314 28.5823C84.0416 28.0441 85.469 26.2891 85.469 23.7619C85.469 20.6497 83.3864 18.7777 79.97 18.7777H73.7924V35.1577H76.2026V28.7695H79.0808L82.4504 35.1577H85.1648L81.6314 28.6057V28.5823ZM76.2026 26.5465V21.0241H79.736C81.9356 21.0241 83.0588 21.9367 83.0588 23.7619C83.0588 25.5871 81.9122 26.5465 79.736 26.5465H76.2026ZM90.4832 35.1577H100.522V32.9113H92.8934V27.8101H99.8666V25.6573H92.8934V21.0241H100.522V18.7777H90.4832V35.1577ZM105.94 35.1577H111.111C115.417 35.1577 118.295 32.5369 118.295 26.9443C118.295 21.7963 115.417 18.7777 111.205 18.7777H105.94V35.1577ZM108.35 32.9113V21.0241H111.135C114.036 21.0241 115.885 23.0131 115.885 26.9443C115.885 31.2031 114.036 32.9113 111.018 32.9113H108.35ZM132.055 35.1577H138.115C142 35.1577 144.012 33.4729 144.012 30.2437C144.012 28.3951 143.053 26.9677 141.579 26.3827V26.3359C142.702 25.7743 143.404 24.5809 143.404 23.0599C143.404 20.5093 141.462 18.7777 138.607 18.7777H132.055V35.1577ZM134.465 25.5871V21.0007H138.209C140.034 21.0007 140.994 21.7729 140.994 23.2705C140.994 24.8149 140.081 25.5871 138.209 25.5871H134.465ZM134.465 32.9347V27.6229H138.162C140.572 27.6229 141.602 28.4185 141.602 30.2203C141.602 32.1157 140.549 32.9347 138.115 32.9347H134.465ZM151.963 35.1577H154.373V28.3717L160.012 18.7777H157.321L153.18 26.1253H153.133L148.991 18.7777H146.3L151.963 28.3717V35.1577Z' fill='black'/>
                <path fill-rule='evenodd' clip-rule='evenodd' d='M275.925 35.6008L275.923 35.5993H286.002L292.193 23.1514L298.383 35.5993H310.63L292.191 0L274.689 33.7968L267.969 23.9845C272.118 21.9836 274.704 18.5181 274.704 13.54V13.442C274.704 9.92849 273.631 7.39027 271.581 5.34009C269.189 2.94868 265.334 1.43574 259.282 1.43574H242.59V35.6008H254.011V25.8391H256.451L262.893 35.6008H275.925ZM346.353 0L327.917 35.5993H340.164L346.354 23.1514L352.545 35.5993H364.791L346.353 0ZM319.283 37L337.719 1.40071H325.473L319.282 13.8486L313.091 1.40071H300.845L319.283 37ZM258.94 17.6885C261.673 17.6885 263.333 16.4684 263.333 14.3698V14.2718C263.333 12.0756 261.624 11.0019 258.989 11.0019H254.01V17.6885H258.94ZM218.165 11.0994H208.112V1.43574H239.64V11.0994H229.587V35.6008H218.165V11.0994ZM180.282 23.2037L174.181 30.476C178.525 34.2835 184.772 36.2353 191.703 36.2353C200.879 36.2353 206.784 31.8425 206.784 24.6675V24.5703C206.784 17.6885 200.928 15.1502 192.191 13.5401C188.579 12.856 187.652 12.2712 187.652 11.3435V11.2459C187.652 10.4162 188.433 9.83056 190.141 9.83056C193.313 9.83056 197.17 10.8554 200.39 13.1981L205.955 5.48697C202.001 2.36309 197.121 0.800959 190.532 0.800959C181.111 0.800959 176.036 5.8286 176.036 12.3196V12.4176C176.036 19.6406 182.772 21.8376 190.434 23.3986C194.095 24.131 195.168 24.6675 195.168 25.644V25.742C195.168 26.6689 194.29 27.2053 192.24 27.2053C188.238 27.2053 183.992 26.0348 180.282 23.2037Z' fill='#FC5200'/>
            </svg>""", 
            unsafe_allow_html=True
        )
        st.write("")
        """ 
        Fent servir l'aplicació acceptes la  [Política de privacitat](https://github.com/fsanchezmonge/strava-improvement/blob/main/privacy_policy.md)
        """

    st.title("Analitza el teu entrenament!:running::chart_with_upwards_trend:")
    """    
    Benvingut! Aquesta aplicació et pot ajudar a revisar com has entrenat per preparar una cursa i aprendre alguns conceptes bàsics per millorar en el futur.

    Algunes consideracions per fer servir-la:
    - Selecciona un període d'entre 4 setmanes i 2-3 mesos per poder captar canvis i tendències significatives, on l'últim dia seleccionatés el de la cursa que vols analitzar.
    - Per ara, només es tenen en compte les activitats de running i trail.
    - Hi ha certs factors com l'estrès personal, historial esportiu, i sensacions subjectives que no es poden tenir en compte amb les dades disponibles però que afecten a l'entrenament i el rendiment.

    L'aplicació es divideix en tres seccions: **volum**, **freqüència** i **intensitat**, que són tres pilars bàsics que podem modificar per millorar.
    """
    df = None
    with st.container(border=True):
        """
        1. Connecta el teu perfil d'Strava. Fes click al botó i autoritza l'accés a les dades del teu perfil.

        """
        # Initialize session state
        if 'access_token' not in st.session_state:
            st.session_state.access_token = None
        if 'athlete_id' not in st.session_state:
            st.session_state.athlete_id = None

        # Check for authorization code in URL
        query_params = st.query_params
        
        if 'code' in query_params:
            code = query_params.get("code", [])
            # Log authorization start
            log_user_session(
                athlete_id=0,  # Use 0 instead of None for unauthenticated users
                event_type='auth_start',
                event_data={'auth_code_present': True}
            )
            
            with st.spinner('Connectant amb Strava...'):
                try:
                    token_response = get_token(code)
                    if 'access_token' in token_response:
                        st.session_state.access_token = token_response['access_token']
                        st.session_state.athlete_id = token_response['athlete']['id']
                        save_token_to_supabase(token_response)
                        # Log successful authorization
                        log_user_session(
                            athlete_id=token_response['athlete']['id'],
                            event_type='auth_success',
                            event_data={'athlete_id': token_response['athlete']['id']}
                        )
                        st.query_params.clear()
                        st.rerun()
                    else:
                        # Log failed authorization
                        log_user_session(
                            athlete_id=None,
                            event_type='auth_failed',
                            event_data={'error': token_response.get('error', 'Unknown error')}
                        )
                        st.error(f"Error en la connexió: {token_response.get('error', 'Error desconegut')}")
                except Exception as e:
                    # Log authorization error
                    log_user_session(
                        athlete_id=None,
                        event_type='auth_error',
                        event_data={'error': str(e)}
                    )
                    st.error(f"Error durant la connexió: {str(e)}")
        
        # Try to get stored token if we don't have one in session
        if st.session_state.access_token is None and st.session_state.athlete_id is not None:
            # Try to get a fresh token for this athlete
            fresh_token = ensure_fresh_token()
            if fresh_token:
                st.session_state.access_token = fresh_token
                st.rerun()

        if st.session_state.access_token is None:
            col7, col8, col9 = st.columns(3)
            with col8:
                # Path to your SVG file
                svg_path = f"{current_dir}/assets/strava_button.svg"
                # Read and encode the SVG as base64
                with open(svg_path, "rb") as f:
                    svg_data = f.read()
                    b64_svg = base64.b64encode(svg_data).decode("utf-8")
                # Create a data URI for the SVG
                svg_uri = f"data:image/svg+xml;base64,{b64_svg}"
                # Render the clickable button
                st.markdown(f"""
                <style>
                .strava-button {{
                    display: inline-block;
                    cursor: pointer;
                    transition: transform 0.2s;
                }}
                .strava-button:hover {{
                    transform: scale(1.02);
                }}
                </style>
                <div class="strava-button">
                    <a href="{AUTH_URL}">
                        <img src="{svg_uri}" width="193" height="48" alt="Connect with Strava"/>
                    </a>
                </div>
                """, unsafe_allow_html=True)
                st.write("")

        else:
            st.write("")
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
                st.success("Activitats carregades!")
            else:
                # Log failed data load
                log_user_session(
                    st.session_state.athlete_id,
                    'data_load_failed'
                )
                st.warning("No s'han trobat activitats.")
    
        # Save to Supabase
        #save_activities_to_supabase(activities, st.session_state.athlete_id)
    if df is not None:
        # Add these session state initializations
        if 'date_range' not in st.session_state:
            st.session_state.date_range = (
                pd.to_datetime('now').date() - pd.DateOffset(days=60),
                pd.to_datetime('now').date()
            )
        if 'selected_activity_type' not in st.session_state:
            st.session_state.selected_activity_type = "Totes"

        with st.container(border=True):
            """
            2. Selecciona el període que vols analitzar i el tipus d'activitat (opcional):
            """
            # Modify the form to update session state
            with st.form("date_selection_form", border=False):
                col1, col2 = st.columns(2)
                with col1:
                    selected_dates = st.date_input(
                        "",
                        value=st.session_state.date_range,
                        min_value=pd.to_datetime(df['datetime_local'].min()).date(),
                        max_value=pd.to_datetime('now').date(),
                        label_visibility="collapsed"
                    )
                with col2:
                    running_types = df[df['sport'] == 'Run']['type'].unique().tolist()
                    running_types.insert(0, "Totes")
                    
                    selected_type = st.selectbox(
                        "Selecciona el tipus de cursa:",
                        options=running_types,
                        label_visibility="collapsed",
                        key="activity_type_select",
                        index=running_types.index(st.session_state.selected_activity_type)
                    )
                submit_dates = st.form_submit_button("Guardar")
                
                if submit_dates:
                    st.session_state.date_range = selected_dates
                    st.session_state.selected_activity_type = selected_type

            # Check for valid session state instead of form submission
            if 'date_range' not in st.session_state:
                st.stop()

            # Use session state values for filtering
            df['datetime_local'] = pd.to_datetime(df['datetime_local'])
            
            if st.session_state.selected_activity_type == "Totes":
                mask = (
                    (df['datetime_local'].dt.date >= pd.to_datetime(st.session_state.date_range[0]).date()) & 
                    (df['datetime_local'].dt.date <= pd.to_datetime(st.session_state.date_range[1]).date()) & 
                    (df['sport'] == 'Run')
                )
            else:
                mask = (
                    (df['datetime_local'].dt.date >= pd.to_datetime(st.session_state.date_range[0]).date()) & 
                    (df['datetime_local'].dt.date <= pd.to_datetime(st.session_state.date_range[1]).date()) & 
                    (df['sport'] == 'Run') &
                    (df['type'] == st.session_state.selected_activity_type)
                )
            df_filtered = df[mask]

        st.divider()     
        """
        ### **Volum**
        **Incrementar gradualment** i **ser consistent** amb el volum setmanal és un molt bon signe de millora del nivell de forma. Una norma general és estar al voltant del **10% de variació setmanal**.

        Si entrenes per muntanya, pot ser important combinar distància amb temps per tenir en compte la desigualtat del terreny i el desnivell.
        """
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

        with tab1:
            # Create the distance bar chart
            fig_distance = go.Figure()
            mean_distance = weekly_distance['Distance'].mean()

            
            # Add main bars
            fig_distance.add_trace(
                go.Bar(
                    x=weekly_distance['Week_Label'],
                    y=weekly_distance['Distance'],
                    text=weekly_distance['Distance'].round(1),
                    textposition='auto',
                )
            )

            # Add horizontal line for mean distance
            fig_distance.add_hline(
                y=mean_distance,
                line_dash="dash",
                line_color="gray",
                annotation_text=f"{mean_distance:.1f} km",
                annotation_position="top right"
            )
            
            # Add percentage change labels for distance chart
            fig_distance.add_trace(
                go.Scatter(
                    x=weekly_distance['Week_Label'],
                    y=weekly_distance['Distance'],
                    text=weekly_distance['Distance_pct'].apply(
                        lambda x: f"{x:+.0f}%" if pd.notnull(x) else ""
                    ),
                    textposition='top center',
                    mode='text',
                    showlegend=False,
                    textfont=dict(
                        color=weekly_distance['Distance_pct'].apply(
                            lambda x: '#DAA520' if pd.notnull(x) and (x > 10 or x < -10) else 'green'  # Changed color logic
                        )
                    )
                )
            )
            
            # Update layout
            fig_distance.update_layout(
                title='Distància setmanal (km)',
                xaxis_title='Setmana',
                yaxis_title='Distància (km)',
                showlegend=False,
                plot_bgcolor='white'
            )
            
            # Update axes
            fig_distance.update_xaxes(
                showgrid=False,
                gridwidth=1,
                gridcolor='LightGray'
            )
            fig_distance.update_yaxes(
                showgrid=False,
                gridwidth=1,
                gridcolor='LightGray',
                zeroline=True,
                zerolinewidth=1,
                zerolinecolor='LightGray'
            )
            
            st.plotly_chart(fig_distance, use_container_width=True)

        with tab2:
            # Convert minutes to hours for better readability
            weekly_distance['Time'] = weekly_distance['Time'] / 60  # Convert to hours
            mean_time = weekly_distance['Time'].mean()

            # Create the time bar chart
            fig_time = go.Figure()
            
            # Add main bars
            fig_time.add_trace(
                go.Bar(
                    x=weekly_distance['Week_Label'],
                    y=weekly_distance['Time'],
                    text=weekly_distance['Time'].round(1),
                    textposition='auto',
                )
            )

            # Add horizontal line for mean time
            fig_time.add_hline(
                y=mean_time,
                line_dash="dash",
                line_color="gray",
                annotation_text=f"{mean_time:.1f} h",
                annotation_position="top right"
            )
            
            # Add percentage change labels
            fig_time.add_trace(
                go.Scatter(
                    x=weekly_distance['Week_Label'],
                    y=weekly_distance['Time'],
                    text=weekly_distance['Time_pct'].apply(
                        lambda x: f"{x:+.0f}%" if pd.notnull(x) else ""
                    ),
                    textposition='top center',
                    mode='text',
                    showlegend=False,
                    textfont=dict(
                        color=weekly_distance['Time_pct'].apply(
                            lambda x: '#DAA520' if pd.notnull(x) and (x > 10 or x < -10) else 'green'  # Changed to goldenrod color
                        )
                    )
                )
            )
            
            # Update layout
            fig_time.update_layout(
                title='Temps setmanal (hores)',
                xaxis_title='Setmana',
                yaxis_title='Temps (h)',
                showlegend=False,
                plot_bgcolor='white'
            )
            
            # Update axes
            fig_time.update_xaxes(
                showgrid=False,
                gridwidth=1,
                gridcolor='LightGray'
            )
            fig_time.update_yaxes(
                showgrid=False,
                gridwidth=1,
                gridcolor='LightGray',
                zeroline=True,
                zerolinewidth=1,
                zerolinecolor='LightGray'
            )
            
            st.plotly_chart(fig_time, use_container_width=True)

        """        
        ##### Sortides llargues

        Com de llarga ha de ser la teva sortida llarga dependrà del teu nivell i objectiu, però el més important és **començar amb una distància que et permeti progressar setmana a setmana** sense impactar excessivament en la resta de sessions. 
        
        Una forma de comprovar això és mantenir la distància d'aquesta sortida entre el 30% i el 40% del total setmanal (ho pots veure al gràfic de sota).

        Incrementar la distància setmana a setmana amb ritmes semblants és un bon indicador de que estàs millorant.
        """
        
        # Get longest activity per week and weekly totals
        weekly_totals = df_filtered.groupby([
            df_filtered['datetime_local'].dt.isocalendar().year,
            df_filtered['datetime_local'].dt.isocalendar().week
        ])['distance'].sum().reset_index()
        weekly_totals.columns = ['year', 'week', 'weekly_total']
        
        longest_runs = df_filtered.groupby([
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

        # Sort by datetime first (while it's still in datetime format)
        longest_runs_display = longest_runs_display.sort_values('datetime_local', ascending=False)

        # Format display columns (except percentage)
        longest_runs_display['datetime_local'] = longest_runs_display['datetime_local'].dt.strftime('%d/%m/%Y')
        longest_runs_display['moving_time'] = longest_runs_display['moving_time'].apply(
            lambda x: f"{int(x//60)}h{int(x%60)}min" if x >= 60 else f"{int(x)}min"
        )
        longest_runs_display['distance'] = longest_runs_display['distance'].apply(lambda x: f"{x:.1f} km")
        longest_runs_display['average_speed'] = longest_runs_display['average_speed'].apply(
            lambda x: f"{int((60/x))}:{int((60/x)%1 * 60):02d} min/km" if pd.notna(x) and x > 0 else "-"
        )
        # The 'percentage' column is still numeric here

        # Rename columns for final display
        longest_runs_display.columns = ['Data', 'Nom', 'Distància', 'Temps', 'Ritme', '% del total']

        # Define styling function for percentage background
        def style_percentage_background(val):
            if pd.isna(val):
                return '' # No style for NaN
            elif 30 <= val <= 40:
                return 'background-color: lightgreen'
            else:
                return 'background-color: #FFFFE0' # Light Yellow hex

        # Display the styled dataframe using Styler
        # This replaces the previous st.dataframe call and the code dropping 'numeric_percentage'
        st.write("**Sessió més llarga per setmana i % del total de distància setmanal**")
        st.dataframe(
            longest_runs_display.style.apply(
                lambda col: col.map(style_percentage_background), # Apply style based on numeric value
                subset=['% del total']
            ).format(
                # Format to string *after* applying style based on number
                {'% del total': lambda x: f"{x:.1f}%" if pd.notna(x) else "-"}
            ),
            use_container_width=True,
            hide_index=True
        )

        # Create line chart for longest runs with weekly distance bars
        fig_longest = go.Figure()
        
        # Format x-axis dates to show only week numbers
        longest_runs['year_week'] = longest_runs['datetime_local'].dt.strftime('W%V')  # Changed from '%Y-W%V'
        weekly_totals['year_week'] = 'W' + weekly_totals['week'].astype(str).str.zfill(2)  # Changed from year-week format
        
        # Add weekly distance bars
        fig_longest.add_trace(
            go.Bar(
                x=weekly_totals['year_week'],
                y=weekly_totals['weekly_total'],
                name='Distància setmanal',
                marker_color='lightgray',
                opacity=0.6,
                hovertemplate='Setmana: %{x}<br>Distància total: %{y:.1f} km<extra></extra>'
            )
        )
        
        # Add longest run line
        fig_longest.add_trace(
            go.Scatter(
                x=longest_runs['year_week'],
                y=longest_runs['distance'],
                mode='lines+markers+text',
                name='Sortida més llarga',
                text=longest_runs['distance'].round(1),
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
            plot_bgcolor='white',
            yaxis=dict(
                range=[0, max(longest_runs['distance'].max(), weekly_totals['weekly_total'].max()) * 1.2]
            ),
            legend=dict(
                yanchor="top",
                y=0.99,
                xanchor="left",
                x=0.01
            )
        )

        # Update axes
        fig_longest.update_xaxes(
            showgrid=False,
            gridwidth=1,
            gridcolor='LightGray'
        )
        fig_longest.update_yaxes(
            showgrid=True,
            gridwidth=1,
            gridcolor='LightGray',
            zeroline=True,
            zerolinewidth=1,
            zerolinecolor='LightGray'
        )

        st.plotly_chart(fig_longest, use_container_width=True)
        
        """
        ### **Freqüència**
        Una major freqüència d'entrenament pot ser beneficiosa perquè produeix estímuls més constants i **distribueix millor la fatiga**, evitant sessions amb càrrega excessiva.

        Busca la freqüència que et permeti **ser consistent** i trobar l'organització per **entrenar de forma continuada en el temps**.
        """
        
        # Count sessions per week
        weekly_sessions = df_filtered.groupby([
            df_filtered['datetime_local'].dt.isocalendar().year,
            df_filtered['datetime_local'].dt.isocalendar().week
        ]).size().reset_index()
        weekly_sessions.columns = ['Year', 'Week', 'Sessions']

        # Create a combined year-week label for x-axis
        weekly_sessions['Week_Label'] = weekly_sessions.apply(lambda x: f"S{int(x['Week']):02d}", axis=1)

        # Add metric for mode sessions (changed from median)
        mode_sessions = weekly_sessions['Sessions'].mode()[0]  # [0] because mode can return multiple values
        st.metric("Num. de sessions més freqüent", f"{mode_sessions:.0f}")
        
        # Create the sessions bar chart
        fig_sessions = go.Figure(data=[
            go.Bar(
                x=weekly_sessions['Week_Label'],
                y=weekly_sessions['Sessions'],
                text=weekly_sessions['Sessions'],
                textposition='auto',
            )
        ])
        
        # Update layout
        fig_sessions.update_layout(
            title='Sessions per setmana',
            xaxis_title='Setmana',
            yaxis_title='Nombre de sessions',
            showlegend=False,
            plot_bgcolor='white'
        )
        
        # Update axes
        fig_sessions.update_xaxes(
            showgrid=False,
            gridwidth=1,
            gridcolor='LightGray'
        )
        fig_sessions.update_yaxes(
            showgrid=False,
            gridwidth=1,
            gridcolor='LightGray',
            zeroline=True,
            zerolinewidth=1,
            zerolinecolor='LightGray'
        )
        
        st.plotly_chart(fig_sessions, use_container_width=True)

        """
        ### **Intensitat**

        Per estimar la intensitat dels teus entrenaments, farem servir el ritme de la cursa amb el ritme més alt dintre del període seleccionat o el que introdueixis manualment.
        """      
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
            
            st.write("Aquesta és la cursa amb ritme més alt detectada:")
            st.dataframe(
                races_display,
                use_container_width=True,
                hide_index=True
            )
            # Assign detected values
            race_speed = race_activities['average_speed'].iloc[0]
            race_pace_detected = speed_to_pace(race_speed)
            race_distance_detected = race_activities['distance'].iloc[0]

            st.write("O introueix un altre ritme i distància d'una cursa anterior o un entrenament:")
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
            "Selecciona el ritme de referència:",
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

        """
        A partir d'aquest ritme ajustat a la distància, calcularem un llindar que farem servir per classificar cada entrenament en baixa, mitjana o alta intensitat en funció del ritme de cada un d'ells.
        
        Aquest llindar, hauria de ser l'equivalent al ritme màxim mig que podries mantenir durant 1 hora.
        """          
                # After creating df_filtered, add the pace column
        df_filtered['average_pace'] = df_filtered['average_speed'].apply(speed_to_pace)
        df_intensity = add_intensity_index(df_filtered, race_pace, race_distance)
        #st.dataframe(df_intensity[['datetime_local', 'average_pace', 'intensity_index', 'intensity_zone_pace', 'average_heartrate']])
        easy_percentage = compute_easy_percentage(df_intensity)
        st.metric("% de sessions amb intensitat baixa", f"{easy_percentage:.1f}%",help="La distribució que funciona millor per a la majoria de corredors és **80% de dies de baixa intensitat** i **20% de dies de moderada o alta intensitat**.")

        # Group by week and intensity zone to get counts
        intensity_by_week = df_intensity.groupby([
            df_intensity['datetime_local'].dt.isocalendar().year,
            df_intensity['datetime_local'].dt.isocalendar().week,
            'intensity_zone_pace'
        ]).size().reset_index()
        intensity_by_week.columns = ['Year', 'Week', 'Intensity', 'Count']

        # Create week labels
        intensity_by_week['Week_Label'] = intensity_by_week.apply(lambda x: f"S{int(x['Week']):02d}", axis=1)

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
                    x=intensity_by_week[mask]['Week_Label'].unique(),
                    y=intensity_by_week[mask]['Count'],
                    text=intensity_by_week[mask]['Count'],
                    textposition='auto',
                    marker_color=intensity_colors[intensity]  # Set color for each intensity
                )
            )

        # Update layout
        fig_intensity.update_layout(
            title='Distribució de la intensitat per setmana',
            xaxis_title='Setmana',
            yaxis_title='Nombre de sessions',
            barmode='stack',
            plot_bgcolor='white',
            showlegend=False,
            legend=dict(
                yanchor="top",
                y=0.99,
                xanchor="left",
                x=0.01
            )
        )

        # Update axes
        fig_intensity.update_xaxes(
            showgrid=False,
            gridwidth=1,
            gridcolor='LightGray'
        )
        fig_intensity.update_yaxes(
            showgrid=True,
            gridwidth=1,
            gridcolor='LightGray',
            zeroline=True,
            zerolinewidth=1,
            zerolinecolor='LightGray'
        )

        st.plotly_chart(fig_intensity, use_container_width=True)

        st.divider()

        display_training_summary(weekly_distance, weekly_sessions, df_intensity)
        
        st.divider()

        st.markdown("""
        ### Referències
        - [Training for a (half-)marathon: Training volume and longest endurance run related to performance and running injuries](https://pubmed.ncbi.nlm.nih.gov/32421886/)
        - [Does Training Frequency Matter for Fitness Gains?](https://www.physiologicallyspeaking.com/p/physiology-friday-257-does-training?utm_source=post-email-title&publication_id=549308&post_id=157136370&utm_campaign=email-post-title&isFreemail=true&r=1kkul7&triedRedirect=true&utm_medium=email)
        - [Estimating running performance](https://medium.com/@altini_marco/estimating-running-performance-890c303aa7ce)
        - [Endurance Training - Science and Practice (2nd Edition)](https://www.inigomujika.com/libros/endurance-training-science-and-practice-2-edicion/)
        - [Training for the Uphill Athlete](https://uphillathlete.com/product/training-for-the-uphill-athlete-book/)
        - [The truth about long runs](https://youtu.be/Qcnlhzw0dQY?si=HatCwe94pM9Qb7Ld)
        """)

    # After analysis is performed (e.g., after processing selected dates), add:
    if df is not None and len(selected_dates) == 2:
        log_user_session(
            st.session_state.athlete_id,
            'analysis_performed',
            {
                'date_range': [str(selected_dates[0]), str(selected_dates[1])],
                'activity_type': selected_type,
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
    # Generate a unique session ID when the app starts
    if 'session_id' not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())
    main()
