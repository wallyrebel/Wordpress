"""Authenticated client for the durable MS News Workflow companion."""
import html
import re
import hashlib
import json
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
    def __init__(self, wp_url, username, app_password, media_receipt_dir='review/media'):
        self.wp_url = wp_url.rstrip("/")
        self.session = requests.Session()
        self.session.auth = HTTPBasicAuth(username, app_password)
        self.coverage = None
        self.supported_statuses = None
        self.media_receipt_dir = Path(media_receipt_dir)
        self.supported_media_statuses = None

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

    def media_statuses(self):
        if self.supported_media_statuses is None:
            schema = self.request('OPTIONS', 'wp/v2/media')
            choices = []
            for endpoint in schema.get('endpoints', []):
                if 'GET' in endpoint.get('methods', []):
                    choices = endpoint.get('args', {}).get('status', {}).get('items', {}).get('enum', [])
                    if choices:
                        break
            statuses = sorted(set(choices)-{'any'})
            if 'inherit' not in statuses:
                raise ValueError('Cannot establish supported WordPress media statuses')
            self.supported_media_statuses = statuses
        return self.supported_media_statuses

    def media_identity(self, slug):
        found = {}
        for status in self.media_statuses():
            for page in range(1, 6):
                batch = self.request('GET', 'wp/v2/media', params={'context': 'edit',
                    'status': status, 'slug': slug, 'per_page': 100, 'page': page})
                if not isinstance(batch, list):
                    raise ValueError('Malformed media identity lookup')
                for media in batch:
                    if media.get('slug') != slug or type(media.get('id')) is not int:
                        raise ValueError('Media identity lookup did not match exact slug')
                    found[media['id']] = media
                if len(batch) < 100:
                    break
            else:
                raise ValueError('Media identity lookup exceeds bounded limit')
        if len(found) > 1:
            raise ValueError('Multiple assets share the deterministic media identity; review required')
        return next(iter(found.values()), None)

    def save_media_receipt(self, record):
        self.last_media_receipt = dict(record)
        self.media_receipt_dir.mkdir(parents=True, exist_ok=True)
        path = self.media_receipt_dir / (record['sha256']+'.json')
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(record, indent=2), encoding='utf-8')
        temp.replace(path)

    def preview_media(self, image):
        """Read-only identity/pixel check; never upload or change metadata."""
        from safe_http import fetch_bytes
        digest = hashlib.sha256(Path(image.path).read_bytes()).hexdigest()
        slug = 'msn-image-'+digest
        media = self.media_identity(slug)
        if media:
            if media.get('status') not in ('inherit', 'publish'):
                raise ValueError('Preview media identity exists in a nonpublic status')
            served, _, _ = fetch_bytes(media['source_url'], max_bytes=10_000_000)
            if hashlib.sha256(served).hexdigest() != digest:
                raise ValueError('Preview media identity bytes differ')
        return {'sha256': digest, 'slug': slug, 'existing_media_id': media['id'] if media else None,
                'all_supported_statuses_checked': self.media_statuses(), 'writes': 0}

    def upload_media(self, image, headline):
        """One deterministic identity; reconcile before creating, never retry uncertain POST."""
        from safe_http import fetch_bytes
        data = Path(image.path).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        slug = 'msn-image-'+digest
        previous_path = self.media_receipt_dir/(digest+'.json')
        previous = json.loads(previous_path.read_text()) if previous_path.exists() else {}
        if previous and (previous.get('sha256') != digest or previous.get('slug') != slug):
            raise ValueError('Saved media intent identity mismatch')
        record = {'sha256': digest, 'slug': slug, 'state': 'lookup', 'media_id': None}
        self.save_media_receipt(record)
        media = self.media_identity(slug)
        if media:
            if media.get('status') not in ('inherit', 'publish'):
                record.update(state='existing_nonpublic', media_id=media['id'], observed_status=media.get('status'))
                self.save_media_receipt(record)
                raise ValueError('Deterministic media is nonpublic; do not restore, expose or recreate automatically')
            served, _, _ = fetch_bytes(media['source_url'], max_bytes=10_000_000)
            if hashlib.sha256(served).hexdigest() != digest:
                record.update(state='existing_bytes_mismatch', media_id=media['id'])
                self.save_media_receipt(record)
                raise ValueError('Existing deterministic media bytes differ; no overwrite or upload')
            record.update(state='existing_verified', media_id=media['id'])
            self.save_media_receipt(record)
            resume_metadata = (previous.get('state') in ('upload_intent', 'upload_uncertain', 'uploaded',
                               'metadata_intent', 'metadata_uncertain')
                               and previous.get('media_id') in (None, media['id']))
            if not resume_metadata:
                # Matching bytes do not authorize overwriting an editor's existing metadata.
                record.update(state='verified', metadata_preserved=True)
                self.save_media_receipt(record)
                return media['id']
        else:
            record['state'] = 'upload_intent'
            self.save_media_receipt(record)
            try:
                with Path(image.path).open('rb') as stream:
                    media = self.request('POST', 'wp/v2/media',
                        data={'slug': slug, 'title': slug},
                        files={'file': (slug+'.jpg', stream, 'image/jpeg')})
                if type(media.get('id')) is int:
                    record['media_id'] = media['id']
                if type(media.get('id')) is not int or media.get('slug') != slug:
                    raise ValueError('Uploaded media identity was not confirmed')
                record.update(state='uploaded', media_id=media['id'])
                self.save_media_receipt(record)
            except Exception:
                record['state'] = 'upload_uncertain'
                self.save_media_receipt(record)
                raise
        try:
            record['state'] = 'metadata_intent'
            self.save_media_receipt(record)
            result = self.request('POST', 'wp/v2/media/'+str(media['id']),
                json={'slug': slug, 'title': headline, 'alt_text': headline,
                      'caption': html.escape(image.credit),
                      'description': 'Source image: '+html.escape(image.source_url)})
            if result.get('id') != media['id'] or result.get('slug') != slug:
                raise ValueError('Media metadata identity was not confirmed')
            record['state'] = 'verified'
            self.save_media_receipt(record)
            return media['id']
        except Exception:
            record['state'] = 'metadata_uncertain'
            self.save_media_receipt(record)
            raise
