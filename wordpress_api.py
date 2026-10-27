"""Authenticated client for the durable MS News Workflow companion."""
import html
import re
from pathlib import Path
from bs4 import BeautifulSoup
import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime, timedelta, timezone
from coverage import CoverageIndex, linked_sources, source_identity, text_field

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
        self.coverage = None
        self.supported_statuses = None

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

    def discover_statuses(self):
        if self.supported_statuses is None:
            schema = self.request('OPTIONS', 'wp/v2/posts')
            choices = []
            for endpoint in schema.get('endpoints', []):
                if 'GET' in endpoint.get('methods', []):
                    choices = endpoint.get('args', {}).get('status', {}).get('items', {}).get('enum', [])
                    if choices:
                        break
            statuses = sorted(set(choices) - {'any'})
            if not {'publish', 'future', 'draft', 'pending', 'private'} <= set(statuses):
                raise ValueError('Cannot establish all supported WordPress post statuses')
            self.supported_statuses = statuses
        return self.supported_statuses

    def posts(self, **query):
        # Query each supported status explicitly; status=any can omit states.
        for status in self.discover_statuses():
            for page in range(1, 51):
                batch = self.request('GET', 'wp/v2/posts', params={
                    'context': 'edit', 'status': status, 'per_page': 100, 'page': page,
                    '_fields': 'id,status,date_gmt,modified_gmt,title,content,excerpt,link,slug', **query})
                if not isinstance(batch, list) or any(not isinstance(p, dict) for p in batch):
                    raise ValueError('Malformed WordPress reconciliation response')
                yield from batch
                if len(batch) < 100:
                    break
            else:
                raise ValueError('WordPress reconciliation exceeds bounded page limit')

    def reconcile(self, target=5, anchor_ids=()):
        now = datetime.now(timezone.utc)
        after = (now-timedelta(days=35)).strftime('%Y-%m-%dT%H:%M:%S')
        posts = list(self.posts(modified_after=after))
        for post_id in anchor_ids:
            post = self.request('GET', 'wp/v2/posts/'+str(post_id), params={'context': 'edit'})
            if post.get('id') != post_id:
                raise ValueError('Fixed reconciliation anchor is unavailable')
            posts.append(post)
        self.coverage = CoverageIndex(posts, self.discover_statuses(), now)
        return self.coverage.public_receipt(target, anchor_ids)

    def daily_progress(self, target=5):
        if self.coverage is None:
            raise ValueError('Reconcile WordPress before daily accounting')
        return self.coverage.daily(target)

    def find_duplicate_headline(self, headline):
        """Check the authoritative site, including after runner cache loss.

        Exact normalized titles only: similar stories can be distinct developments.
        Search errors propagate so publication fails closed.
        """
        def normalized_title(value):
            return " ".join(re.findall(r"\w+", html.unescape(value).casefold()))
        normalized = normalized_title(headline)
        for post in self.posts(search=" ".join(normalized.split()[:6])):
            if normalized_title(text_field(post, 'title')) == normalized:
                return post['id']
        return None

    def find_source_publication(self, source_url):
        """All links count, including other producers' briefings and unpublished posts."""
        if self.coverage and self.coverage.source_match(source_url):
            return self.coverage.source_match(source_url)
        identity = source_identity(source_url)
        for post in self.posts(search=source_url):
            if identity in linked_sources(post):
                return post['id']
        return None

    def event_candidates(self, title):
        if self.coverage is None:
            raise ValueError('Reconcile WordPress before event matching')
        # A current source can repeat a long-planned event first covered months ago.
        stop = set('the and for from with that this will have into its news mississippi county says said today update new public'.split())
        terms = [w for w in re.findall(r'[a-z0-9]+', html.unescape(title).lower())
                 if len(w) > 2 and w not in stop]
        historic = list(self.posts(search=' '.join(terms[:2]))) if terms else []
        merged = CoverageIndex(list(self.coverage.posts.values()) + historic,
                               self.discover_statuses(), self.coverage.now)
        return merged.event_candidates(title)

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
