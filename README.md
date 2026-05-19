# Who Represents Me

A Streamlit app that finds elected representatives by location using the OpenStates API and Congress.gov API, including recent bill activity.

## Setup

### 1) Clone and install

```bash
git clone https://github.com/wpimedia1/change-mechanism.git
cd change-mechanism
pip install -r requirements.txt
```

### 2) Add API keys

Create `.streamlit/secrets.toml` in the project root:

```toml
OPEN_API_KEY = "your-openstates-api-key"
CONGRESS_API_KEY = "your-congress-gov-api-key"  # optional
```

- OpenStates key (required): https://openstates.org/accounts/signup/
- Congress.gov key (optional): https://api.congress.gov/sign-up/

Do not commit `secrets.toml`; it is ignored by `.gitignore`.

### 3) Run

```bash
streamlit run streamlit_app.py
```

## Tests

Run the full test suite:

```bash
pytest -q
```

## Security Notes

- OpenStates key is sent in `X-Api-Key` header.
- Congress.gov key is intentionally sent as `api_key` query parameter for provider compatibility.
- If you deploy behind a reverse proxy, disable or redact query-string logging for `api_key`.
- See [SECURITY_CHECKLIST.md](SECURITY_CHECKLIST.md) for deployment guidance.

## Project Structure

```text
streamlit_app.py
tests/test_streamlit_app.py
requirements.txt
SECURITY_CHECKLIST.md
```

## License

MIT
