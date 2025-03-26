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

st.set_page_config(
    page_title="100 milles app",
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

REDIRECT_URI = st.secrets.get("REDIRECT_URI", "http://localhost:8501")  # Use configured URL or default to local
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

@st.cache_data(show_spinner="S'estan carregant les teves activitiats...")
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

def main():
    with st.sidebar:
        """
        Benvinguts!

        Escriu-me per xarxes o envia'm un mail amb qualsevol dubte o suggerència que tinguis.
        """
        col1sb, col2sb, col3sb = st.columns(3)
        
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
                    <a href="https://twitter.com/@fsanchez_mp" target="_blank" style="color: #1DA1F2; text-decoration: none;">
                        <i class="fab fa-twitter fa-2x"></i>
                    </a>
                </div>
            """, unsafe_allow_html=True)
        
        with col3sb:
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
    Si t'estàs preparant o has preparat una cursa recentment és important revisar que el teu entrenament compleixi uns principis bàsics i provoqui adaptacions que acabin fent millorar el teu rendiment.
    
    Hi ha tres 'palanques' bàsiques que podem modificar per desencadenar aquestes adaptacions: **volum** (o durada), **freqüencia** i **intensitat**.
    L'aplicació mostra cada una d'aquestes parts per separat.

    Recorda que hi ha factors com l'estrès personal, historial esportiu... que també aftecten a l'estat de forma i no es poden quantificar fàcilment.
    """
    df = None
    with st.container(border=True):
        """
        1. Conecta el teu perfil d'Strava. Fes click al botó i autoritza l'accés a les dades del teu perfil.

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
            with st.spinner('Connectant amb Strava...'):
                try:
                    token_response = get_token(code)
                    if 'access_token' in token_response:
                        st.session_state.access_token = token_response['access_token']
                        st.session_state.athlete_id = token_response['athlete']['id']
                        save_token_to_supabase(token_response)
                        st.query_params.clear()
                        st.rerun()
                    else:
                        st.error(f"Error en la connexió: {token_response.get('error', 'Error desconegut')}")
                except Exception as e:
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
                strava_svg = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
                    <svg width="193px" height="48px" viewBox="0 0 193 48" version="1.1" xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">
                        <!-- Generator: Sketch 39.1 (31720) - http://www.bohemiancoding.com/sketch -->
                        <title>btn_strava_connectwith_orange</title>
                        <desc>Created with Sketch.</desc>
                        <defs></defs>
                        <g id="Strava-Button_outlined" stroke="none" stroke-width="1" fill="none" fill-rule="evenodd">
                            <g id="btn_strava_connectwith_orange">
                                <rect id="button-bg" fill="#FC4C02" x="4" y="4" width="185" height="40" rx="2"></rect>
                                <path d="M27,25.164 L28.736,25.514 C28.6239994,26.6153388 28.2226701,27.5066633 27.532,28.188 C26.8413299,28.8693367 25.9500055,29.21 24.858,29.21 C23.6166605,29.21 22.6016706,28.7760043 21.813,27.908 C21.0243294,27.0399957 20.63,25.7426753 20.63,24.016 C20.63,22.4106586 21.0429959,21.171671 21.869,20.299 C22.6950041,19.426329 23.6866609,18.99 24.844,18.99 C25.8613384,18.99 26.7199965,19.3096635 27.42,19.949 C28.1200035,20.5883365 28.5306661,21.4306614 28.652,22.476 L26.944,22.742 C26.7013321,21.2579926 26.0060057,20.516 24.858,20.516 C24.1393297,20.516 23.5396691,20.8053304 23.059,21.384 C22.5783309,21.9626696 22.338,22.8493274 22.338,24.044 C22.338,25.2666728 22.5736643,26.178997 23.045,26.781 C23.5163357,27.383003 24.120663,27.684 24.858,27.684 C26.0806728,27.684 26.7946656,26.8440084 27,25.164 L27,25.164 Z M33.51875,27.768 C34.0694194,27.768 34.5150816,27.5510022 34.85575,27.117 C35.1964184,26.6829978 35.36675,26.0740039 35.36675,25.29 C35.36675,24.5059961 35.1964184,23.8970022 34.85575,23.463 C34.5150816,23.0289978 34.0694194,22.812 33.51875,22.812 C32.9587472,22.812 32.5084184,23.0266645 32.16775,23.456 C31.8270816,23.8853355 31.65675,24.4966627 31.65675,25.29 C31.65675,26.0926707 31.8270816,26.7063312 32.16775,27.131 C32.5084184,27.5556688 32.9587472,27.768 33.51875,27.768 L33.51875,27.768 Z M33.51875,29.21 C32.5200783,29.21 31.6964199,28.8646701 31.04775,28.174 C30.3990801,27.4833299 30.07475,26.5220062 30.07475,25.29 C30.07475,24.0766606 30.4084133,23.1200035 31.07575,22.42 C31.7430867,21.7199965 32.5574119,21.37 33.51875,21.37 C34.4800881,21.37 35.2897467,21.7199965 35.94775,22.42 C36.6057533,23.1200035 36.93475,24.0766606 36.93475,25.29 C36.93475,26.5220062 36.6150865,27.4833299 35.97575,28.174 C35.3364135,28.8646701 34.5174217,29.21 33.51875,29.21 L33.51875,29.21 Z M38.7635,29 L38.7635,21.58 L40.3035,21.58 L40.3035,22.294 L40.3315,22.294 C40.5368344,22.0326654 40.8214982,21.8133342 41.1855,21.636 C41.5495018,21.4586658 41.9321647,21.37 42.3335,21.37 C43.1548374,21.37 43.8011643,21.6149976 44.2725,22.105 C44.7438357,22.5950024 44.9795,23.2739957 44.9795,24.142 L44.9795,29 L43.3975,29 L43.3975,24.562 C43.3975,23.4139943 42.9168381,22.84 41.9555,22.84 C41.4701642,22.84 41.0828348,22.9799986 40.7935,23.26 C40.5041652,23.5400014 40.3595,23.9179976 40.3595,24.394 L40.3595,29 L38.7635,29 Z M47.22825,29 L47.22825,21.58 L48.76825,21.58 L48.76825,22.294 L48.79625,22.294 C49.0015844,22.0326654 49.2862482,21.8133342 49.65025,21.636 C50.0142518,21.4586658 50.3969147,21.37 50.79825,21.37 C51.6195874,21.37 52.2659143,21.6149976 52.73725,22.105 C53.2085857,22.5950024 53.44425,23.2739957 53.44425,24.142 L53.44425,29 L51.86225,29 L51.86225,24.562 C51.86225,23.4139943 51.3815881,22.84 50.42025,22.84 C49.9349142,22.84 49.5475848,22.9799986 49.25825,23.26 C48.9689152,23.5400014 48.82425,23.9179976 48.82425,24.394 L48.82425,29 L47.22825,29 Z M60.621,26.536 L61.769,27.474 C61.0503297,28.6313391 60.0236733,29.21 58.689,29.21 C57.6623282,29.21 56.8246699,28.8530036 56.176,28.139 C55.5273301,27.4249964 55.203,26.4753393 55.203,25.29 C55.203,24.1046607 55.5249968,23.1550036 56.169,22.441 C56.8130032,21.7269964 57.6296617,21.37 58.619,21.37 C59.6083383,21.37 60.4179968,21.7246631 61.048,22.434 C61.6780031,23.1433369 61.993,24.0953274 61.993,25.29 L61.993,25.766 L56.813,25.766 C56.8316668,26.3820031 57.0019984,26.8743315 57.324,27.243 C57.6460016,27.6116685 58.0916638,27.796 58.661,27.796 C58.931668,27.796 59.1743323,27.758667 59.389,27.684 C59.6036677,27.609333 59.7926658,27.4973341 59.956,27.348 C60.1193342,27.1986659 60.2429996,27.0680006 60.327,26.956 C60.4110004,26.8439994 60.5089994,26.7040008 60.621,26.536 L60.621,26.536 Z M56.827,24.562 L60.439,24.562 C60.4109999,24.0393307 60.2430015,23.612335 59.935,23.281 C59.6269985,22.949665 59.1883362,22.784 58.619,22.784 C58.0869973,22.784 57.6623349,22.9613316 57.345,23.316 C57.0276651,23.6706684 56.8550001,24.0859976 56.827,24.562 L56.827,24.562 Z M68.32975,26.046 L69.91175,26.382 C69.7624159,27.2780045 69.4217527,27.9733309 68.88975,28.468 C68.3577473,28.9626691 67.6530877,29.21 66.77575,29.21 C65.767745,29.21 64.9417532,28.8646701 64.29775,28.174 C63.6537468,27.4833299 63.33175,26.5220062 63.33175,25.29 C63.33175,24.1046607 63.6560801,23.1550036 64.30475,22.441 C64.9534199,21.7269964 65.7724117,21.37 66.76175,21.37 C67.6110876,21.37 68.3110806,21.6243308 68.86175,22.133 C69.4124194,22.6416692 69.7344162,23.3019959 69.82775,24.114 L68.32975,24.366 C68.1710825,23.3299948 67.6530877,22.812 66.77575,22.812 C66.2064138,22.812 65.7537517,23.0289978 65.41775,23.463 C65.0817483,23.8970022 64.91375,24.5059961 64.91375,25.29 C64.91375,26.0740039 65.0770817,26.6829978 65.40375,27.117 C65.7304183,27.5510022 66.1877471,27.768 66.77575,27.768 C67.6437543,27.768 68.1617492,27.1940057 68.32975,26.046 L68.32975,26.046 Z M72.0205,26.522 L72.0205,22.952 L70.9005,22.952 L70.9005,21.58 L72.0625,21.58 L72.0625,19.76 L73.5745,19.76 L73.5745,21.58 L75.4365,21.58 L75.4365,22.952 L73.5885,22.952 L73.5885,26.354 C73.5885,26.7646687 73.6514994,27.0516658 73.7775,27.215 C73.9035006,27.3783342 74.162498,27.46 74.5545,27.46 L75.1425,27.46 L75.1425,29 L74.4285,29 C73.5324955,29 72.9071684,28.8016686 72.5525,28.405 C72.1978316,28.0083313 72.0205,27.380671 72.0205,26.522 L72.0205,26.522 Z M81.992,29 L80.354,21.58 L81.922,21.58 L82.972,26.746 L83,26.746 L84.764,21.58 L86.206,21.58 L87.858,26.732 L87.886,26.732 L89.076,21.58 L90.616,21.58 L88.838,29 L87.298,29 L85.492,23.428 L85.464,23.428 L83.518,29 L81.992,29 Z M92.40275,29 L92.40275,21.58 L93.99875,21.58 L93.99875,29 L92.40275,29 Z M92.37475,20.362 L92.37475,18.78 L94.02675,18.78 L94.02675,20.362 L92.37475,20.362 Z M96.6955,26.522 L96.6955,22.952 L95.5755,22.952 L95.5755,21.58 L96.7375,21.58 L96.7375,19.76 L98.2495,19.76 L98.2495,21.58 L100.1115,21.58 L100.1115,22.952 L98.2635,22.952 L98.2635,26.354 C98.2635,26.7646687 98.3264994,27.0516658 98.4525,27.215 C98.5785006,27.3783342 98.837498,27.46 99.2295,27.46 L99.8175,27.46 L99.8175,29 L99.1035,29 C98.2074955,29 97.5821684,28.8016686 97.2275,28.405 C96.8728316,28.0083313 96.6955,27.380671 96.6955,26.522 L96.6955,26.522 Z M101.87025,29 L101.87025,18.78 L103.46625,18.78 L103.46625,22.308 L103.49425,22.308 C103.662251,22.0559987 103.937581,21.8366676 104.32025,21.65 C104.702919,21.4633324 105.090248,21.37 105.48225,21.37 C106.275587,21.37 106.919581,21.6126642 107.41425,22.098 C107.908919,22.5833358 108.15625,23.2459958 108.15625,24.086 L108.15625,29 L106.57425,29 L106.57425,24.464 C106.57425,23.9786642 106.443585,23.5866682 106.18225,23.288 C105.920915,22.9893318 105.542919,22.84 105.04825,22.84 C104.572248,22.84 104.189585,22.9799986 103.90025,23.26 C103.610915,23.5400014 103.46625,23.8993311 103.46625,24.338 L103.46625,29 L101.87025,29 Z" id="Connect-with" fill="#FFFFFF"></path>
                                <path d="M160.015559,18.7243818 L157.573637,23.6936411 L155.130184,18.7243818 L151.538762,18.7243818 L157.573637,31 L163.604197,18.7243818 L160.015559,18.7243818 Z M140.167341,23.0633572 C140.167341,22.6899778 140.038474,22.4112701 139.782411,22.2312505 C139.527323,22.049653 139.178854,21.959428 L137.108085,21.959428 L137.108085,24.220073 L138.726013,24.220073 C139.17454,24.220073 139.527323,24.1208112 139.782411,23.9228613 C140.038474,23.7244811 140.167341,23.4484988 140.167341,23.0966357 L140.167341,23.0633572 Z M149.175468,18 L155.208534,30.2756182 L151.617112,30.2756182 L149.175468,25.306072 L146.735216,30.2756182 L144.297747,30.2756182 L143.145603,30.2756182 L140.022749,30.2756182 L137.908281,26.9753059 L137.877804,26.9753059 L137.108085,26.9753059 L137.108085,30.2756182 L133.360798,30.2756182 L133.360798,18.7243818 L138.838458,18.7243818 C139.841696,18.7243818 140.666246,18.8428649 141.311553,19.0794006 C141.958668,19.3155061 142.477752,19.638107 142.87298,20.0451952 C143.215325,20.3961977 143.471249,20.7933884 143.642978,21.231747 C143.812619,21.6721138 143.898902,22.1909433 143.898902,22.7824979 L143.898902,22.8174977 C143.898902,23.6638052 143.701288,24.3792936 143.305086,24.9618113 C142.911389,25.5449028 142.372405,26.0083638 141.687715,26.3481777 L143.635324,29.2788401 L149.175468,18 Z M165.966934,18 L159.934565,30.2756182 L163.525987,30.2756182 L165.966934,25.306072 L168.409552,30.2756182 L172,30.2756182 L165.966934,18 Z M122.487587,21.9899811 L125.786345,21.9899811 L125.786345,30.2756182 L129.534327,30.2756182 L129.534327,21.9899811 L132.833921,21.9899811 L132.833921,18.7243818 L122.487587,18.7243818 L122.487587,21.9899811 Z M122.352597,25.0606428 C122.581523,25.4677311 122.696612,25.9636099 122.696612,26.5455539 L122.696612,26.5794062 C122.696612,27.1838705 122.579853,27.7295237 122.343829,28.212923 C122.109615,28.6976133 121.777847,29.1069967 121.351168,29.4430811 C120.923515,29.7780181 120.405405,30.036357 119.797395,30.2182414 C119.189663,30.400843 118.505112,30.4919286 117.748474,30.4919286 C116.606767,30.4919286 115.540905,30.3282614 114.553254,30.0046563 C113.565741,29.6791866 112.715028,29.1923446 112,28.5447042 L114.001188,26.0865396 C114.609059,26.5697955 115.250886,26.9167816 115.922216,27.1254896 C116.594521,27.334628 117.262511,27.4391972 117.924378,27.4391972 C118.266584,27.4391972 118.511235,27.3947302 118.660976,27.3078043 C118.811692,27.2194441 118.884892,27.0980922 118.884892,26.9447528 L118.884892,26.9119046 C118.884892,26.7453685 118.774256,26.6062298 118.556603,26.4980746 C118.338949,26.388198 117.928692,26.273301 117.331258,26.1525229 C116.701677,26.0195522 116.100764,25.8647784 115.530329,25.6893489 C114.959058,25.51478 114.457508,25.2881418 114.025541,25.0125898 C113.592878,24.7383288 113.248863,24.3904821 112.991966,23.9727791 C112.735903,23.554359 112.607871,23.0422712 112.607871,22.4378069 L112.607871,22.4045284 C112.607871,21.8538547 112.711549,21.341767 112.920435,20.8692692 C113.126816,20.3961977 113.431726,19.982798 113.832242,19.6317956 C114.232897,19.2795021 114.730132,19.0049543 115.323112,18.80557 C115.913588,18.6076201 116.594521,18.508932 117.364379,18.508932 C118.45209,18.508932 119.404811,18.6413289 120.221569,18.9062662 C121.038465,19.1683346 121.771724,19.5649516 122.423988,20.092818 L120.598705,22.7013097 C120.064869,22.3039755 119.501531,22.0153704 118.909246,21.8333425 C118.315431,21.6514581 117.759746,21.5603725 117.235513,21.5603725 C116.958575,21.5603725 116.753168,21.6044092 116.619849,21.6927694 C116.484024,21.7808428 116.419591,21.8968873 116.419591,22.0391817 L116.419591,22.0720299 C116.419591,22.2273776 116.51965,22.3593441 116.723526,22.469651 C116.927263,22.5790972 117.321656,22.6949983 117.907817,22.8160633 C118.623541,22.9471692 119.274136,23.1073939 119.862802,23.2932946 C120.448825,23.4822077 120.954827,23.7204648 121.375383,24.012656 C121.797052,24.3042735 122.123532,24.6521202 122.352597,25.0606428 L122.352597,25.0606428 Z" id="Strava-logo-Copy-6" fill="#FFFFFF"></path>
                            </g>
                        </g>
                    </svg>"""
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
                        <a href="{AUTH_URL}">{strava_svg}</a>
                    </div>
                    """, unsafe_allow_html=True)
                st.write("")

        else:
            st.write("")
            activities = get_activities(st.session_state.access_token)
            if activities:
                # Convert activities to DataFrame
                df = pd.DataFrame(activities)
                st.success("Activitats carregades!")
            else:
                st.warning("No s'han trobat activitats.")
    
        # Save to Supabase
        #save_activities_to_supabase(activities, st.session_state.athlete_id)
    if df is not None:
        with st.container(border=True):
            """
            2. Selecciona el període que vols analitzar i el tipus d'activitat (opcional):
            """
            # Add date filter
            col1, col2 = st.columns(2)
            with col1:
                selected_dates = st.date_input(
                    "",
                    value=(pd.to_datetime('now').date() - pd.DateOffset(days=30),pd.to_datetime('now').date()),
                    min_value=pd.to_datetime(df['datetime_local'].min()).date(),
                    max_value=pd.to_datetime('now').date(),
                    label_visibility="collapsed"
                )
            # Check if dates are selected
            if len(selected_dates) != 2:
                st.warning("Selecciona un rang de dates per continuar.")
                st.stop()
            # Show warning if the date range is less than 28 days
            date_diff = (selected_dates[1] - selected_dates[0]).days
            if date_diff < 28:
                st.warning("Et recomanem seleccionar un període mínim de 4 setmanes (o 28 dies) per veure tendències i canvis significatius.")
                st.stop()
  
            with col2:
                # Get unique running activity types
                running_types = df[df['sport'] == 'Run']['type'].unique().tolist()
                running_types.insert(0, "Totes")  # Add "All" option at the beginning
                
                selected_type = st.selectbox(
                    "Selecciona el tipus de cursa:",
                    options=running_types,
                    label_visibility="collapsed"
                )

        # Convert datetime_local to datetime for filtering
        df['datetime_local'] = pd.to_datetime(df['datetime_local'])
        
        # Filter DataFrame based on selected dates and Sport = Run, optionally by specific run type
        if selected_type == "Totes":
            mask = (
                (df['datetime_local'].dt.date >= selected_dates[0]) & 
                (df['datetime_local'].dt.date <= selected_dates[1]) & 
                (df['sport'] == 'Run')
            )
        else:
            mask = (
                (df['datetime_local'].dt.date >= selected_dates[0]) & 
                (df['datetime_local'].dt.date <= selected_dates[1]) & 
                (df['sport'] == 'Run') &
                (df['type'] == selected_type)
            )
        df_filtered = df[mask]
        st.divider()     
        """
        ### **Volum**
        **Incrementar gradualment** (no es recomana més d'un 10% inter-setmanal com a norma general) i **ser consistent** amb el volum setmanal és un molt bon indicador de que estàs millorant el nivell de forma.

        Si entrenes per muntanya, pot ser més útil fer servir temps en comptes de distància per tenir en compte el desnivell.
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
                annotation_text=f"Mitjana: {mean_distance:.1f} km",
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
                annotation_text=f"Mitjana: {mean_time:.1f} h",
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
        Un dels entrenaments claus per proves de resistència és fer una sortida llarga.
        Com a guia, a les setmanes de major volum, la seva distància hauria d'estar **entre el 30% i el 40% del total setmanal**. 
        
        En general, si ets capaç d'incrementar la distància setmana a setmana i aconsegueixes mantenir ritmes semblants, és probable que estiguis millorant.
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

        # Select columns for display
        longest_runs_display = longest_runs[[
            'datetime_local', 'name', 'distance', 'moving_time', 'average_speed', 'percentage'
        ]].copy()

        # Sort by datetime first (while it's still in datetime format)
        longest_runs_display = longest_runs_display.sort_values('datetime_local', ascending=False)

        # Then format the columns
        longest_runs_display['datetime_local'] = longest_runs_display['datetime_local'].dt.strftime('%d/%m/%Y')

        # Format moving time to hours and minutes
        longest_runs_display['moving_time'] = longest_runs_display['moving_time'].apply(
            lambda x: f"{int(x//60)}h{int(x%60)}min" if x >= 60 else f"{int(x)}min"
        )

        longest_runs_display['distance'] = longest_runs_display['distance'].apply(lambda x: f"{x:.1f} km")

        # Convert speed (km/h) to pace (min/km)
        longest_runs_display['average_speed'] = longest_runs_display['average_speed'].apply(
            lambda x: f"{int((60/x))}:{int((60/x)%1 * 60):02d} min/km"
        )

        # Format percentage
        longest_runs_display['percentage'] = longest_runs_display['percentage'].apply(lambda x: f"{x:.1f}%")

        # Rename columns
        longest_runs_display.columns = ['Data', 'Nom', 'Distància', 'Temps', 'Ritme', '% del total']

        # First, create a copy of the percentage column with numeric values for comparison
        longest_runs_display['numeric_percentage'] = longest_runs['percentage']
                
        # Drop any columns you want to remove BEFORE creating the Styler object
        longest_runs_display = longest_runs_display.drop('numeric_percentage', axis=1)
        
        # Display the styled dataframe with sorting preserved
        st.dataframe(
            longest_runs_display,
            use_container_width=True,
            hide_index=True  # Optional: hide the index if you don't need it
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

        No obstant, el més important és **ser consistent** i trobar l'organització que et permeti **entrenar de forma continuada en el temps**.
        """
        
        # Count sessions per week
        weekly_sessions = df_filtered.groupby([
            df_filtered['datetime_local'].dt.isocalendar().year,
            df_filtered['datetime_local'].dt.isocalendar().week
        ]).size().reset_index()
        weekly_sessions.columns = ['Year', 'Week', 'Sessions']

        # Create a combined year-week label for x-axis
        weekly_sessions['Week_Label'] = weekly_sessions.apply(lambda x: f"S{int(x['Week']):02d}", axis=1)

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

        Amb les dades disponibles, per fer una aproximació de com has variat la intensitat al llarg de les setmanes calculem  

        """
        """
        Els gràfics mostren la variació percentual setmana a setmana de la freqüència cardíaca i el ritme mitjans.
        - Per la **FC**, verd significa que ha baixat (menys esforç) i vermell que ha pujat (més esforç).
        - Pel **ritme**, verd significa que ha millorat (més ràpid) i vermell que ha empitjorat (més lent).
        
        Una tendència positiva seria veure barres verdes en ambdós gràfics (menor FC i ritme més ràpid).
        """
        # Extract the week number and year for grouping
        df_filtered["week"] = df_filtered["datetime_local"].dt.strftime('%Y-%W')

        # Filter out activities with no heart rate data
        df_hr = df_filtered[df_filtered['average_heartrate'].notna()]

        # Check if there's any heart rate data
        has_hr_data = not df_hr.empty

        # Compute Weighted Weekly Average HR and Speed
        weekly_stats = df_hr.groupby("week").apply(
            lambda x: pd.Series({
                "weekly_avg_hr": (x["average_heartrate"] * x["moving_time"]).sum() / x["moving_time"].sum() if has_hr_data else None,
                "weekly_avg_speed": (x["average_speed"] * x["moving_time"]).sum() / x["moving_time"].sum()
            })
        ).reset_index()

        # Sort by week to ensure correct calculation of changes
        weekly_stats = weekly_stats.sort_values('week')

        # Calculate percentage changes
        weekly_stats["speed_change_pct"] = weekly_stats["weekly_avg_speed"].pct_change() * 100
        if has_hr_data:
            weekly_stats["hr_change_pct"] = weekly_stats["weekly_avg_hr"].pct_change() * 100

        # Create the subplots figure with appropriate number of rows
        if has_hr_data:
            fig = make_subplots(rows=2, cols=1, 
                              subplot_titles=('Variació FC mitjana (%)', 'Variació ritme mitjà (%)'),
                              vertical_spacing=0.15)
        else:
            fig = make_subplots(rows=1, cols=1, 
                              subplot_titles=('Variació ritme mitjà (%)',))

        # Add HR change bars if data exists
        if has_hr_data:
            fig.add_trace(
                go.Bar(
                    x=weekly_stats['week'].str.replace('\d{4}-', 'S'),
                    y=weekly_stats['hr_change_pct'].round(1),
                    text=weekly_stats['hr_change_pct'].round(1).apply(lambda x: f"{x:+.1f}%" if pd.notnull(x) else ""),
                    textposition='auto',
                    name='FC',
                    marker_color=weekly_stats['hr_change_pct'].apply(
                        lambda x: 'lightcoral' if x > 0 else 'lightgreen' if x < 0 else 'gray'
                    ),
                ),
                row=1, col=1
            )

        # Add pace change bars
        fig.add_trace(
            go.Bar(
                x=weekly_stats['week'].str.replace('\d{4}-', 'S'),
                y=-weekly_stats['speed_change_pct'].round(1),  # Negative to show pace change
                text=(-weekly_stats['speed_change_pct'].round(1)).apply(lambda x: f"{x:+.1f}%" if pd.notnull(x) else ""),
                textposition='auto',
                name='Ritme',
                marker_color=(-weekly_stats['speed_change_pct']).apply(
                    lambda x: 'lightcoral' if x > 0 else 'lightgreen' if x < 0 else 'gray'
                ),
            ),
            row=2 if has_hr_data else 1, col=1
        )

        # Update layout
        fig.update_layout(
            showlegend=False,
            plot_bgcolor='white',
            height=600 if has_hr_data else 400,  # Adjust height based on number of plots
            title_text="Variació setmanal de FC i ritme" if has_hr_data else "Variació setmanal del ritme"
        )

        # Update axes
        if has_hr_data:
            fig.update_xaxes(showgrid=False, title_text="", row=1, col=1)
        fig.update_xaxes(showgrid=False, title_text="", row=2 if has_hr_data else 1, col=1)
        if has_hr_data:
            fig.update_yaxes(showgrid=True, gridcolor='LightGray', title_text="", row=1, col=1)
        fig.update_yaxes(showgrid=True, gridcolor='LightGray', title_text="", row=2 if has_hr_data else 1, col=1)

        st.plotly_chart(fig, use_container_width=True)


        # Continue with the existing weekly stats table
        weekly_stats["weekly_avg_pace"] = (60 / weekly_stats["weekly_avg_speed"]).apply(lambda x: f"{int(x)}:{int((x - int(x)) * 60):02d} min/km")
        weekly_stats["weekly_avg_hr"] = weekly_stats["weekly_avg_hr"].apply(lambda x: f"{int(x)} bpm")
        weekly_stats = weekly_stats.drop("weekly_avg_speed", axis=1)
        weekly_stats = weekly_stats.sort_values('week', ascending=False)
       
        weekly_stats = weekly_stats.drop(["hr_change_pct", "speed_change_pct"], axis=1)
        weekly_stats.columns = ['Setmana', 'FC mitjana', 'Ritme mitjà']

        st.dataframe(weekly_stats, use_container_width=True)
        
        st.divider()
        """
        ### **Rendiment**
        """
        """
        #### Eficiència aeròbica

        La freqüència cardíaca és una mesura de la resposta del teu cos a l'exercici. De forma indirecta, ens indica el treball que estàs realitzant tot i que pot veure's afectat per factors com la temperatura corporal, la fatiga i la hidratacaió.
        
        Tot i les limitacions, per comprovar la capacitat aeròbica és útil analitzar la relació entre càrrega externa (ritme) i càrrega interna (freqüència cardíaca) i veure com evoluciona en el temps.

        En general, una **FC més baixa per ritmes semblants** (quan és consistent en el temps) indica una millora de la capacitat aeròbica.
        
        """
        with st.form("Configura l'anàlisi:"):
            """
            *Utilitza els filtres per seleccionar entrenaments que realitzis freqüentment i que siguin semblants entre ells. Als entrenaments més llargs, la FC pot veure's afectada per la deshidratació i la fatiga acumulada.*
            """
            col1, col2 = st.columns(2,gap="medium")
            with col1:
                min_dist = int(df_filtered['distance'].min())
                max_dist = int(df_filtered['distance'].max()) + 3
                distance_options = list(range(min_dist, max_dist))
                
                selected_distance = st.select_slider(
                    '**Distància (km):**',
                    options=distance_options,  # Use the list of integers
                    value=(min_dist, max_dist - 1)
                ) 
                sports = df_filtered['type'].unique()
                selected_sport = st.selectbox(label='**Activitat:**',options=sports)

            with col2:
                min_elev = int(df_filtered['elevation_gain'].min())
                max_elev = int(df_filtered['elevation_gain'].max()) + 1
                elevation_options = list(range(min_elev, max_elev))

                selected_elevation = st.select_slider(
                    '**Desnivell (m):**',
                    options=elevation_options,  # Use the list of integers
                    value=(min_elev, max_elev - 1)
                )
            submitted = st.form_submit_button("Guardar")
            if submitted:
                mask = (
                    (df_filtered['distance'] >= (selected_distance[0])) 
                    & (df_filtered['distance'] <= (selected_distance[1])) 
                    & (df_filtered['elevation_gain'] >= selected_elevation[0]) & (df_filtered['elevation_gain'] <= selected_elevation[1]) 
                    & (df_filtered['type'] == selected_sport)
                )
                df_aerobic = df_filtered[mask]
            else:
                st.stop()  
        # Format df_aerobic for display
        df_display = df_aerobic[[
            'datetime_local', 'distance', 'moving_time', 
            'elevation_gain', 'average_speed', 
            'average_heartrate'
        ]].copy()

        # Convert datetime_local to datetime for proper sorting
        df_display['sort_date'] = pd.to_datetime(df_display['datetime_local'])
        df_display = df_display.sort_values('sort_date', ascending=False)

        # Calculate efficiency index (speed/heart rate ratio)
        # Multiply by 100 to get more readable numbers
        df_display['efficiency'] = (df_display['average_speed'] / df_display['average_heartrate'] * 100).round(2)

        # Drop the sorting column and format the display dataframe
        df_display = df_display.drop('sort_date', axis=1)
        
        # Then format the columns
        df_display['datetime_local'] = df_display['datetime_local'].dt.strftime('%d/%m/%Y')
        df_display['distance'] = df_display['distance'].apply(lambda x: f"{x:.1f} km")
        df_display['moving_time'] = df_display['moving_time'].apply(
            lambda x: f"{int(x//60)}h{int(x%60)}min" if x >= 60 else f"{int(x)}min"
        )
        df_display['elevation_gain'] = df_display['elevation_gain'].apply(lambda x: f"{int(x)} m")
        df_display['average_speed'] = df_display['average_speed'].apply(
            lambda x: f"{int((60/x))}:{int((60/x)%1 * 60):02d} min/km"
        )
        df_display['average_heartrate'] = df_display['average_heartrate'].apply(lambda x: f"{int(x)} bpm" if pd.notnull(x) else None)
        #df_display['max_heartrate'] = df_display['max_heartrate'].apply(lambda x: f"{int(x)} bpm" if pd.notnull(x) else None)
        df_display['efficiency'] = df_display['efficiency'].apply(lambda x: f"{x:.2f}" if pd.notnull(x) else None)
        
        # Rename columns
        df_display.columns = [
            'Data', 'Distància', 'Temps', 'Desnivell', 
            'Ritme', 'FC mitjana', 'Índex Eficiència'
        ]

        """
        Per facilitar l'anàlisi, calculem un índex d'eficiència que representa la **relació entre velocitat i freqüència cardíaca**. 
        Una tendència incremental de l'índex indica que s'està corrent més ràpid amb menys esforç cardíac, 
        el que pot ser un indicador de millora en el rendiment.

        Tingues en compte que aquest valor és una simplificació i s'ha d'analitzar conjuntament amb altres dades i factors com desnivell, fatiga, etc.
        """

        st.dataframe(df_display, use_container_width=True)



if __name__ == "__main__":
    main()
