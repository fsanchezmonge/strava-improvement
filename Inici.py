import streamlit as st
import os
from dotenv import load_dotenv
import base64
import pathlib
import requests
from datetime import datetime, timezone
from supabase import create_client, Client
import uuid

st.set_page_config(
    page_title="Analitza el teu entrenament",
    page_icon=":running:",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Load environment variables
load_dotenv()

# Initialize Supabase client
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
    REDIRECT_URI = os.getenv("REDIRECT_URI", "http://localhost:8501")  # Local development fallback

AUTH_URL = f"http://www.strava.com/oauth/authorize?client_id={STRAVA_CLIENT_ID}&response_type=code&redirect_uri={REDIRECT_URI}&scope=activity:read_all"

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

def log_user_session(athlete_id, event_type, event_data=None):
    """Log user session data to Supabase"""
    try:
        log_entry = {
            'athlete_id': athlete_id if athlete_id is not None else 0,
            'event_type': event_type,
            'event_data': event_data,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        supabase.table('app_logs').insert(log_entry).execute()
    except Exception as e:
        st.error(f"Error logging event: {str(e)}")

def main():
    st.markdown("""
        <style>
        /* Override Streamlit's default container styles */
        .stApp {
            max-width: 100% !important;
            padding: 0 !important;
            background-color: rgba(207, 240, 17, 0.20) !important;
        }
        .main .block-container {
            max-width: 100% !important;
            padding: 0 !important;
            margin: 0px !important;
            background-color: rgba(207, 240, 17, 0.27) !important;
        }
        /* Remove all default padding and margins */
        .stApp > header {
            background-color: transparent;
        }
        .stApp > footer {
            display: none;
        }
        section[data-testid="stSidebar"] {
            display: none;
        }
        .stDeployButton {
            display: none;
        }
        /* Ensure content takes full width */
        .stMarkdown {
            max-width: 100% !important;
            padding: 0 !important;
        }
        /* Override any other potential width constraints */
        div[data-testid="stVerticalBlock"] {
            max-width: 100% !important;
            padding: 0 !important;
        }
        div[data-testid="stHorizontalBlock"] {
            max-width: 100% !important;
            padding: 0 !important;
        }
        /* Your existing styles */
        h1 {
            font-family: 'Helvetica Neue', sans-serif;
            font-size: 40px;
            color: #222831;
            margin-bottom: 10px;
        }
        h4 {
            font-family: 'Helvetica Neue', sans-serif;
            font-size: 25px;
            color: #393E46;
            font-weight: bold;
            margin-top: 10px;
            margin-bottom: 50px;
        }
        h5 {
            font-family: 'Helvetica Neue', sans-serif;
            font-size: 20px;
            color: #393E46;
            font-weight: normal;
            margin-bottom: 50px;
        }
        </style>
    """, unsafe_allow_html=True)
    # Generate a unique session ID when the app starts
    if 'session_id' not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())

    # Log landing page view
    log_user_session(
        athlete_id=0,
        event_type='landing_page_view',
        event_data={'session_id': st.session_state.session_id}
    )


    # Check for authorization code in URL
    query_params = st.query_params
    
    if 'code' in query_params:
        code = query_params.get("code", [])
        # Log authorization start
        log_user_session(
            athlete_id=0,
            event_type='auth_start',
            event_data={'auth_code_present': True}
        )
        
        with st.spinner('Connectant amb Strava...'):
            try:
                token_response = get_token(code)
                if 'access_token' in token_response:
                    # Store token in session state
                    st.session_state.access_token = token_response['access_token']
                    st.session_state.athlete_id = token_response['athlete']['id']
                    # Save token to Supabase
                    save_token_to_supabase(token_response)
                    # Log successful authorization
                    log_user_session(
                        athlete_id=token_response['athlete']['id'],
                        event_type='auth_success',
                        event_data={'athlete_id': token_response['athlete']['id']}
                    )
                    # Redirect to main app
                    st.switch_page("pages/Anàlisi.py")
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

    # Display the Strava connect button and image
    #col1, col2, col3 = st.columns([1, 3, 1])
    #with col2:
    st.markdown("""
        <div style="text-align: center; background-color: rgba(255, 255, 255, 0); padding: 20px 0; margin: 0;">
            <h1>Apren els bàsics, entrena millor</h1>
            <h5>Analitza com prepares les teves curses i descobreix què pots millorar amb consells personalitzats</h5>
        </div>
    """, unsafe_allow_html=True)

    # Path to your SVG file
    svg_path = f"{current_dir}/assets/strava_button.svg"
    with open(svg_path, "rb") as f:
        svg_data = f.read()
        b64_svg = base64.b64encode(svg_data).decode("utf-8")
    svg_uri = f"data:image/svg+xml;base64,{b64_svg}"

    st.markdown(f"""
        <style>
        .full-width-cta {{
            width: 100vw;
            position: relative;
            left: 50%;
            right: 50%;
            margin-left: -50vw;
            margin-right: -50vw;
            background-color: rgba(255, 255, 255, 0.6);
            padding: 20px 0;
            border-radius: 0;
            box-shadow: none;
            text-align: center;
        }}
        .strava-button {{
            display: inline-block;
            cursor: pointer;
            transition: transform 0.2s;
        }}
        .strava-button:hover {{
            transform: scale(1.04);
        }}
        </style>
        <div class="full-width-cta">
            <p style="margin-bottom: 20px; font-weight: normal; font-family: 'Helvetica Neue', sans-serif; font-size: 16px; color: #222831;">
                Connecta el teu perfil i comença
            </p>
            <a href="{AUTH_URL}" class="strava-button">
                <img src="{svg_uri}" width="210" height="70" alt="Connect with Strava"/>
            </a>
        </div>
    """, unsafe_allow_html=True)

    # Convert background image to base64
    background_path = f"{current_dir}/assets/background.jpeg"
    with open(background_path, "rb") as f:
        background_data = f.read()
        b64_background = base64.b64encode(background_data).decode("utf-8")
    background_uri = f"data:image/jpeg;base64,{b64_background}"
    
    # Create the entire section in a single markdown block
    st.markdown(f"""
        <style>
        .full-width-bg {{
            width: 100vw;
            position: relative;
            left: 50%;
            right: 50%;
            margin-left: -50vw;
            margin-right: -50vw;
            padding: 0;
        }}
        .background-container {{
            background-image: url('{background_uri}');
            background-size: cover;
            background-position: center bottom;
            background-repeat: no-repeat;
            min-height: 40vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 0;
            width: 100%;
            margin: 0;
            position: relative;
        }}
        .background-container::before {{
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(87, 87, 87, 0.5);
            z-index: 1;
        }}
        .content-wrapper {{
            position: relative;
            z-index: 2;
            display: flex;
            justify-content: space-between;
            padding: 40px 20px;
        }}
        .column-content {{
            flex: 1;
            margin: 0 30px;
            background: transparent;
            padding: 60px;
            border-radius: 0;
            transition: all 0.3s ease;
            text-align: center;
        }}
        .column-content h4 {{
            color:rgb(255, 255, 255);
            font-family: 'Helvetica Neue', sans-serif;
            margin-bottom: 0px;
        }}
        .column-content p {{
            color:rgb(255, 255, 255);
            font-family: 'Helvetica Neue', sans-serif;
            font-size: 16px;
        }}
        </style>
        <div class="full-width-bg">
            <div class="background-container">
                <div class="content-wrapper">
                    <div class="column-content">
                        <h4>Volum</h4>
                        <p>Controlar la quantitat i progressar és clau</p>
                    </div>
                    <div class="column-content">
                        <h4>Freqüència</h4>
                        <p>Comprova si estàs sent consistent</p>
                    </div>
                    <div class="column-content">
                        <h4>Intensitat</h4>
                        <p>Troba el nivell d'esforç adequat</p>
                    </div>
                </div>
            </div>
        </div>
    """, unsafe_allow_html=True)

if __name__ == "__main__":
    main()