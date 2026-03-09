import streamlit as st
import time
import json
import requests

# ======= CONFIG =======
# Load from ./.streamlit/secrets.toml
# https://docs.streamlit.io/develop/api-reference/connections/st.secrets

try:
    OPEN_API_KEY = st.secrets["OPEN_API_KEY"]
except KeyError:
    st.error("Missing `OPEN_API_KEY` in Streamlit secrets. Add it to `.streamlit/secrets.toml`.")
    st.stop()

CONGRESS_API_KEY = st.secrets.get("CONGRESS_API_KEY", "")

BASE_OS = "https://v3.openstates.org"
BASE_CONG = "https://api.congress.gov/v3"
BASE_CENSUS = "https://geocoding.geo.census.gov/geocoder"

HEADERS_OS = {"X-Api-Key": OPEN_API_KEY}

# Current congress number — update when a new congress is seated (Jan of odd years)
CURRENT_CONGRESS = 119

# ======= CONSTANTS =======
GEOCODE_TIMEOUT = 12
CENSUS_TIMEOUT = 15
API_TIMEOUT = 20
BILLS_TIMEOUT = 18
CONGRESS_TIMEOUT = 16
RATE_LIMIT_PAUSE = 2.0
INTER_REP_PAUSE = 0.12
BILLS_PER_PAGE = 5
COOLDOWN_SECONDS = 5


# ======= UTILITIES =======

def format_error(name: str, message: str, meta: dict | None = None) -> str:
    """Return a structured JSON error block for display."""
    error = {"error": {"name": name, "message": message}}
    if meta is not None:
        error["error"]["meta"] = meta
    return "```json\n" + json.dumps(error, indent=2) + "\n```"


# FIPS code to two-letter state abbreviation
FIPS_TO_STATE = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA",
    "08": "CO", "09": "CT", "10": "DE", "11": "DC", "12": "FL",
    "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN",
    "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME",
    "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS",
    "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
    "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND",
    "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI",
    "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT",
    "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI",
    "56": "WY", "60": "AS", "66": "GU", "69": "MP", "72": "PR",
    "78": "VI",
}


# ======= API CALLS (Cached) =======

# --- Geocoding (Nominatim) ---

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


# --- Census Geocoder: lat/lng to state + congressional district ---

@st.cache_data(ttl=3600, show_spinner=False)
def resolve_district(lat: float, lng: float) -> tuple:
    """Use Census Geocoder to get (state_abbr, district_number, error_or_none).

    Census coordinates endpoint: x=longitude, y=latitude (note the swap).
    No API key required.
    """
    try:
        r = requests.get(
            f"{BASE_CENSUS}/geographies/coordinates",
            params={
                "x": lng,
                "y": lat,
                "benchmark": "Public_AR_Current",
                "vintage": "Current_Current",
                "layers": "10",
                "format": "json",
            },
            timeout=CENSUS_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        geographies = data.get("result", {}).get("geographies", {})

        # Key name varies by congress (e.g. "119th Congressional Districts")
        cd_data = None
        for key in geographies:
            if "Congressional" in key:
                districts = geographies[key]
                if districts:
                    cd_data = districts[0]
                break

        if not cd_data:
            return None, None, format_error(
                "CensusError", "No congressional district found for these coordinates."
            )

        state_fips = cd_data.get("STATE", "")
        district_code = cd_data.get("CD", cd_data.get("CDSESSN", ""))

        if not state_fips:
            return None, None, format_error("CensusError", "Missing state FIPS in response.")

        state_abbr = FIPS_TO_STATE.get(state_fips)
        if not state_abbr:
            return None, None, format_error("CensusError", f"Unknown state FIPS: {state_fips}")

        # "00" = at-large (single-district states like WY, VT, AK)
        district_num = int(district_code) if district_code and district_code.isdigit() else 0

        return state_abbr, district_num, None

    except requests.Timeout:
        return None, None, format_error("CensusError", "Census geocoder timed out.")
    except requests.RequestException as e:
        return None, None, format_error("CensusError", str(e))
    except (KeyError, IndexError, ValueError) as e:
        return None, None, format_error("CensusError", f"Unexpected response format: {e}")


# --- OpenStates: state-level reps + bills ---

@st.cache_data(ttl=600, show_spinner=False)
def fetch_state_reps(lat: float, lng: float) -> list:
    """Fetch state-level representatives from OpenStates by coordinates."""
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


# --- Congress.gov: federal reps + sponsored legislation ---

@st.cache_data(ttl=600, show_spinner=False)
def fetch_federal_reps(state: str, district: int) -> list[dict]:
    """Fetch current federal reps (senators + house member) from Congress.gov.

    Uses /member/congress/{congress}/{state} with currentMember=true.
    Filters house members to the matching district number.
    """
    if not CONGRESS_API_KEY:
        return []

    members = []
    params = {
        "format": "json",
        "api_key": CONGRESS_API_KEY,
        "currentMember": "true",
        "limit": 20,
    }

    try:
        r = requests.get(
            f"{BASE_CONG}/member/congress/{CURRENT_CONGRESS}/{state}",
            params=params,
            timeout=CONGRESS_TIMEOUT,
        )
        if not r.ok:
            return []

        for m in r.json().get("members", []):
            terms = m.get("terms", {}).get("item", [])
            chamber = ""
            if terms:
                chamber = terms[-1].get("chamber", "")

            members.append({
                "name": m.get("name", "Unknown"),
                "party": m.get("partyName", "Unknown"),
                "role": "Senator" if chamber == "Senate" else "Representative",
                "district": m.get("district"),
                "bioguideId": m.get("bioguideId", ""),
            })
    except requests.RequestException:
        return []

    # Keep all senators; for house, only the matching district
    filtered = []
    for m in members:
        if m["role"] == "Senator":
            filtered.append(m)
        elif m["role"] == "Representative":
            member_district = m.get("district")
            if member_district is not None:
                try:
                    if int(member_district) == district:
                        filtered.append(m)
                except (ValueError, TypeError):
                    pass
            elif district == 0:
                # At-large: single rep, no district number
                filtered.append(m)

    return filtered


@st.cache_data(ttl=600, show_spinner=False)
def fetch_sponsored_bills(bioguide_id: str) -> list[str]:
    """Fetch recent sponsored legislation by bioguide ID.

    Uses /member/{bioguideId}/sponsored-legislation — returns actual bills
    this member introduced, not keyword search noise.
    """
    if not CONGRESS_API_KEY or not bioguide_id:
        return ["- Congress.gov key not configured"]

    params = {
        "format": "json",
        "api_key": CONGRESS_API_KEY,
        "limit": BILLS_PER_PAGE,
    }
    try:
        r = requests.get(
            f"{BASE_CONG}/member/{bioguide_id}/sponsored-legislation",
            params=params,
            timeout=CONGRESS_TIMEOUT,
        )
        if r.status_code == 429:
            time.sleep(RATE_LIMIT_PAUSE)
            return ["- Rate limited (429) — try again shortly"]
        if not r.ok:
            return [f"- Congress.gov error {r.status_code}"]
        data = r.json() or {}
        bills = data.get("sponsoredLegislation", [])
        return [
            f"- [{b.get('congress', '')} {b.get('type', '')}{b.get('number', '')}] "
            f"{b.get('latestTitle', b.get('title', 'No title'))}"
            for b in bills
        ] or ["- No sponsored legislation found"]
    except requests.RequestException as e:
        return [f"- Congress.gov request failed: {e}"]


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

            loc_str = f"({lat:.6f}, {lng:.6f})"
            out = [f"**Location:** {loc_str}", ""]
            total_idx = 0

            # ============================================================
            # 2) STATE-LEVEL REPS (OpenStates)
            # ============================================================
            status.update(label=f"\U0001f4cd Fetching state reps for {loc_str}\u2026")
            output_area.markdown(f"\U0001f4cd Resolving representatives for **{loc_str}**\u2026")

            try:
                state_people = fetch_state_reps(lat, lng)
            except requests.HTTPError as he:
                state_people = []
                out.append(f"*OpenStates error: {he}*\n")

            if state_people:
                out.append("## State Representatives\n")

                for p in state_people:
                    total_idx += 1
                    name = p.get("name", "N/A")
                    party = p.get("party", "Unknown")
                    role = (p.get("current_role") or {}).get("title", "Unknown")
                    juris = p.get("jurisdiction") or {}

                    out.append(f"### {name} ({party}) \u2014 {role}")
                    out.append(f"Jurisdiction: {juris.get('name', '')}")

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

                    out.append("\n**Recent Bills (OpenStates)**")
                    out += fetch_openstates_bills(person_id=p.get("id", ""))
                    out.append("\n---\n")

                    status_msg = f"State rep {total_idx}: {name}\u2026"
                    status.update(label=status_msg)
                    output_area.markdown("\n".join(out) + f"\n\n*({status_msg})*")
                    time.sleep(INTER_REP_PAUSE)
            else:
                out.append("*No state representatives found via OpenStates.*\n")

            # ============================================================
            # 3) FEDERAL REPS (Census Geocoder + Congress.gov)
            # ============================================================
            if CONGRESS_API_KEY:
                status.update(label="Resolving congressional district\u2026")

                state_abbr, district_num, census_err = resolve_district(lat, lng)

                if census_err:
                    out.append("\n## Federal Representatives\n")
                    out.append(f"*Could not resolve congressional district.*\n")
                else:
                    district_label = (
                        f"{state_abbr} At-Large" if district_num == 0
                        else f"{state_abbr}-{district_num}"
                    )
                    out.append(f"\n## Federal Representatives ({district_label})\n")

                    status.update(label=f"Fetching federal reps for {district_label}\u2026")
                    output_area.markdown("\n".join(out) + "\n\n*(Fetching federal reps\u2026)*")

                    fed_members = fetch_federal_reps(state_abbr, district_num)

                    if not fed_members:
                        out.append("*No federal representatives found via Congress.gov.*\n")
                    else:
                        for m in fed_members:
                            total_idx += 1
                            name = m["name"]
                            party = m["party"]
                            role = m["role"]
                            bioguide = m["bioguideId"]

                            out.append(f"### {name} ({party}) \u2014 {role}")

                            out.append("\n**Recent Sponsored Legislation**")
                            out += fetch_sponsored_bills(bioguide)
                            out.append("\n---\n")

                            status_msg = f"Federal rep {total_idx}: {name}\u2026"
                            status.update(label=status_msg)
                            output_area.markdown(
                                "\n".join(out) + f"\n\n*({status_msg})*"
                            )
                            time.sleep(INTER_REP_PAUSE)
            else:
                out.append("\n## Federal Representatives\n")
                out.append("*Add `CONGRESS_API_KEY` to secrets to enable federal rep lookup.*\n")

            # 4) Done
            if total_idx == 0:
                output_area.warning("No representatives found for this location.")
                status.update(label="No results.", state="error")
            else:
                status.update(
                    label=f"Lookup complete! ({total_idx} reps found)", state="complete"
                )
                output_area.markdown("\n".join(out))

        except Exception as e:
            output_area.error(format_error("UnhandledException", str(e)))
            status.update(label="An unexpected error occurred.", state="error")

        finally:
            st.session_state.running = False