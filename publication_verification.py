"""Verify the exact saved post and its independently served public representation."""
import hashlib
import html
import io
from pathlib import Path
from bs4 import BeautifulSoup
from PIL import Image
import requests
from safe_http import validate_public, fetch_bytes
from coverage import text_field


def plain(value):
    value = value.translate(str.maketrans({'’': "'", '‘': "'", '“': '"', '”': '"', '–': '-', '—': '-'}))
    return ' '.join(BeautifulSoup(html.unescape(value), 'html.parser').get_text(' ', strip=True).split())


def public_document(url):
    validate_public(url)
    with requests.Session() as session:
        session.trust_env = False
        with session.get(url, timeout=(10, 30), allow_redirects=False, stream=True) as response:
            response.raise_for_status()
            if response.is_redirect:
                raise ValueError('Public article unexpectedly redirected')
            data = bytearray()
            for chunk in response.iter_content(65536):
                data.extend(chunk)
                if len(data) > 4_000_000:
                    raise ValueError('Public article exceeds verification size limit')
            return bytes(data), dict(response.headers)


def pixels(data):
    with Image.open(io.BytesIO(data)) as image:
        image = image.convert('RGB')
        return image.size, hashlib.sha256(image.tobytes()).hexdigest()


def verify_article(wp, receipt, article, expected_content, media_id, image):
    post_id = receipt.get('post_id')
    if type(post_id) is not int or receipt.get('status') != 'publish':
        raise ValueError('Publication receipt did not identify a live post')
    post = wp.request('GET', 'wp/v2/posts/'+str(post_id), params={'context': 'edit'})
    if (post.get('id') != post_id or post.get('status') != 'publish'
            or plain(text_field(post, 'title')) != plain(article.headline)
            or plain(text_field(post, 'content')) != plain(expected_content)
            or plain(text_field(post, 'excerpt')) != plain(article.excerpt)
            or post.get('featured_media') != media_id):
        raise ValueError('Saved article differs from its reviewed payload')
    url = post.get('link')
    if not url or url != receipt.get('url'):
        raise ValueError('Receipt/public permalink mismatch')
    page, headers = public_document(url)
    soup = BeautifulSoup(page, 'html.parser')
    h1 = soup.select_one('article h1') or soup.find('h1')
    canonical = soup.select_one('link[rel="canonical"]')
    description = soup.select_one('meta[name="description"]')
    if h1 is None or plain(h1.get_text()) != plain(article.headline):
        raise ValueError('Public H1 differs from reviewed headline')
    if not soup.title or plain(article.headline) not in plain(soup.title.get_text()):
        raise ValueError('Public title does not describe this article')
    if canonical is None or canonical.get('href', '').rstrip('/') != url.rstrip('/'):
        raise ValueError('Public canonical is missing or incorrect')
    if description is None or plain(description.get('content', '')) != plain(article.excerpt):
        raise ValueError('Public description differs from reviewed excerpt')
    robots = ' '.join(n.get('content', '') for n in soup.select('meta[name="robots"],meta[name="googlebot"]'))
    robots += ' ' + ' '.join(v for k, v in headers.items() if k.lower() == 'x-robots-tag')
    if 'noindex' in robots.lower():
        raise ValueError('Public article is marked noindex')
    public_article = soup.find('article')
    if public_article is None:
        raise ValueError('Public article container missing')
    visible = plain(str(public_article))
    expected = BeautifulSoup(expected_content, 'html.parser')
    for node in expected.find_all(['p', 'h2']):
        if plain(str(node)) not in visible:
            raise ValueError('Public article text or section missing')
    for node in expected.find_all('a', href=True):
        if not public_article.find('a', href=node['href']):
            raise ValueError('Public source link missing')
    if media_id:
        media = wp.request('GET', 'wp/v2/media/'+str(media_id))
        served, _, _ = fetch_bytes(media['source_url'], max_bytes=10_000_000)
        if not image or pixels(served) != pixels(Path(image.path).read_bytes()):
            raise ValueError('Public featured-image pixels differ from reviewed source image')
        if not soup.find('img', src=media['source_url']) and not any(
                media['source_url'] in str(n) for n in soup.find_all('img')):
            raise ValueError('Featured image not present in the public article')
    return {'verified': True, 'post_id': post_id, 'url': url, 'text': True,
            'title_h1': True, 'description': True, 'self_canonical': True,
            'indexable': True, 'source_links': True, 'image_pixels': bool(media_id),
            'seo_plugin_fields_written': False}
