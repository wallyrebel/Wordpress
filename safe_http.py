"""Bounded public HTTP reads and stable source identifiers."""
import ipaddress
import socket
from urllib.parse import urlsplit, urljoin, urlunsplit, parse_qsl, urlencode
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

def canonical_url(url):
    p = urlsplit(url.strip())
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        raise ValueError("Expected an absolute public HTTP(S) URL")
    if p.port not in (None, 80, 443):
        raise ValueError("Nonstandard source port")
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid")]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or "/", urlencode(sorted(query)), ""))

def validate_public(url):
    canonical_url(url)
    addresses = socket.getaddrinfo(urlsplit(url).hostname, None, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError("Source resolves to a non-public address")

def fetch_bytes(url, max_bytes=4_000_000):
    session = requests.Session()
    session.trust_env = False
    session.headers["User-Agent"] = "MSNewsGroupFeedReader/2.0"
    retry = Retry(total=2, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"], respect_retry_after_header=False)
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    try:
        for _ in range(5):
            validate_public(url)
            with session.get(url, timeout=(10, 25), allow_redirects=False, stream=True) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers["Location"])
                    continue
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("Response exceeds size limit")
                    chunks.append(chunk)
                return b"".join(chunks), response.headers.get("Content-Type", ""), url
        raise ValueError("Too many redirects")
    finally:
        session.close()
