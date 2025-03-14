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

st.set_page_config(
    page_title="Estic millorant el meu estat de forma?",
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
        Benvingut!

        Podràs trobar més informació sobre la aplicació a la meva web

        Si tens dubtes o suggerències, també em pots escriure per xarxes
        """
        col1sb, col2sb, col3sb = st.columns(3)
        st.markdown(f""

        )
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
        Fent servir l'aplicació acceptes la  [Política de privacitat](privacy_policy.md)
        """

    st.title("Estic millorant el meu estat de forma?")
    """    
    Si t'estàs preparant per una cursa o vols millorar el teu estat de forma és important revisar si el teu entrenament està funcionant i adaptar-ho si és necessari.
    
    Amb aquesta aplicació podràs revisar algunes dades que t'ajudaran a trobar la resposta. Recorda que el més important és ser consistent i cada un de nosaltres té un context diferent.
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
                st.markdown(f"""
                    <a href="{AUTH_URL}" target="_self" style="text-decoration: none;">
                        <div style="
                            background-color: #FC4C02;
                            border: none;
                            color: white;
                            padding: 12px 24px;
                            text-align: center;
                            text-decoration: none;
                            display: flex;
                            align-items: center;
                            justify-content: center;
                            font-size: 16px;
                            margin: 4px 2px;
                            cursor: pointer;
                            border-radius: 4px;
                            width: 100%;
                        ">
                            <img src="https://upload.wikimedia.org/wikipedia/commons/thumb/7/7c/Strava_logo_2019.svg/120px-Strava_logo_2019.svg.png" 
                                 style="height: 24px; margin-right: 8px;">
                            Connect with Strava
                        </div>
                    </a>
                """, unsafe_allow_html=True)
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
                st.warning("Es recomana seleccionar un període mínim de 4 setmanes (o 28 dies) per veure tendències i canvis significatius.")
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
        ## Analitza els resultats

        La informació està dividida en 4 parts: **volum**, **freqüencia**, **intensitat** i **rendiment**.
        
        Per saber si estàs millorant o no has de tenir en compte totes les parts en conjunt i recordar que hi ha factors com estrès personal, historial esportiu... que també aftecten a l'estat de forma
        """
        
        """
        ### **Volum**
        **Incrementar gradualment** (no es recomana més d'un 10% inter-setmanal) i **ser consistent** amb el volum setmanal és un molt bon indicador de que estàs millorant el nivell de forma.

        Aquest [estudi](https://pubmed.ncbi.nlm.nih.gov/32421886/) on s'examinava volum mitjà setmanal i sortida més llarga de 556 participants d'una mitja marató i 441 d'una marató, va trobar **una correlació alta entre volums d'entrenament alts i els temps la prova més baixos**.
        
        """
        # Create tabs for distance and time charts
        tab1, tab2 = st.tabs(["📏 Distància", "⏱️ Temps"])

        # Group by week and sum distances
        weekly_distance = df_filtered.groupby(df_filtered['datetime_local'].dt.isocalendar().week).agg({
            'distance': 'sum',
            'moving_time': 'sum'
        }).reset_index()
        weekly_distance.columns = ['Week', 'Distance', 'Time']

        with tab1:
            # Create the distance bar chart
            fig_distance = go.Figure(data=[
                go.Bar(
                    x=weekly_distance['Week'],
                    y=weekly_distance['Distance'],
                    text=weekly_distance['Distance'].round(1),
                    textposition='auto',
                )
            ])
            
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
            
            # Create the time bar chart
            fig_time = go.Figure(data=[
                go.Bar(
                    x=weekly_distance['Week'],
                    y=weekly_distance['Time'],
                    text=weekly_distance['Time'].round(1),
                    textposition='auto',
                )
            ])
            
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
        A l'estudi mencionat, també s'observa relació entre el rendiment i la distància de la sortida més llarga. A continuació, pots veure les sortides més llargues de cada setmana.

        Algunes preguntes que et podries fer:

            - Ha incrementat la distància amb el temps?

            - Has estat capaç de mantenir un ritme semblant tot i incrementar la distància?
        
        Per properes sortides, és molt útil prendre consciència de com reacciona el teu cos a aquests esforços. 
        Si l'entrenament està fent efecte, hauries de recuperar-te més fàcilment per esforços similars.

        """
        
        # Get longest activity per week
        longest_runs = df_filtered.groupby(df_filtered['datetime_local'].dt.isocalendar().week).apply(
            lambda x: x.nlargest(1, 'distance')
        ).reset_index(drop=True)

        # Select columns for display
        longest_runs_display = longest_runs[[
            'datetime_local', 'name', 'distance', 'moving_time', 'average_speed'
        ]].copy()

        # Format the columns
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

        # Rename columns
        longest_runs_display.columns = ['Data', 'Nom', 'Distància', 'Temps', 'Ritme']

        # Sort by distance (need to create temporary numeric column for sorting)
        longest_runs_display['sort_distance'] = longest_runs['distance']
        longest_runs_display = longest_runs_display.sort_values('sort_distance', ascending=False).drop('sort_distance', axis=1)

        # Display the dataframe
        st.dataframe(longest_runs_display, use_container_width=True)

        # Create line chart for longest runs
        fig_longest = go.Figure(data=[
            go.Scatter(
                x=longest_runs['datetime_local'],
                y=longest_runs['distance'],
                mode='lines+markers+text',
                text=longest_runs['distance'].round(1),
                textposition='top center',
                hovertemplate='%{x|%d/%m/%Y}<br>Distància: %{y:.1f} km<extra></extra>'
            )
        ])

        # Update layout
        fig_longest.update_layout(
            title='Evolució de la distància de les sortides més llargues per setmana',
            xaxis_title='Data',
            yaxis_title='Distància (km)',
            showlegend=False,
            plot_bgcolor='white',
            yaxis=dict(
                range=[0, longest_runs['distance'].max() * 1.2]  # Add 10% padding to max value
            )
        )

        # Update axes
        fig_longest.update_xaxes(
            showgrid=False,
            gridwidth=1,
            gridcolor='LightGray'
        )
        fig_longest.update_yaxes(
            showgrid=False,
            gridwidth=1,
            gridcolor='LightGray',
            zeroline=True,
            zerolinewidth=1,
            zerolinecolor='LightGray'
        )

        st.plotly_chart(fig_longest, use_container_width=True)
        
        """
        ### **Freqüència**

        La **freqüencia**, juntament amb el **volum** i la **intensitat**, és una altra variable que podem modificar per incrementar la càrrega d'entrenament.

        Cada entrenament actúa com un estressor sobre el teu cos que desencadena diferents respostes (hormonals, metabòliques, etc.) i acaba produïnt les adaptacions que et fan millorar.  

        Una major freqüència d'entrenament pot ser beneficiosa perquè produeix estímuls més constants i distribueix millor la fatiga, evitant sessions amb una càrrega excessiva.

        No obstant, el més important és **ser consistent** i trobar la distribució que et permeti **entrenar de forma continuada en el temps**.
        """
        
        # Count sessions per week
        weekly_sessions = df_filtered.groupby(df_filtered['datetime_local'].dt.isocalendar().week).size().reset_index()
        weekly_sessions.columns = ['Week', 'Sessions']

        # Create the sessions bar chart
        fig_sessions = go.Figure(data=[
            go.Bar(
                x=weekly_sessions['Week'],
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

        """
        # Extract the week number and year for grouping
        df_filtered["week"] = df_filtered["datetime_local"].dt.strftime('%Y-%W')

        # Compute Weighted Weekly Average HR
        weekly_stats = df_filtered.groupby("week").apply(
            lambda x: pd.Series({
                "weekly_avg_hr": (x["average_heartrate"] * x["moving_time"]).sum() / x["moving_time"].sum(),
                "weekly_avg_speed": (x["average_speed"] * x["moving_time"]).sum() / x["moving_time"].sum()
            })
        ).reset_index()
        weekly_stats["hr_change"] = weekly_stats["weekly_avg_hr"].diff()
        weekly_stats["speed_change"] = weekly_stats["weekly_avg_speed"].diff()

        # Format dataframe
        weekly_stats["weekly_avg_pace"] = (60 / weekly_stats["weekly_avg_speed"]).apply(lambda x: f"{int(x)}:{int((x - int(x)) * 60):02d} min/km")
        weekly_stats["weekly_avg_hr"] = weekly_stats["weekly_avg_hr"].apply(lambda x: f"{int(x)} bpm")
        weekly_stats = weekly_stats.drop("weekly_avg_speed", axis=1)
        weekly_stats = weekly_stats.sort_values('week', ascending=False)
       
        weekly_stats = weekly_stats.drop(["hr_change", "speed_change"], axis=1)
        weekly_stats.columns = ['Setmana', 'FC mitjana', 'Ritme mitjà']

        st.dataframe(weekly_stats, use_container_width=True)
        """
        ### **Rendiment**

        """
        """
        #### Eficiència aeròbica

        La freqüència cardíaca és una mesura de la resposta del teu cos a l'exercici. De forma indirecta, ens indica el treball que estàs realitzant muscularment tot i que pot veure's afectat per factors com la temperatura corporal, la fatiga i la hidratacaió.
        
        Tot i les limitacions, per comprovar la capacitat aeròbica és útil analitzar la relació entre càrrega externa (ritme) i càrrega interna (freqüència cardíaca) i veure com evoluciona en el temps.

        En general, una **FC més baixa per ritmes semblants** (quan és consistent en el temps) indica una millora de la capacitat aeròbica.
        
        """
        with st.form("Configura l'anàlisi:"):
            """
            *Utilitza els filtres per seleccionar entrenaments que realitzis freqüentment i que siguin semblants entre ells.*
            """
            col1, col2 = st.columns(2,gap="medium")
            with col1:
                min_dist = int(df_filtered['distance'].min())
                max_dist = int(df_filtered['distance'].max()) + 1
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
