import json
import os
import time

import requests
import streamlit as st

# ======= CONFIG =======
BASE_OS = "https://v3.openstates.org"
BASE_CONG = "https://api.congress.gov/v3"
BILLS_PER_PAGE = 5
GEOCODE_TIMEOUT_SECONDS = 12
PEOPLE_TIMEOUT_SECONDS = 20
STATE_BILLS_TIMEOUT_SECONDS = 18
CONGRESS_BILLS_TIMEOUT_SECONDS = 16

OPEN_API_KEY = ""
CONGRESS_API_KEY = ""
HEADERS_OS = {}


def configure_api_keys(open_api_key: str, congress_api_key: str) -> None:
    """Configures API keys and request headers."""
    global OPEN_API_KEY, CONGRESS_API_KEY, HEADERS_OS
    OPEN_API_KEY = (open_api_key or "").strip()
    CONGRESS_API_KEY = (congress_api_key or "").strip()
    HEADERS_OS = {"X-Api-Key": OPEN_API_KEY} if OPEN_API_KEY else {}


def read_secret(key: str, default: str = "") -> str:
    """Reads a secret with environment fallback for non-Streamlit test runs."""
    try:
        return st.secrets.get(key, default)
    except Exception:
        return os.getenv(key, default)


configure_api_keys(
    os.getenv("OPEN_API_KEY", ""),
    os.getenv("CONGRESS_API_KEY", ""),
)


# ======= UTIL =======
def jerr(name, message, meta=None):
    """Formats an error as a JSON markdown block."""
    error = {
        "error": {
            "name": name,
            "message": message,
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
        response = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": location, "format": "json", "limit": 1},
            headers={"User-Agent": "WhoRepMe-Streamlit/1.0"},
            timeout=GEOCODE_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
        if not data:
            return None, None, jerr("GeoError", f"No results for '{location}'.")
        return float(data[0]["lat"]), float(data[0]["lon"]), None
    except requests.Timeout:
        return None, None, jerr("GeoError", "Geocoding timed out. Try again.")
    except requests.RequestException:
        return None, None, jerr("GeoError", "Geocoding request failed.")
    except (KeyError, TypeError, ValueError):
        return None, None, jerr("GeoError", "Geocoding response could not be parsed.")


def is_federal(juris: dict) -> bool:
    """Checks if a jurisdiction dict represents the US Federal government."""
    if not juris:
        return False
    name_value = (juris.get("name") or "").lower()
    id_value = (juris.get("id") or "").lower()
    class_value = (juris.get("classification") or "").lower()
    return (
        name_value == "united states"
        or id_value == "ocd-jurisdiction/country:us/government"
        or class_value == "country"
    )


# ======= API CALLS (Cached) =======
@st.cache_data(ttl=600)
def fetch_people(lat, lng):
    """Fetches people from OpenStates API by coordinates."""
    params = {"lat": lat, "lng": lng, "include": ["offices"]}
    response = requests.get(
        f"{BASE_OS}/people.geo",
        headers=HEADERS_OS,
        params=params,
        timeout=PEOPLE_TIMEOUT_SECONDS,
    )
    if response.status_code == 429:
        time.sleep(2.0)
        response = requests.get(
            f"{BASE_OS}/people.geo",
            headers=HEADERS_OS,
            params=params,
            timeout=PEOPLE_TIMEOUT_SECONDS,
        )
    response.raise_for_status()
    return response.json().get("results", [])


@st.cache_data(ttl=600)
def fetch_openstates_bills(person_id, juris_id):
    """Fetches recent bills for a state-level person."""
    params = {
        "sponsor": person_id,
        "jurisdiction": juris_id,
        "sort": "updated_desc",
        "per_page": BILLS_PER_PAGE,
    }
    response = requests.get(
        f"{BASE_OS}/bills",
        headers=HEADERS_OS,
        params=params,
        timeout=STATE_BILLS_TIMEOUT_SECONDS,
    )
    if response.status_code == 429:
        return ["- Rate limited (429) - try again shortly"]
    if not response.ok:
        return [f"- OpenStates bills error {response.status_code}"]

    bills = response.json().get("results", [])
    return [f"- {b.get('identifier', '?')} - {b.get('title', 'No title')}" for b in bills] or ["- None"]


def resolve_bioguide_id(person_dict):
    """Finds Bioguide ID only from authoritative OpenStates identifiers."""
    for ident in person_dict.get("identifiers", []):
        if (ident.get("scheme") or "").lower() == "bioguide" and ident.get("identifier"):
            return ident.get("identifier")
    return None


@st.cache_data(ttl=600)
def fetch_congress_bills(bioguide_id):
    """Fetches recent bills sponsored by a federal member from Congress.gov."""
    if not CONGRESS_API_KEY:
        return ["- congress.gov key not configured"]
    if not bioguide_id:
        return ["- No bioguide ID available to fetch bills"]

    # Congress.gov compatibility note:
    # Keep api_key in query params until endpoint-level verification confirms header-only parity.
    # If this app is deployed behind a reverse proxy, scrub query strings from access logs.
    params = {"format": "json", "limit": BILLS_PER_PAGE, "api_key": CONGRESS_API_KEY}
    response = requests.get(
        f"{BASE_CONG}/member/{bioguide_id}/sponsored-legislation",
        params=params,
        timeout=CONGRESS_BILLS_TIMEOUT_SECONDS,
    )

    if response.status_code == 429:
        return ["- Rate limited (429) - try again shortly"]
    if not response.ok:
        return [f"- Congress.gov error {response.status_code}"]

    data = response.json() or {}
    bills = data.get("sponsoredLegislation", [])
    results = []
    for bill in bills[:BILLS_PER_PAGE]:
        congress = bill.get("congress", "?")
        bill_type = bill.get("type", "")
        bill_number = bill.get("number", "")
        title = bill.get("title") or "No title available"
        results.append(f"- [{congress} {bill_type} {bill_number}] {title}")
    return results or ["- None"]


def initialize_session_state() -> None:
    """Initializes session variables used by app flow."""
    if "running" not in st.session_state:
        st.session_state.running = False
    if "last_output" not in st.session_state:
        st.session_state.last_output = "Ready"


def render_lookup_form():
    """Renders responsive inputs and returns submitted values."""
    with st.form("lookup_form", clear_on_submit=False):
        loc = st.text_input(
            "Location (City, State)",
            placeholder="e.g., Detroit, MI",
            help="Use either location text or latitude/longitude coordinates.",
        )
        col1, col2 = st.columns(2)
        with col1:
            lat_in = st.number_input(
                "Latitude",
                value=None,
                placeholder="e.g., 42.3314",
                format="%.6f",
            )
        with col2:
            lng_in = st.number_input(
                "Longitude",
                value=None,
                placeholder="e.g., -83.0458",
                format="%.6f",
            )
        submitted = st.form_submit_button(
            "Find My Representatives",
            type="primary",
            disabled=st.session_state.running,
            use_container_width=True,
        )
    return submitted, loc, lat_in, lng_in


def main() -> None:
    configure_api_keys(
        read_secret("OPEN_API_KEY", ""),
        read_secret("CONGRESS_API_KEY", ""),
    )

    st.set_page_config(page_title="Who Reps Me | Civic Map", layout="wide")
    st.title("Who Represents Me")

    if not OPEN_API_KEY:
        st.error("Missing OPEN_API_KEY in Streamlit secrets. Please add it to ./.streamlit/secrets.toml")
        st.stop()

    initialize_session_state()
    output_area = st.empty()
    output_area.markdown(st.session_state.last_output)
    submitted, loc, lat_in, lng_in = render_lookup_form()

    if submitted and st.session_state.running:
        output_area.warning(jerr("BusyError", "A lookup is already in progress."))
        return

    if not submitted:
        return

    st.session_state.running = True
    lat, lng = lat_in, lng_in
    out = []

    with st.status("Starting lookup...") as status:
        try:
            if loc:
                status.update(label=f"Geocoding '{loc}'...")
                lat, lng, geo_err = geocode(loc)
                if geo_err:
                    output_area.error(geo_err)
                    status.error("Geocoding failed.")
                    st.session_state.last_output = geo_err
                    st.stop()

            if lat is None or lng is None:
                err = jerr("InputError", "Provide coordinates or a City, State string.")
                output_area.error(err)
                status.warning("No location provided.")
                st.session_state.last_output = err
                st.stop()

            loc_str = f"({lat:.6f}, {lng:.6f})"
            status.update(label=f"Resolving reps for {loc_str}...")
            output_area.markdown(f"Resolving representatives for **{loc_str}**...")

            try:
                people = fetch_people(lat, lng)
            except requests.HTTPError:
                err = jerr("HTTPError", "Representative lookup failed.")
                output_area.error(err)
                status.error("Failed to fetch representatives.")
                st.session_state.last_output = err
                st.stop()

            if not people:
                err = jerr("EmptyResults", "No representatives found.", {"lat": lat, "lng": lng})
                output_area.warning(err)
                status.warning("No representatives found.")
                st.session_state.last_output = err
                st.stop()

            status.update(label=f"Found {len(people)} reps. Fetching bills...")
            out = [f"**Location:** {loc_str}", ""]

            for idx, person in enumerate(people, start=1):
                name = person.get("name", "N/A")
                party = person.get("party", "Unknown")
                role = (person.get("current_role") or {}).get("title", "Unknown")
                juris = person.get("jurisdiction") or {}

                out.append(f"### {name} ({party}) - {role}")
                out.append(f"Jurisdiction: {juris.get('name', '')}")

                offices = person.get("offices", []) or person.get("contact_details", [])
                if offices:
                    out.append("**Contact:**")
                    for office in offices:
                        if isinstance(office, dict):
                            addr = office.get("address") or office.get("value") or ""
                            voice = office.get("voice") or office.get("voice_number") or ""
                            line = ", ".join(part for part in [addr, voice] if part)
                            if line:
                                out.append(f"- {line}")

                if is_federal(juris):
                    out.append("\n**Federal (Congress.gov)**")
                    bioguide_id = resolve_bioguide_id(person)
                    if bioguide_id:
                        out += fetch_congress_bills(bioguide_id)
                    else:
                        out.append("- *Missing authoritative Bioguide ID for sponsor search*")
                else:
                    out.append("\n**State (OpenStates)**")
                    out += fetch_openstates_bills(person.get("id", ""), juris.get("id", ""))

                out.append("\n---\n")
                status_msg = f"Fetching bills for {name}... ({idx}/{len(people)})"
                status.update(label=status_msg)
                rendered = "\n".join(out) + f"\n\n*({status_msg})*"
                output_area.markdown(rendered)
                st.session_state.last_output = rendered
                time.sleep(0.12)

            final_output = "\n".join(out)
            status.success("Lookup complete.")
            output_area.markdown(final_output)
            st.session_state.last_output = final_output

        except Exception:
            err = jerr("UnhandledException", "Unexpected application error. Retry the request.")
            output_area.error(err)
            status.error("An unexpected error occurred.")
            st.session_state.last_output = err

        finally:
            st.session_state.running = False


if __name__ == "__main__":
    main()
