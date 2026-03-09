import streamlit as st
import os, time, json, requests

# ======= CONFIG (using st.secrets) =======
# Load from ./.streamlit/secrets.toml
OPEN_API_KEY = st.secrets.get("OPEN_API_KEY")
CONGRESS_API_KEY = st.secrets.get("CONGRESS_API_KEY", "")

if not OPEN_API_KEY:
    st.error("Missing OPEN_API_KEY in Streamlit secrets. Please add it to ./.streamlit/secrets.toml")
    st.stop()

BASE_OS = "https://v3.openstates.org"
BASE_CONG = "https://api.congress.gov/v3"

HEADERS_OS = {"X-Api-Key": OPEN_API_KEY}
HEADERS_CONG = {"X-Api-Key": CONGRESS_API_KEY} if CONGRESS_API_KEY else {}

# ======= UTIL =======
# (These functions are copied directly from your script)

def jerr(name, message, meta=None):
    """Formats an error as a JSON markdown block."""
    import json
    error = {
        "error": {
            "name": name,
            "message": message
        }
    }
    if meta is not None:
        error["error"]["meta"] = meta
    return "```json\n" + json.dumps(error, indent=2) + "\n```"

@st.cache_data(ttl=3600) # Cache geocoding results for 1 hour
def geocode(location):
    """Converts a location string to (lat, lng) or returns an error."""
    if not location:
        return None, None, jerr("GeoError", "No location provided.")
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": location, "format": "json", "limit": 1},
            headers={"User-Agent": "WhoRepMe-Streamlit/1.0"},
            timeout=12,
        )
        r.raise_for_status()
        data = r.json()
        if not data:
            return None, None, jerr("GeoError", f"No results for '{location}'.")
        return float(data[0]["lat"]), float(data[0]["lon"]), None
    except Exception as e:
        return None, None, jerr("GeoError", str(e))

def is_federal(juris: dict) -> bool:
    """Checks if a jurisdiction dict represents the US Federal government."""
    if not juris:
        return False
    n = (juris.get("name") or "").lower()
    i = (juris.get("id") or "").lower()
    c = (juris.get("classification") or "").lower()
    return "united states" in n or "country:us" in i or c == "country"

# ======= API CALLS (Cached) =======
@st.cache_data(ttl=600) # Cache people results for 10 minutes
def fetch_people(lat, lng):
    """Fetches people from OpenStates API by coordinates."""
    params = {"lat": lat, "lng": lng, "include": ["offices"]}
    r = requests.get(f"{BASE_OS}/people.geo", headers=HEADERS_OS, params=params, timeout=20)
    if r.status_code == 429:
        time.sleep(2.0)
        r = requests.get(f"{BASE_OS}/people.geo", headers=HEADERS_OS, params=params, timeout=20)
    r.raise_for_status()
    return r.json().get("results", [])

@st.cache_data(ttl=600)
def fetch_openstates_bills(person_id):
    """Fetches recent bills for a state-level person."""
    params = {"sponsor": person_id, "sort": "updated_desc", "per_page": 5}
    r = requests.get(f"{BASE_OS}/bills", headers=HEADERS_OS, params=params, timeout=18)
    if r.status_code == 429:
        time.sleep(1.5)
        return ["- Rate limited (429) — try again shortly"]
    if not r.ok:
        return [f"- OpenStates bills error {r.status_code}"]
    bills = r.json().get("results", [])
    return [f"- {b.get('identifier','?')} — {b.get('title','No title')}" for b in bills] or ["- None"]

@st.cache_data(ttl=600)
def fetch_congress_bills_by_name(name):
    """Fetches recent bills from Congress.gov by name."""
    if not CONGRESS_API_KEY:
        return ["- congress.gov key not configured"]
    params = {"q": name, "format": "json", "limit": 5, "api_key": CONGRESS_API_KEY}
    r = requests.get(f"{BASE_CONG}/bill", headers=HEADERS_CONG, params=params, timeout=16)
    if r.status_code == 429:
        time.sleep(1.5)
        return ["- Rate limited (429) — try again shortly"]
    if not r.ok:
        return [f"- Congress.gov error {r.status_code}"]
    data = r.json() or {}
    bills = data.get("bills", [])
    return [f"- [{b.get('congress','')} {b.get('number','')}] {b.get('title','No title')}" for b in bills] or ["- None"]
    
# ======= UI =======
st.set_page_config(page_title="Who Represents Me", layout="wide")
st.title("🏛️ Who Represents Me")

col1, col2, col3 = st.columns(3)
with col1:
    loc = st.text_input("Location (City, State)", placeholder="e.g., Detroit, MI")
with col2:
    # Use format="%.6f" to match the precision from your Gradio app
    lat_in = st.number_input("Latitude", value=None, placeholder="e.g., 42.3314", format="%.6f")
with col3:
    lng_in = st.number_input("Longitude", value=None, placeholder="e.g., -83.0458", format="%.6f")

# Use st.session_state to mimic the RUN_LOCK and prevent multiple clicks
if 'running' not in st.session_state:
    st.session_state.running = False

# This is the main output area
output_area = st.empty()
output_area.markdown("🔹 Ready")

if st.button("Find My Representatives", type="primary", disabled=st.session_state.running):
    st.session_state.running = True
    lat, lng = lat_in, lng_in
    out = [] # This will store our markdown lines
    
    # st.status is the Streamlit equivalent of your streaming loader
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
                    st.rerun() # Use rerun to re-enable button
            
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
            except requests.HTTPError as he:
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

            # 3) Build final Markdown (with gentle pacing)
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

                # Bills: use OpenStates for state/local; Congress.gov for federal
                if is_federal(juris):
                    out.append("\n**Federal (Congress.gov)**")
                    out += fetch_congress_bills_by_name(name)
                else:
                    out.append("\n**State (OpenStates)**")
                    out += fetch_openstates_bills(person_id=p.get("id",""))

                out.append("\n---\n")
                
                # Update status and progressive output
                status_msg = f"Fetching bills for {name}... ({idx}/{len(people)})"
                status.update(label=status_msg)
                output_area.markdown("\n".join(out) + f"\n\n*({status_msg})*")
                
                # small pause between people
                time.sleep(0.12)

            # 4) Final update
            status.success("Lookup Complete!")
            output_area.markdown("\n".join(out))
            
        except Exception as e:
            # General error catch-all
            output_area.error(jerr("UnhandledException", str(e)))
            status.error("An unexpected error occurred.")
        
        finally:
            # MUST reset the lock
            st.session_state.running = False
            # We don't rerun here, so the final output stays