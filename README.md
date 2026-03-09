# 🏛️ Who Represents Me

A Streamlit app that finds your elected representatives by location using the [OpenStates API](https://docs.openstates.org/api-v3/) and [Congress.gov API](https://api.congress.gov/), with recent bill activity for each legislator.

Enter a city/state or coordinates → get your state and federal reps, contact info, and recent bills.

## Setup

### 1. Clone and install

```bash
git clone https://github.com/wpimedia1/change-mechanism.git
cd change-mechanism
pip install -r requirements.txt
```

### 2. Add API keys

Create `.streamlit/secrets.toml` in the project root:

```toml
OPEN_API_KEY = "your-openstates-api-key"
CONGRESS_API_KEY = "your-congress-gov-api-key"  # optional
```

- **OpenStates key** (required): Register at [openstates.org/accounts/signup](https://openstates.org/accounts/signup/)
- **Congress.gov key** (optional): Register at [api.congress.gov/sign-up](https://api.congress.gov/sign-up/)

> ⚠️ Do not commit `secrets.toml` — it is in `.gitignore`.

### 3. Run

```bash
streamlit run app.py
```

## How it works

1. Geocodes your location via [Nominatim](https://nominatim.openstreetmap.org/) (OpenStreetMap)
2. Looks up representatives at those coordinates via OpenStates `people.geo`
3. Fetches recent bills — OpenStates for state legislators, Congress.gov for federal
4. Displays results progressively with contact info and bill summaries

## Project structure

```
├── app.py                     # Main application
├── requirements.txt           # streamlit, requests
├── .streamlit/
│   └── secrets.toml           # API keys (not committed)
└── .gitignore
```

## Configuration

All tunable values are constants at the top of `app.py`:

| Constant | Default | Purpose |
|----------|---------|---------|
| `COOLDOWN_SECONDS` | `5` | Min seconds between lookups per session |
| `BILLS_PER_PAGE` | `5` | Recent bills fetched per legislator |
| `API_TIMEOUT` | `20` | Timeout for OpenStates people lookup |
| `GEOCODE_TIMEOUT` | `12` | Timeout for Nominatim geocoding |

## Caching

- Geocoding results cached 1 hour (`st.cache_data`, `ttl=3600`)
- Representative and bill data cached 10 minutes (`ttl=600`)
- Cache is global across sessions — identical lookups share results

## Deployment

Works on [Streamlit Community Cloud](https://streamlit.io/cloud) — paste your `secrets.toml` contents into the app's **Advanced Settings > Secrets** panel during deployment.

For other platforms, set `OPEN_API_KEY` and `CONGRESS_API_KEY` as environment variables or provide a `secrets.toml` per [Streamlit docs](https://docs.streamlit.io/deploy/concepts/secrets).

## License

MIT
