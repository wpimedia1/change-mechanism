import inspect
import json

import requests

import streamlit_app as app


class DummyResponse:
    def __init__(self, *, status_code=200, json_data=None, ok=True, http_error=False):
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}
        self.ok = ok
        self._http_error = http_error

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self._http_error:
            raise requests.HTTPError(f"HTTP {self.status_code}")



def parse_error_markdown(markdown_block: str) -> dict:
    payload = markdown_block.replace("```json\n", "", 1).rsplit("\n```", 1)[0]
    return json.loads(payload)



def clear_cached_functions():
    for fn in [app.geocode, app.fetch_people, app.fetch_openstates_bills, app.fetch_congress_bills]:
        clear = getattr(fn, "clear", None)
        if callable(clear):
            clear()



def test_configure_api_keys_sets_openstates_header_only():
    app.configure_api_keys("openstates-key", "congress-key")
    assert app.HEADERS_OS == {"X-Api-Key": "openstates-key"}
    assert app.CONGRESS_API_KEY == "congress-key"



def test_fetch_people_uses_header_auth_for_openstates(monkeypatch):
    clear_cached_functions()
    app.configure_api_keys("openstates-key", "congress-key")
    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return DummyResponse(json_data={"results": []})

    monkeypatch.setattr(app.requests, "get", fake_get)
    app.fetch_people(42.0, -83.0)

    assert captured["url"].endswith("/people.geo")
    assert captured["kwargs"]["headers"]["X-Api-Key"] == "openstates-key"
    assert "api_key" not in captured["kwargs"]["params"]



def test_fetch_congress_bills_keeps_query_api_key(monkeypatch):
    clear_cached_functions()
    app.configure_api_keys("openstates-key", "congress-key")
    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return DummyResponse(json_data={"sponsoredLegislation": []})

    monkeypatch.setattr(app.requests, "get", fake_get)
    app.fetch_congress_bills("A000360")

    assert captured["url"].endswith("/member/A000360/sponsored-legislation")
    assert captured["kwargs"]["params"]["api_key"] == "congress-key"
    assert "headers" not in captured["kwargs"]



def test_resolve_bioguide_id_requires_authoritative_identifier():
    person_without_id = {
        "name": "Jane Example",
        "identifiers": [{"scheme": "other", "identifier": "123"}],
    }
    assert app.resolve_bioguide_id(person_without_id) is None

    person_with_id = {
        "name": "Jane Example",
        "identifiers": [{"scheme": "bioguide", "identifier": "J000000"}],
    }
    assert app.resolve_bioguide_id(person_with_id) == "J000000"



def test_geocode_timeout_error_is_sanitized(monkeypatch):
    clear_cached_functions()

    def fake_get(*args, **kwargs):
        raise requests.Timeout("secret-token-should-not-leak")

    monkeypatch.setattr(app.requests, "get", fake_get)
    _, _, err = app.geocode("Detroit, MI")
    data = parse_error_markdown(err)
    msg = data["error"]["message"]

    assert "secret-token" not in msg
    assert "timed out" in msg.lower()



def test_main_contains_duplicate_submit_guard():
    main_source = inspect.getsource(app.main)
    form_source = inspect.getsource(app.render_lookup_form)
    assert "if submitted and st.session_state.running" in main_source
    assert "disabled=st.session_state.running" in form_source
