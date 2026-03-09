import streamlit as st
import json
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ======= CONFIG (using st.secrets) =======
# Load from ./.streamlit/secrets.toml
OPEN_API_KEY = st.secrets.get("OPEN_API_KEY")
CONGRESS_API_KEY = st.secrets.get("CONGRESS_API_KEY", "")
MAPS_API_KEY = st.secrets.get("MAPS_API_KEY")

if not OPEN_API_KEY:
    st.error("Missing OPEN_API_KEY in Streamlit secrets. Please add it to ./.streamlit/secrets.toml")
    st.stop()
    
if not MAPS_API_KEY:
    st.warning("Missing MAPS_API_KEY. Geocoding from City/State will fail. Please add it to your secrets.")

BASE_OS = "https://v3.openstates.org"
BASE_CONG = "https://api.congress.gov/v3"

HEADERS_OS = {"X-Api-Key": OPEN_API_KEY}
HEADERS_CONG = {"X-Api-Key": CONGRESS_API_KEY} if CONGRESS_API_KEY else {}

# ======= SESSION & RETRIES =======
@st.cache_resource
def get_requests_session():
    """Creates a requests session with connection pooling and exponential backoff."""
    session = requests.Session()
    # Retries for 429 (Too Many Requests) and 5xx Server Errors
    retry = Retry(
        total=5,
        backoff_factor=1, # Delays: 1s, 2s, 4s, 8s, 16s...
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session

http_session = get_requests_session()

# ======= UTIL =======
def jerr(name, message, meta=None):
    """Formats an error as a JSON markdown block."""
    error = {
        "error": {
            "name": name,
            "message": message
        }
    }
    if meta is not None:
        error["error"]["meta"] = meta
    return "```json\n" + json.dumps(error, indent=2) + "\n```"

def is_federal(juris: dict) -> bool:
    """Checks if a jurisdiction dict represents the US Federal government."""
    if not juris:
        return False
    n = (juris.get("name") or "").lower()
    i = (juris.get("id") or "").lower()
    c = (juris.get("classification") or "").lower()
    return "united states" in n or "country:us" in i or c == "country"

# ======= API CALLS =======
@st.cache_data(ttl=3600)
def _do_geocode(location):
    """Internal cached function to hit Google Maps API. Raises ValueError on failure so errors aren't cached."""
    if not MAPS_API_KEY:
        raise ValueError("MAPS_API_KEY is not configured. Please add it to secrets.toml")
    
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": location, "key": MAPS_API_KEY}
    r = http_session.get(url, params=params, timeout=12)
    r.raise_for_status()
    data = r.json()
    
    if data.get("status") != "OK" or not data.get("results"):
        raise ValueError(f"No results found for '{location}'. (Status: {data.get('status', 'Unknown')})")
        
    loc = data["results"][0]["geometry"]["location"]
    return loc["lat"], loc["lng"]

def geocode(location):
    """Wrapper around cached geocode to catch errors and return them cleanly."""
    if not location:
        return None, None, jerr("GeoError", "No location provided.")
    try:
        lat, lng = _do_geocode(location)
        return lat, lng, None
    except Exception as e:
        return None, None, jerr("GeoError", str(e))

@st.cache_data(ttl=600)
def fetch_people(lat, lng):
    """Fetches people from OpenStates API by coordinates."""
    params = {"lat": lat, "lng": lng, "include": ["offices"]}
    # Exponential backoff automatically handles 429s here now
    r = http_session.get(f"{BASE_OS}/people.geo", headers=HEADERS_OS, params=params, timeout=20)
    r.raise_for_status()
    return r.json().get("results", [])

@st.cache_data(ttl=600)
def fetch_openstates_bills(person_id):
    """Fetches recent bills for a state-level person."""
    params = {"sponsor": person_id, "sort": "updated_desc", "per_page": 5}
    try:
        r = http_session.get(f"{BASE_OS}/bills", headers=HEADERS_OS, params=params, timeout=18)
        r.raise_for_status()
        bills = r.json().get("results", [])
        return [f"- {b.get('identifier','?')} — {b.get('title','No title')}" for b in bills] or ["- None"]
    except requests.exceptions.RequestException as e:
        return [f"- OpenStates bills error: {str(e)}"]

@st.cache_data(ttl=600)
def fetch_congress_bills_by_name(name):
    """Fetches recent bills from Congress.gov by name."""
    if not CONGRESS_API_KEY:
        return ["- congress.gov key not configured"]
    params = {"q": name, "format": "json", "limit": 5, "api_key": CONGRESS_API_KEY}
    try:
        r = http_session.get(f"{BASE_CONG}/bill", headers=HEADERS_CONG, params=params, timeout=16)
        r.raise_for_status()
        data = r.json() or {}
        bills = data.get("bills", [])
        return [f"- [{b.get('congress','')} {b.get('number','')}] {b.get('title','No title')}" for b in bills] or ["- None"]
    except requests.exceptions.RequestException as e:
        return [f"- Congress.gov error: {str(e)}"]
    
# ======= UI =======
st.set_page_config(page_title="Who Represents Me", layout="wide")
st.title("🏛️ Who Represents Me")

col1, col2, col3 = st.columns(3)
with col1:
    loc = st.text_input("Location (City, State)", placeholder="e.g., Detroit, MI")
with col2:
    lat_in = st.number_input("Latitude", value=None, placeholder="e.g., 42.3314", format="%.6f")
with col3:
    lng_in = st.number_input("Longitude", value=None, placeholder="e.g., -83.0458", format="%.6f")

if 'running' not in st.session_state:
    st.session_state.running = False

output_area = st.empty()
output_area.markdown("🔹 Ready")

if st.button("Find My Representatives", type="primary", disabled=st.session_state.running):
    st.session_state.running = True
    lat, lng = lat_in, lng_in
    out = [] 
    
    with st.status("⏳ Starting lookup…") as status:
        try:
            # 1) Resolve coordinates
            if loc:
                status.update(label=f"Geocoding '{loc}'...")
                lat, lng, geo_err = geocode(loc)
                if geo_err:
                    output_area.error(geo_err)
                    status.error("Geocoding failed.")
                    st.session_state.running = False
                    st.rerun()
            
            if lat is None or lng is None:
                output_area.error(jerr("InputError", "Provide coordinates or a City, State string."))
                status.warning("No location provided.")
                st.session_state.running = False
                st.rerun()
            
            # 2) People
            loc_str = f"({lat:.6f}, {lng:.6f})"
            status.update(label=f"📍 Resolving reps for {loc_str}…")
            output_area.markdown(f"📍 Resolving representatives for **{loc_str}**…")

            try:
                people = fetch_people(lat, lng)
            except requests.exceptions.RequestException as he:
                output_area.error(jerr("HTTPError", str(he)))
                status.error("Failed to fetch representatives.")
                st.session_state.running = False
                st.rerun()
                
            if not people:
                output_area.warning(jerr("EmptyResults", "No representatives found.", {"lat": lat, "lng": lng}))
                status.warning("No representatives found.")
                st.session_state.running = False
                st.rerun()

            status.update(label=f"✅ Found {len(people)} reps. Fetching bills…")

            # 3) Build final Markdown
            out = [f"**Location:** {loc_str}", ""]
            
            for idx, p in enumerate(people, start=1):
                name = p.get("name", "N/A")
                party = p.get("party", "Unknown")
                role = (p.get("current_role") or {}).get("title", "Unknown")
                juris = p.get("jurisdiction") or {}
                out.append(f"### {name} ({party}) — {role}")
                out.append(f"Jurisdiction: {juris.get('name','')}")

                # Offices / contact
                offices = p.get("offices", []) or p.get("contact_details", [])
                if offices:
                    out.append("**Contact:**")
                    for o in offices:
                        if isinstance(o, dict):
                            addr = o.get("address") or o.get("value") or ""
                            voice = o.get("voice") or o.get("voice_number") or ""
                            line = ", ".join([s for s in [addr, voice] if s])
                            if line:
                                out.append(f"- {line}")

                if is_federal(juris):
                    out.append("\n**Federal (Congress.gov)**")
                    out += fetch_congress_bills_by_name(name)
                else:
                    out.append("\n**State (OpenStates)**")
                    out += fetch_openstates_bills(person_id=p.get("id",""))

                out.append("\n---\n")
                
                status_msg = f"Fetching bills for {name}... ({idx}/{len(people)})"
                status.update(label=status_msg)
                output_area.markdown("\n".join(out) + f"\n\n*({status_msg})*")
                
                time.sleep(0.12)

            # 4) Final update
            status.success("Lookup Complete!")
            output_area.markdown("\n".join(out))
            
        except Exception as e:
            output_area.error(jerr("UnhandledException", str(e)))
            status.error("An unexpected error occurred.")
        
        finally:
            st.session_state.running = False