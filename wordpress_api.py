"""Authenticated client for the durable MS News Workflow companion."""
import html
import re
from pathlib import Path
import requests
from requests.auth import HTTPBasicAuth

def safe_http_details(exc):
    """Diagnostic codes only: never response text, URLs, headers or credentials."""
    if not isinstance(exc, requests.HTTPError) or exc.response is None:
        return {}
    details = {"http_status": exc.response.status_code}
    try:
        body = exc.response.json()
    except ValueError:
        return details
    code = body.get("code") if isinstance(body, dict) else None
    if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code):
        details["api_error_code"] = code
    return details

class WordPressAPI:
    def __init__(self, wp_url, username, app_password):
        self.wp_url = wp_url.rstrip("/")
        self.session = requests.Session()
        self.session.auth = HTTPBasicAuth(username, app_password)

    def request(self, method, endpoint, **kwargs):
        # No blind retry of writes. Companion source/version receipts make manual
        # reruns safe after a timeout, including when the local cache was lost.
        response = self.session.request(method, self.wp_url + "/wp-json/" + endpoint,
                                        timeout=(10, 60), allow_redirects=False, **kwargs)
        response.raise_for_status()
        if response.is_redirect:
            raise ValueError("WordPress redirected an authenticated request")
        return response.json()

    def test_connection(self):
        info = self.request("GET", "ms-news/v1/health")
        if info.get("version") != 1:
            raise ValueError("Install/activate the bundled MS News Workflow companion")
        return True

    def receipt(self, source_key):
        return self.request("GET", "ms-news/v1/receipt/" + source_key)

    def upsert(self, payload):
        return self.request("POST", "ms-news/v1/article", json=payload)

    def create_or_get_tag(self, tag):
        try:
            return self.request("POST", "wp/v2/tags", json={"name": tag})["id"]
        except requests.HTTPError as exc:
            # WordPress search can miss HTML-encoded names (Texas A&M), and a
            # concurrent publisher can create a tag after our search. The REST
            # conflict supplies the authoritative existing ID; do not retry POST.
            response = exc.response
            if response is None or response.status_code not in (400, 409):
                raise
            try:
                body = response.json()
            except ValueError:
                raise exc
            if not isinstance(body, dict) or body.get("code") != "term_exists":
                raise
            data = body.get("data")
            term_id = data.get("term_id") if isinstance(data, dict) else None
            if type(term_id) is not int or term_id <= 0:
                raise
            existing = self.request("GET", "wp/v2/tags/" + str(term_id))
            if existing.get("id") != term_id:
                raise ValueError("Existing WordPress tag could not be verified")
            return term_id

    def taxonomy(self, category, tags, category_map):
        category_id = category_map.get(category)
        if category_id:
            found = self.request("GET", "wp/v2/categories/" + str(category_id))
            if found.get("id") != category_id:
                raise ValueError("Configured category not found")
        else:
            alias = {"Crime & Courts":"Crime", "Politics":"Mississippi Politics", "Sports":"Mississippi Sports"}.get(category, category)
            matches = self.request("GET", "wp/v2/categories", params={"search": alias, "per_page": 100})
            category_id = next((t["id"] for t in matches
                if html.unescape(t["name"]).casefold() == alias.casefold()), None)
        tag_ids = []
        for tag in tags[:5]:
            matches = self.request("GET", "wp/v2/tags", params={"search": tag, "per_page": 100})
            exact = [t["id"] for t in matches
                if html.unescape(t["name"]).casefold() == tag.casefold()]
            if exact:
                tag_ids.extend(exact)
            else:
                # Only source-grounded entities supplied by the validated extractor.
                tag_ids.append(self.create_or_get_tag(tag))
        return ([category_id] if category_id else []), list(dict.fromkeys(tag_ids))

    def upload_media(self, image, headline):
        with Path(image.path).open("rb") as stream:
            media = self.request("POST", "wp/v2/media",
                files={"file": (Path(image.path).name, stream, "image/jpeg")})
        self.request("POST", "wp/v2/media/" + str(media["id"]),
            json={"alt_text": "", "caption": html.escape(image.credit),
                  "description": "Source image: " + html.escape(image.source_url)})
        return media["id"]
