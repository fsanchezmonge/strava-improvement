import streamlit as st
import os
import base64
import pathlib
import requests
from datetime import datetime, timezone
from supabase import create_client, Client
import uuid

# Try to import dotenv, but don't fail if it's not available
try:
    from dotenv import load_dotenv
    DOTENV_AVAILABLE = True
except ImportError:
    DOTENV_AVAILABLE = False

st.set_page_config(
    page_title="Analitza el teu entrenament",
    page_icon=":running:",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Load environment variables only in local development
if DOTENV_AVAILABLE and os.path.exists('.env'):
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
            background-color: rgba(255, 255, 255, 0.5) !important;
        }
        .main .block-container {
            max-width: 100% !important;
            padding: 0 !important;
            margin: 0px !important;
            background-color: rgba(255, 255, 255, 0.5) !important;
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
        h3 {
            font-family: 'Helvetica Neue', sans-serif;
            font-size: 25px;
            color: #393E46;
            font-weight: bold;
            margin-top: 10px;
            margin-bottom: 20px;
        }
        h4 {
            font-family: 'Helvetica Neue', sans-serif;
            font-size: 25px;
            color: #393E46;
            font-weight: normal;
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
                    
                    # Clear the URL parameters before redirecting
                    st.query_params.clear()
                    
                    # Redirect to main app
                    st.switch_page("pages/Analisi.py")
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
        <div style="text-align: center; background-color: rgba(255, 255, 255, 0.5); padding: 20px 0; margin: 0;">
            <h1>Apren els bàsics, entrena millor</h1>
            <h4>Analitza com prepares les teves curses i descobreix què pots millorar amb consells personalitzats</h4>
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
            background-color: rgba(207, 240, 17, 0.20);
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
            <p style="margin-bottom: 20px; font-weight: normal; font-family: 'Helvetica Neue', sans-serif; font-size: 18px; color: #222831;">
                Prova-ho amb una cursa recent
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
            margin: 0 90px;
            background: transparent;
            padding: 0px;
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
                <div class="content-wrapper" style="flex-direction: column;">
                    <h4 style="color: white; text-align: center; margin-bottom: 0px; font-size: 18px;">Controla tres aspectes clau d'una bona preparació</h4>
                    <div style="display: flex; justify-content: space-between; width: 100%; margin-top: 0px;">
                        <div class="column-content">
                            <h4>Volum</h4>
                            <p>Progressa gradualment</p>
                        </div>
                        <div class="column-content">
                            <h4>Freqüència</h4>
                            <p>Comprova si ets consistent</p>
                        </div>
                        <div class="column-content">
                            <h4>Intensitat</h4>
                            <p>Troba el nivell d'esforç adequat</p>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    """, unsafe_allow_html=True)

    # Add video section
    st.markdown("""
        <style>
        .video-section {
            width: 100%;
            padding: 40px 20px;
            background: linear-gradient(to bottom, rgba(255,255,255,0.9), rgba(255,255,255,0.9));
            margin: 0px 0;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .description-column {
            flex: 1;
            padding: 0 40px;
        }
        .video-column {
            flex: 1;
            display: flex;
            justify-content: center;
            align-items: center;
        }
        .video-container {
            width: 100%;
            max-width: 800px;
            position: relative;
            border-radius: 10px;
            overflow: hidden;
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.1);
            background: #000;
        }
        .video-title {
            margin-bottom: 30px;
        }
        .video-title h2 {
            font-family: 'Helvetica Neue', sans-serif;
            font-size: 32px;
            color: #222831;
            margin-bottom: 15px;
        }
        .video-title p {
            font-family: 'Helvetica Neue', sans-serif;
            font-size: 18px;
            color: #393E46;
            line-height: 1.6;
        }
        .stVideo {
            border-radius: 10px;
            overflow: hidden;
            width: 100% !important;
        }
        .stVideo > div {
            width: 100% !important;
        }
        .stVideo > div > video {
            width: 100% !important;
            height: auto !important;
        }
        </style>
    """, unsafe_allow_html=True)

    # Video path
    video_path = f"{current_dir}/assets/screen_recording.mp4"
    st.write("")
    st.write("")
    col2v, col3v, col4v = st.columns([0.5,0.4,0.1])
    with col2v: 
        st.write("")
        st.write("")
        st.write("")
        st.write("")
        st.markdown(f"""
            <div class="video-section">
                <div class="description-column">
                    <div class="video-title">
                        <h5>Connecta el teu perfil d'<span style="background-color:#FC4C02; color:#fff; border-radius:1px; font-weight:bold; padding:1px 4px;">Strava</span> i descobreix com pots entrenar més intel·ligent i millorar el teu temps.</h5>
                    </div>
                </div>
            </div>
        """, unsafe_allow_html=True)
    with col3v:
        st.video(video_path)

    st.write("")
    col1, col2, col3 = st.columns(3)
    with col2:
        st.markdown(
        """<div style="display: flex; justify-content: center; align-items: center;">
        <svg width='180' height='30' viewBox='0 0 365 37' fill='none' xmlns='http://www.w3.org/2000/svg'>
            <path d='M0.905029 35.1577H3.31523V29.0503H7.34003C10.8266 29.0503 12.8858 27.1315 12.8858 23.9257C12.8858 20.7433 10.8266 18.7777 7.36343 18.7777H0.905029V35.1577ZM3.31523 26.8039V21.0241H7.26983C9.42263 21.0241 10.4756 21.9601 10.4756 23.9257C10.4756 25.8445 9.39923 26.8039 7.24643 26.8039H3.31523ZM23.6787 35.5087C27.8907 35.5087 30.6753 32.1157 30.6753 26.9677C30.6753 21.8197 27.8907 18.4267 23.6787 18.4267C19.4667 18.4267 16.7055 21.8197 16.7055 26.9677C16.7055 32.1157 19.4667 35.5087 23.6787 35.5087ZM23.6787 33.2623C20.8473 33.2623 19.1157 30.8755 19.1157 26.9677C19.1157 23.0599 20.8473 20.6731 23.6787 20.6731C26.5335 20.6731 28.2651 23.0599 28.2651 26.9677C28.2651 30.8755 26.5335 33.2623 23.6787 33.2623ZM37.4562 35.1577H40.2174L43.9848 22.5919H44.0316L47.7756 35.1577H50.5602L53.6958 18.7777H51.2388L48.8988 31.3201H48.852L45.1314 18.7777H42.8616L39.1644 31.3201H39.1176L36.7776 18.7777H34.2972L37.4562 35.1577ZM58.3356 35.1577H68.3742V32.9113H60.7458V27.8101H67.719V25.6573H60.7458V21.0241H68.3742V18.7777H58.3356V35.1577ZM81.6314 28.5823C84.0416 28.0441 85.469 26.2891 85.469 23.7619C85.469 20.6497 83.3864 18.7777 79.97 18.7777H73.7924V35.1577H76.2026V28.7695H79.0808L82.4504 35.1577H85.1648L81.6314 28.6057V28.5823ZM76.2026 26.5465V21.0241H79.736C81.9356 21.0241 83.0588 21.9367 83.0588 23.7619C83.0588 25.5871 81.9122 26.5465 79.736 26.5465H76.2026ZM90.4832 35.1577H100.522V32.9113H92.8934V27.8101H99.8666V25.6573H92.8934V21.0241H100.522V18.7777H90.4832V35.1577ZM105.94 35.1577H111.111C115.417 35.1577 118.295 32.5369 118.295 26.9443C118.295 21.7963 115.417 18.7777 111.205 18.7777H105.94V35.1577ZM108.35 32.9113V21.0241H111.135C114.036 21.0241 115.885 23.0131 115.885 26.9443C115.885 31.2031 114.036 32.9113 111.018 32.9113H108.35ZM132.055 35.1577H138.115C142 35.1577 144.012 33.4729 144.012 30.2437C144.012 28.3951 143.053 26.9677 141.579 26.3827V26.3359C142.702 25.7743 143.404 24.5809 143.404 23.0599C143.404 20.5093 141.462 18.7777 138.607 18.7777H132.055V35.1577ZM134.465 25.5871V21.0007H138.209C140.034 21.0007 140.994 21.7729 140.994 23.2705C140.994 24.8149 140.081 25.5871 138.209 25.5871H134.465ZM134.465 32.9347V27.6229H138.162C140.572 27.6229 141.602 28.4185 141.602 30.2203C141.602 32.1157 140.549 32.9347 138.115 32.9347H134.465ZM151.963 35.1577H154.373V28.3717L160.012 18.7777H157.321L153.18 26.1253H153.133L148.991 18.7777H146.3L151.963 28.3717V35.1577Z' fill='black'/>
            <path fill-rule='evenodd' clip-rule='evenodd' d='M275.925 35.6008L275.923 35.5993H286.002L292.193 23.1514L298.383 35.5993H310.63L292.191 0L274.689 33.7968L267.969 23.9845C272.118 21.9836 274.704 18.5181 274.704 13.54V13.442C274.704 9.92849 273.631 7.39027 271.581 5.34009C269.189 2.94868 265.334 1.43574 259.282 1.43574H242.59V35.6008H254.011V25.8391H256.451L262.893 35.6008H275.925ZM346.353 0L327.917 35.5993H340.164L346.354 23.1514L352.545 35.5993H364.791L346.353 0ZM319.283 37L337.719 1.40071H325.473L319.282 13.8486L313.091 1.40071H300.845L319.283 37ZM258.94 17.6885C261.673 17.6885 263.333 16.4684 263.333 14.3698V14.2718C263.333 12.0756 261.624 11.0019 258.989 11.0019H254.01V17.6885H258.94ZM218.165 11.0994H208.112V1.43574H239.64V11.0994H229.587V35.6008H218.165V11.0994ZM180.282 23.2037L174.181 30.476C178.525 34.2835 184.772 36.2353 191.703 36.2353C200.879 36.2353 206.784 31.8425 206.784 24.6675V24.5703C206.784 17.6885 200.928 15.1502 192.191 13.5401C188.579 12.856 187.652 12.2712 187.652 11.3435V11.2459C187.652 10.4162 188.433 9.83056 190.141 9.83056C193.313 9.83056 197.17 10.8554 200.39 13.1981L205.955 5.48697C202.001 2.36309 197.121 0.800959 190.532 0.800959C181.111 0.800959 176.036 5.8286 176.036 12.3196V12.4176C176.036 19.6406 182.772 21.8376 190.434 23.3986C194.095 24.131 195.168 24.6675 195.168 25.644V25.742C195.168 26.6689 194.29 27.2053 192.24 27.2053C188.238 27.2053 183.992 26.0348 180.282 23.2037Z' fill='#FC5200'/>
        </svg>
        </div>""", 
        unsafe_allow_html=True
    )
    st.write("")
    st.write("")
if __name__ == "__main__":
    main()