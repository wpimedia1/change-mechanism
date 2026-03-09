import streamlit as st
import time
import json
import requests

# ======= CONFIG =======
try:
    OPEN_API_KEY = st.secrets["OPEN_API_KEY"]
except KeyError:
    st.error("Missing `OPEN_API_KEY` in Streamlit secrets.")
    st.stop()

CONGRESS_API_KEY = st.secrets.get("CONGRESS_API_KEY", "")

BASE_OS = "https://v3.openstates.org"
BASE_CONG = "https://api.congress.gov/v3"
BASE_CENSUS = "https://geocoding.geo.census.gov/geocoder"

HEADERS_OS = {"X-Api-Key": OPEN_API_KEY}

CURRENT_CONGRESS = 119

# ======= CONSTANTS =======
GEOCODE_TIMEOUT = 12
CENSUS_TIMEOUT = 15
API_TIMEOUT = 20
CONGRESS_TIMEOUT = 16
RATE_LIMIT_PAUSE = 2.0
INTER_REP_PAUSE = 0.15
BILLS_PER_PAGE = 5
COOLDOWN_SECONDS = 5

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


# ======= UTILITIES =======

def format_error(name: str, message: str) -> str:
    return f"**{name}:** {message}"


def safe_get(url, params=None, headers=None, timeout=15, label="API"):
    """Make a GET request with retry on 429. Returns (response, error_string)."""
    try:
        r = requests.get(url, params=params, headers=headers, timeout=timeout)
        if r.status_code == 429:
            time.sleep(RATE_LIMIT_PAUSE)
            r = requests.get(url, params=params, headers=headers, timeout=timeout)
        if not r.ok:
            return None, f"{label} returned HTTP {r.status_code}"
        return r, None
    except requests.Timeout:
        return None, f"{label} request timed out"
    except requests.RequestException as e:
        return None, f"{label} error: {e}"


# ======= GEOCODING =======

@st.cache_data(ttl=3600, show_spinner=False)
def geocode(location: str):
    """Nominatim geocode. Only caches success. Errors raise."""
    r = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": location.strip(), "format": "json", "limit": 1},
        headers={"User-Agent": "ChangeMechanism-Streamlit/1.0"},
        timeout=GEOCODE_TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()
    if not data:
        return None, None, f"No geocoding results for '{location}'."
    return float(data[0]["lat"]), float(data[0]["lon"]), None


@st.cache_data(ttl=3600, show_spinner=False)
def resolve_district(lat: float, lng: float):
    """Census Geocoder: lat/lng -> (state_abbr, district_num, error). Errors raise."""
    r = requests.get(
        f"{BASE_CENSUS}/geographies/coordinates",
        params={
            "x": lng, "y": lat,
            "benchmark": "Public_AR_Current",
            "vintage": "Current_Current",
            "layers": "all",
            "format": "json",
        },
        timeout=CENSUS_TIMEOUT,
    )
    r.raise_for_status()
    geographies = r.json().get("result", {}).get("geographies", {})
    cd_data = None
    for key in geographies:
        if "Congressional" in key:
            districts = geographies[key]
            if districts:
                cd_data = districts[0]
            break
    if not cd_data:
        return None, None, "No congressional district found for these coordinates."
    state_fips = cd_data.get("STATE", "")

    # District code field name changes per congress: CD119, CD118, CD, CDSESSN, etc.
    district_code = ""
    for field_key in cd_data:
        if field_key.startswith("CD") and field_key != "CENTLAT" and field_key != "CENTLON":
            val = cd_data[field_key]
            if val and str(val).isdigit():
                district_code = str(val)
                break
    if not district_code:
        # Fallback: try known field names
        district_code = cd_data.get("CD", cd_data.get("CDSESSN", ""))

    state_abbr = FIPS_TO_STATE.get(state_fips)
    if not state_abbr:
        return None, None, f"Unknown state FIPS: {state_fips}"
    district_num = int(district_code) if district_code and str(district_code).isdigit() else 0
    return state_abbr, district_num, None


# ======= OPENSTATES: STATE REPS + CONTACT + BILLS =======

@st.cache_data(ttl=600, show_spinner=False)
def fetch_state_reps(lat: float, lng: float):
    """Returns (list_of_people, error_string)."""
    r, err = safe_get(
        f"{BASE_OS}/people.geo",
        params={"lat": lat, "lng": lng, "include": ["offices"]},
        headers=HEADERS_OS,
        timeout=API_TIMEOUT,
        label="OpenStates people.geo",
    )
    if err:
        return [], err
    return r.json().get("results", []), None


@st.cache_data(ttl=600, show_spinner=False)
def fetch_state_bills(person_id: str) -> list[str]:
    r, err = safe_get(
        f"{BASE_OS}/bills",
        params={"sponsor": person_id, "sort": "updated_desc", "per_page": BILLS_PER_PAGE},
        headers=HEADERS_OS,
        timeout=API_TIMEOUT,
        label="OpenStates bills",
    )
    if err:
        return [f"- {err}"]
    bills = r.json().get("results", [])
    return [
        f"- **{b.get('identifier', '?')}** — {b.get('title', 'No title')}"
        for b in bills
    ] or ["- No recent bills found"]


def render_state_rep(p: dict) -> list[str]:
    """Render one state-level rep as markdown lines."""
    lines = []
    name = p.get("name", "N/A")
    party = p.get("party", "Unknown")
    role = (p.get("current_role") or {}).get("title", "Unknown")
    juris = p.get("jurisdiction") or {}

    lines.append(f"### {name} ({party}) — {role}")
    lines.append(f"**Jurisdiction:** {juris.get('name', 'Unknown')}")

    # Contact: offices array from OpenStates
    offices = p.get("offices", []) or p.get("contact_details", [])
    if offices:
        lines.append("**Contact:**")
        for o in offices:
            if not isinstance(o, dict):
                continue
            parts = []
            addr = o.get("address") or o.get("value") or ""
            voice = o.get("voice") or o.get("voice_number") or ""
            fax = o.get("fax") or ""
            email = o.get("email") or ""
            name_label = o.get("name") or o.get("note") or ""
            if name_label:
                parts.append(f"**{name_label}**")
            if addr:
                parts.append(addr)
            if voice:
                parts.append(f"Phone: {voice}")
            if fax:
                parts.append(f"Fax: {fax}")
            if email:
                parts.append(f"Email: {email}")
            if parts:
                lines.append("- " + " | ".join(parts))

    # Links
    links = p.get("links", [])
    if links:
        for lnk in links:
            if isinstance(lnk, dict) and lnk.get("url"):
                lines.append(f"- Website: {lnk['url']}")

    # Email at top level
    top_email = p.get("email")
    if top_email:
        lines.append(f"- Email: {top_email}")

    # Bills
    lines.append("\n**Recent Sponsored Bills:**")
    lines += fetch_state_bills(person_id=p.get("id", ""))

    lines.append("\n---\n")
    return lines


# ======= CONGRESS.GOV: FEDERAL REPS + CONTACT + BILLS =======

@st.cache_data(ttl=600, show_spinner=False)
def fetch_federal_members(state: str, district: int):
    """Get current federal members for a state. Returns (list_of_dicts, error)."""
    if not CONGRESS_API_KEY:
        return [], "CONGRESS_API_KEY not configured"

    r, err = safe_get(
        f"{BASE_CONG}/member/congress/{CURRENT_CONGRESS}/{state}",
        params={"format": "json", "api_key": CONGRESS_API_KEY, "currentMember": "true", "limit": 20},
        timeout=CONGRESS_TIMEOUT,
        label="Congress.gov member list",
    )
    if err:
        return [], err

    raw_members = r.json().get("members", [])
    results = []
    for m in raw_members:
        terms = m.get("terms", {}).get("item", [])
        chamber = terms[-1].get("chamber", "") if terms else ""
        role = "Senator" if chamber == "Senate" else "Representative"
        m_district = m.get("district")

        # Filter: keep senators; keep only the matching district rep
        if role == "Senator":
            pass  # always keep
        elif role == "Representative":
            if m_district is not None:
                try:
                    if int(m_district) != district:
                        continue
                except (ValueError, TypeError):
                    continue
            elif district != 0:
                continue  # skip if we can't match district

        results.append({
            "name": m.get("name", "Unknown"),
            "party": m.get("partyName", "Unknown"),
            "role": role,
            "state": m.get("state", state),
            "district": m_district,
            "bioguideId": m.get("bioguideId", ""),
            "depiction": m.get("depiction", {}),
            "url": m.get("url", ""),
        })

    return results, None


@st.cache_data(ttl=600, show_spinner=False)
def fetch_member_detail(bioguide_id: str):
    """Get full member detail including contact info. Returns (dict, error)."""
    if not CONGRESS_API_KEY or not bioguide_id:
        return {}, "No API key or bioguide ID"
    r, err = safe_get(
        f"{BASE_CONG}/member/{bioguide_id}",
        params={"format": "json", "api_key": CONGRESS_API_KEY},
        timeout=CONGRESS_TIMEOUT,
        label="Congress.gov member detail",
    )
    if err:
        return {}, err
    return r.json().get("member", {}), None


@st.cache_data(ttl=600, show_spinner=False)
def fetch_sponsored_bills(bioguide_id: str) -> list[str]:
    if not CONGRESS_API_KEY or not bioguide_id:
        return ["- Congress.gov key not configured"]
    r, err = safe_get(
        f"{BASE_CONG}/member/{bioguide_id}/sponsored-legislation",
        params={"format": "json", "api_key": CONGRESS_API_KEY, "limit": BILLS_PER_PAGE},
        timeout=CONGRESS_TIMEOUT,
        label="Congress.gov sponsored-legislation",
    )
    if err:
        return [f"- {err}"]
    bills = r.json().get("sponsoredLegislation", [])
    return [
        f"- **{b.get('type', '')}{b.get('number', '')}** ({b.get('congress', '')}) — "
        f"{b.get('latestTitle', b.get('title', 'No title'))}"
        for b in bills
    ] or ["- No sponsored legislation found"]


def render_federal_rep(member_summary: dict) -> list[str]:
    """Render one federal rep as markdown: contact from detail endpoint + bills."""
    lines = []
    name = member_summary["name"]
    party = member_summary["party"]
    role = member_summary["role"]
    bioguide = member_summary["bioguideId"]

    lines.append(f"### {name} ({party}) — {role}")

    # Fetch full detail for contact info
    detail, detail_err = fetch_member_detail(bioguide)

    if detail:
        # Contact info
        addr_info = detail.get("addressInformation", {})
        if addr_info:
            lines.append("**Contact:**")
            office_addr = addr_info.get("officeAddress", "")
            city = addr_info.get("city", "")
            district_val = addr_info.get("district", "")
            zipcode = addr_info.get("zipCode", "")
            phone = addr_info.get("phoneNumber", "")

            full_addr = ", ".join(p for p in [office_addr, city, district_val, zipcode] if p)
            if full_addr:
                lines.append(f"- Office: {full_addr}")
            if phone:
                lines.append(f"- Phone: {phone}")

        # Website
        website = detail.get("officialWebsiteUrl", "")
        if website:
            lines.append(f"- Website: {website}")

        # Portrait
        depiction = detail.get("depiction", {})
        img_url = depiction.get("imageUrl", "")
        if img_url:
            lines.append(f"- Portrait: {img_url}")

    elif detail_err:
        lines.append(f"*Contact info unavailable: {detail_err}*")

    # Sponsored bills
    lines.append("\n**Recent Sponsored Legislation:**")
    lines += fetch_sponsored_bills(bioguide)

    lines.append("\n---\n")
    return lines


# ======= UI =======

st.set_page_config(page_title="Who Represents Me", layout="wide")
st.title("\U0001f3db\ufe0f Who Represents Me")

col1, col2, col3 = st.columns(3)
with col1:
    loc = st.text_input("Location (City, State)", placeholder="e.g., Detroit, MI")
with col2:
    lat_in = st.number_input("Latitude", value=None, placeholder="e.g., 42.3314", format="%.6f")
with col3:
    lng_in = st.number_input("Longitude", value=None, placeholder="e.g., -83.0458", format="%.6f")

if "running" not in st.session_state:
    st.session_state.running = False
if "last_lookup_time" not in st.session_state:
    st.session_state.last_lookup_time = 0.0

output_area = st.empty()
output_area.markdown("\U0001f539 Enter a location and click the button.")

if st.button("Find My Representatives", type="primary", disabled=st.session_state.running):

    elapsed = time.time() - st.session_state.last_lookup_time
    if elapsed < COOLDOWN_SECONDS:
        output_area.warning(f"Please wait {int(COOLDOWN_SECONDS - elapsed) + 1}s before searching again.")
        st.stop()

    st.session_state.last_lookup_time = time.time()
    st.session_state.running = True
    lat, lng = lat_in, lng_in
    out: list[str] = []
    total_reps = 0

    with st.status("\u23f3 Starting lookup\u2026") as status:
        try:
            # ── 1. GEOCODE ──
            if loc:
                status.update(label=f"Geocoding '{loc}'\u2026")
                try:
                    lat, lng, geo_err = geocode(loc)
                except Exception as e:
                    lat, lng, geo_err = None, None, str(e)
                if geo_err:
                    output_area.error(format_error("Geocoding failed", geo_err))
                    st.session_state.running = False
                    st.stop()

            if lat is None or lng is None:
                output_area.error("Please provide a City/State or coordinates.")
                st.session_state.running = False
                st.stop()

            loc_str = f"({lat:.4f}, {lng:.4f})"
            out.append(f"**Location:** {loc_str}\n")

            # ── 2. STATE REPS (OpenStates) ──
            status.update(label="Fetching state representatives\u2026")
            output_area.markdown("\n".join(out) + "\n\n*Fetching state reps\u2026*")

            state_people, state_err = fetch_state_reps(lat, lng)

            if state_err:
                out.append(f"## State Representatives\n\n**Error:** {state_err}\n")
            elif not state_people:
                out.append("## State Representatives\n\n*None found for this location.*\n")
            else:
                out.append(f"## State Representatives ({len(state_people)} found)\n")
                for p in state_people:
                    total_reps += 1
                    status.update(label=f"State rep {total_reps}: {p.get('name', '?')}\u2026")
                    out += render_state_rep(p)
                    output_area.markdown("\n".join(out))
                    time.sleep(INTER_REP_PAUSE)

            # ── 3. FEDERAL REPS (Census + Congress.gov) ──
            if CONGRESS_API_KEY:
                status.update(label="Resolving congressional district\u2026")
                output_area.markdown("\n".join(out) + "\n\n*Resolving congressional district\u2026*")

                try:
                    state_abbr, district_num, cd_err = resolve_district(lat, lng)
                except Exception as e:
                    state_abbr, district_num, cd_err = None, None, str(e)

                if cd_err:
                    out.append(f"## Federal Representatives\n\n**Error:** {cd_err}\n")
                else:
                    district_label = f"{state_abbr} At-Large" if district_num == 0 else f"{state_abbr}-{district_num}"
                    out.append(f"## Federal Representatives ({district_label})\n")

                    status.update(label=f"Fetching federal reps for {district_label}\u2026")
                    output_area.markdown("\n".join(out) + "\n\n*Fetching federal reps\u2026*")

                    fed_members, fed_err = fetch_federal_members(state_abbr, district_num)

                    if fed_err:
                        out.append(f"**Error:** {fed_err}\n")
                    elif not fed_members:
                        out.append("*No federal representatives found.*\n")
                    else:
                        for m in fed_members:
                            total_reps += 1
                            status.update(label=f"Federal rep {total_reps}: {m['name']}\u2026")
                            out += render_federal_rep(m)
                            output_area.markdown("\n".join(out))
                            time.sleep(INTER_REP_PAUSE)
            else:
                out.append("## Federal Representatives\n\n*Add `CONGRESS_API_KEY` to secrets to enable.*\n")

            # ── 4. DONE ──
            if total_reps == 0:
                out.append("\n**No representatives found for this location.**\n")
                output_area.markdown("\n".join(out))
                status.update(label="No results.", state="error")
            else:
                output_area.markdown("\n".join(out))
                status.update(label=f"Done — {total_reps} representatives found.", state="complete")

        except Exception as e:
            out.append(f"\n\n**Unexpected error:** {e}")
            output_area.markdown("\n".join(out))
            status.update(label="Error occurred.", state="error")

        finally:
            st.session_state.running = False