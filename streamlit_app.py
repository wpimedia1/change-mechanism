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

@st.cache_data(ttl=3600)
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
    # FIX: Exact match only, prevents false positives on strings like "country:us/state:ky"
    return n == "united states" or i == "ocd-jurisdiction/country:us/government" or c == "country"

# ======= API CALLS (Cached) =======
@st.cache_data(ttl=600)
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
def fetch_openstates_bills(person_id, juris_id):
    """Fetches recent bills for a state-level person."""
    # FIX: Added jurisdiction to prevent OpenStates 400 error
    params = {"sponsor": person_id, "jurisdiction": juris_id, "sort": "updated_desc", "per_page": 5}
    r = requests.get(f"{BASE_OS}/bills", headers=HEADERS_OS, params=params, timeout=18)
    if r.status_code == 429:
        time.sleep(1.5)
        return ["- Rate limited (429) — try again shortly"]
    if not r.ok:
        return [f"- OpenStates bills error {r.status_code}"]
    bills = r.json().get("results", [])
    return [f"- {b.get('identifier','?')} — {b.get('title','No title')}" for b in bills] or ["- None"]

@st.cache_data(ttl=86400)
def fetch_congress_members_by_state(state_code):
    """Fetches current Congress members for a specific state to use as a fallback lookup."""
    if not CONGRESS_API_KEY or not state_code: return []
    params = {"format": "json", "api_key": CONGRESS_API_KEY, "limit": 250, "currentMember": "true"}
    r = requests.get(f"{BASE_CONG}/member/{state_code}", params=params, timeout=15)
    return r.json().get("members", []) if r.ok else []

def resolve_bioguide_id(person_dict, state_code):
    """Finds Bioguide ID directly from OpenStates or via a fallback Congress.gov name match."""
    # 1. Native OpenStates check
    for ident in person_dict.get("identifiers", []):
        if ident.get("scheme") == "bioguide":
            return ident.get("identifier")
            
    # 2. Fallback Congress.gov check (Last Name Match to handle nicknames)
    if not state_code: return None
    last_name = person_dict.get("name", "").split()[-1].lower()
    
    members = fetch_congress_members_by_state(state_code)
    matches = [m for m in members if last_name in m.get("name", "").lower()]
    
    # If exactly one matching last name (e.g. only one "Cruz" in TX), return them safely
    if len(matches) == 1:
        return matches[0].get("bioguideId")
        
    # If multiple, require a first name match
    first_name = person_dict.get("name", "").split()[0].lower()
    for m in matches:
        if first_name in m.get("name", "").lower():
            return m.get("bioguideId")
    return None

@st.cache_data(ttl=600)
def fetch_congress_bills(bioguide_id):
    """Fetches recent bills sponsored by a federal member from Congress.gov."""
    if not CONGRESS_API_KEY:
        return ["- congress.gov key not configured"]
    if not bioguide_id:
        return ["- No bioguide ID available to fetch bills"]
        
    # FIX: Uses sponsor endpoint with bioguide_id instead of keyword name search
    params = {"format": "json", "limit": 5, "api_key": CONGRESS_API_KEY}
    r = requests.get(f"{BASE_CONG}/member/{bioguide_id}/sponsored-legislation", headers=HEADERS_CONG, params=params, timeout=16)
    
    if r.status_code == 429:
        time.sleep(1.5)
        return ["- Rate limited (429) — try again shortly"]
    if not r.ok:
        return [f"- Congress.gov error {r.status_code}"]
        
    data = r.json() or {}
    bills = data.get("sponsoredLegislation", [])
    
    results = []
    for b in bills[:5]:
        congress = b.get("congress", "?")
        b_type = b.get("type", "")
        b_num = b.get("number", "")
        title = b.get("title") or "No title available"
        results.append(f"- [{congress} {b_type} {b_num}] {title}")
        
    return results or ["- None"]
    
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
                    st.stop() # FIX: Changed from rerun() so errors stay visible
            
            if lat is None or lng is None:
                output_area.error(jerr("InputError", "Provide coordinates or a City, State string."))
                status.warning("No location provided.")
                st.session_state.running = False
                st.stop()
            
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
                st.stop()

            if not people:
                output_area.warning(jerr("EmptyResults", "No representatives found.", {"lat": lat, "lng": lng}))
                status.warning("No representatives found.")
                st.session_state.running = False
                st.stop()

            # Extract state code for fallback Congress member lookup
            state_code = None
            for p in people:
                jid = p.get("jurisdiction", {}).get("id", "")
                if "state:" in jid:
                    state_code = jid.split("state:")[1].split("/")[0].upper()
                    break

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

                # Bills
                if is_federal(juris):
                    out.append("\n**Federal (Congress.gov)**")
                    
                    # FIX: Use fallback function to get missing Bioguide IDs for reps like Cornyn/Cruz
                    bioguide_id = resolve_bioguide_id(p, state_code)
                    
                    if bioguide_id:
                        out += fetch_congress_bills(bioguide_id)
                    else:
                        out.append("- *Missing Bioguide ID for sponsor search*")
                else:
                    out.append("\n**State (OpenStates)**")
                    out += fetch_openstates_bills(p.get("id", ""), juris.get("id", ""))

                out.append("\n---\n")
                
                # Update status and progressive output
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