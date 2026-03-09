import streamlit as st
import time
import json
import requests

# ======= CONFIG =======
# Load from ./.streamlit/secrets.toml
# Use bracket notation with KeyError handling per Streamlit docs:
# https://docs.streamlit.io/develop/api-reference/connections/st.secrets

try:
    OPEN_API_KEY = st.secrets["OPEN_API_KEY"]
except KeyError:
    st.error("Missing `OPEN_API_KEY` in Streamlit secrets. Add it to `.streamlit/secrets.toml`.")
    st.stop()

CONGRESS_API_KEY = st.secrets.get("CONGRESS_API_KEY", "")

BASE_OS = "https://v3.openstates.org"
BASE_CONG = "https://api.congress.gov/v3"

HEADERS_OS = {"X-Api-Key": OPEN_API_KEY}
HEADERS_CONG = {"X-Api-Key": CONGRESS_API_KEY} if CONGRESS_API_KEY else {}

# ======= CONSTANTS =======
GEOCODE_TIMEOUT = 12
API_TIMEOUT = 20
BILLS_TIMEOUT = 18
CONGRESS_TIMEOUT = 16
RATE_LIMIT_PAUSE = 2.0
INTER_REP_PAUSE = 0.12
MAX_RETRIES = 1
BILLS_PER_PAGE = 5
COOLDOWN_SECONDS = 5  # Min seconds between lookups per session


# ======= UTILITIES =======

def format_error(name: str, message: str, meta: dict | None = None) -> str:
    """Return a structured JSON error block for display."""
    error = {"error": {"name": name, "message": message}}
    if meta is not None:
        error["error"]["meta"] = meta
    return "```json\n" + json.dumps(error, indent=2) + "\n```"


def is_federal(juris: dict) -> bool:
    """Check if a jurisdiction dict represents US Federal government."""
    if not juris:
        return False
    name = (juris.get("name") or "").lower()
    jid = (juris.get("id") or "").lower()
    classification = (juris.get("classification") or "").lower()
    return "united states" in name or "country:us" in jid or classification == "country"


# ======= API CALLS (Cached) =======

@st.cache_data(ttl=3600, show_spinner=False)
def geocode(location: str) -> tuple:
    """Convert a location string to (lat, lng, error_or_none)."""
    if not location or not location.strip():
        return None, None, format_error("GeoError", "No location provided.")
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": location.strip(), "format": "json", "limit": 1},
            headers={"User-Agent": "WhoRepMe-Streamlit/1.0"},
            timeout=GEOCODE_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        if not data:
            return None, None, format_error("GeoError", f"No results for '{location}'.")
        return float(data[0]["lat"]), float(data[0]["lon"]), None
    except requests.Timeout:
        return None, None, format_error("GeoError", "Geocoding request timed out.")
    except requests.RequestException as e:
        return None, None, format_error("GeoError", str(e))


@st.cache_data(ttl=600, show_spinner=False)
def fetch_people(lat: float, lng: float) -> list:
    """Fetch representatives from OpenStates API by coordinates."""
    params = {"lat": lat, "lng": lng, "include": ["offices"]}
    r = requests.get(
        f"{BASE_OS}/people.geo",
        headers=HEADERS_OS,
        params=params,
        timeout=API_TIMEOUT,
    )
    if r.status_code == 429:
        time.sleep(RATE_LIMIT_PAUSE)
        r = requests.get(
            f"{BASE_OS}/people.geo",
            headers=HEADERS_OS,
            params=params,
            timeout=API_TIMEOUT,
        )
    r.raise_for_status()
    return r.json().get("results", [])


@st.cache_data(ttl=600, show_spinner=False)
def fetch_openstates_bills(person_id: str) -> list[str]:
    """Fetch recent bills for a state-level person from OpenStates."""
    params = {"sponsor": person_id, "sort": "updated_desc", "per_page": BILLS_PER_PAGE}
    r = requests.get(
        f"{BASE_OS}/bills",
        headers=HEADERS_OS,
        params=params,
        timeout=BILLS_TIMEOUT,
    )
    if r.status_code == 429:
        time.sleep(RATE_LIMIT_PAUSE)
        return ["- Rate limited (429) — try again shortly"]
    if not r.ok:
        return [f"- OpenStates bills error {r.status_code}"]
    bills = r.json().get("results", [])
    return [
        f"- {b.get('identifier', '?')} — {b.get('title', 'No title')}"
        for b in bills
    ] or ["- None found"]


@st.cache_data(ttl=600, show_spinner=False)
def fetch_congress_bills_by_name(name: str) -> list[str]:
    """Fetch recent bills from Congress.gov by legislator name."""
    if not CONGRESS_API_KEY:
        return ["- Congress.gov key not configured"]
    params = {
        "q": name,
        "format": "json",
        "limit": BILLS_PER_PAGE,
        "api_key": CONGRESS_API_KEY,
    }
    r = requests.get(
        f"{BASE_CONG}/bill",
        headers=HEADERS_CONG,
        params=params,
        timeout=CONGRESS_TIMEOUT,
    )
    if r.status_code == 429:
        time.sleep(RATE_LIMIT_PAUSE)
        return ["- Rate limited (429) — try again shortly"]
    if not r.ok:
        return [f"- Congress.gov error {r.status_code}"]
    data = r.json() or {}
    bills = data.get("bills", [])
    return [
        f"- [{b.get('congress', '')} {b.get('number', '')}] {b.get('title', 'No title')}"
        for b in bills
    ] or ["- None found"]


# ======= UI =======

st.set_page_config(page_title="Who Represents Me", layout="wide")
st.title("\U0001f3db\ufe0f Who Represents Me")

col1, col2, col3 = st.columns(3)
with col1:
    loc = st.text_input("Location (City, State)", placeholder="e.g., Detroit, MI")
with col2:
    lat_in = st.number_input(
        "Latitude", value=None, placeholder="e.g., 42.3314", format="%.6f"
    )
with col3:
    lng_in = st.number_input(
        "Longitude", value=None, placeholder="e.g., -83.0458", format="%.6f"
    )

# Initialize session state
if "running" not in st.session_state:
    st.session_state.running = False
if "last_lookup_time" not in st.session_state:
    st.session_state.last_lookup_time = 0.0

output_area = st.empty()
output_area.markdown("\U0001f539 Ready")

if st.button("Find My Representatives", type="primary", disabled=st.session_state.running):

    # ---- Cooldown gate ----
    elapsed = time.time() - st.session_state.last_lookup_time
    if elapsed < COOLDOWN_SECONDS:
        wait = int(COOLDOWN_SECONDS - elapsed) + 1
        output_area.warning(f"Please wait {wait}s before searching again.")
        st.stop()

    # Record timestamp BEFORE any API work so error/exception paths are also covered.
    # This prevents a failed request from being retried instantly.
    st.session_state.last_lookup_time = time.time()

    st.session_state.running = True
    lat, lng = lat_in, lng_in
    out: list[str] = []

    with st.status("\u23f3 Starting lookup\u2026") as status:
        try:
            # 1) Resolve coordinates
            if loc:
                status.update(label=f"Geocoding '{loc}'\u2026")
                lat, lng, geo_err = geocode(loc)
                if geo_err:
                    output_area.error(geo_err)
                    status.update(label="Geocoding failed.", state="error")
                    st.session_state.running = False
                    st.stop()

            if lat is None or lng is None:
                output_area.error(
                    format_error("InputError", "Provide coordinates or a City, State string.")
                )
                status.update(label="No location provided.", state="error")
                st.session_state.running = False
                st.stop()

            # 2) Fetch people
            loc_str = f"({lat:.6f}, {lng:.6f})"
            status.update(label=f"\U0001f4cd Resolving reps for {loc_str}\u2026")
            output_area.markdown(f"\U0001f4cd Resolving representatives for **{loc_str}**\u2026")

            try:
                people = fetch_people(lat, lng)
            except requests.HTTPError as he:
                output_area.error(format_error("HTTPError", str(he)))
                status.update(label="Failed to fetch representatives.", state="error")
                st.session_state.running = False
                st.stop()

            if not people:
                output_area.warning(
                    format_error("EmptyResults", "No representatives found.", {"lat": lat, "lng": lng})
                )
                status.update(label="No representatives found.", state="error")
                st.session_state.running = False
                st.stop()

            status.update(label=f"\u2705 Found {len(people)} reps. Fetching bills\u2026")

            # 3) Build output
            out = [f"**Location:** {loc_str}", ""]

            for idx, p in enumerate(people, start=1):
                name = p.get("name", "N/A")
                party = p.get("party", "Unknown")
                role = (p.get("current_role") or {}).get("title", "Unknown")
                juris = p.get("jurisdiction") or {}

                out.append(f"### {name} ({party}) — {role}")
                out.append(f"Jurisdiction: {juris.get('name', '')}")

                # Contact info
                offices = p.get("offices", []) or p.get("contact_details", [])
                if offices:
                    out.append("**Contact:**")
                    for o in offices:
                        if isinstance(o, dict):
                            addr = o.get("address") or o.get("value") or ""
                            voice = o.get("voice") or o.get("voice_number") or ""
                            line = ", ".join(s for s in [addr, voice] if s)
                            if line:
                                out.append(f"- {line}")

                # Bills
                if is_federal(juris):
                    out.append("\n**Federal (Congress.gov)**")
                    out += fetch_congress_bills_by_name(name)
                else:
                    out.append("\n**State (OpenStates)**")
                    out += fetch_openstates_bills(person_id=p.get("id", ""))

                out.append("\n---\n")

                # Progressive update
                status_msg = f"Fetching bills for {name}\u2026 ({idx}/{len(people)})"
                status.update(label=status_msg)
                output_area.markdown("\n".join(out) + f"\n\n*({status_msg})*")

                time.sleep(INTER_REP_PAUSE)

            # 4) Done
            status.update(label="Lookup complete!", state="complete")
            output_area.markdown("\n".join(out))

        except Exception as e:
            output_area.error(format_error("UnhandledException", str(e)))
            status.update(label="An unexpected error occurred.", state="error")

        finally:
            st.session_state.running = False