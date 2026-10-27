"""Read-only cross-producer coverage reconciliation and Chicago daily accounting."""
import html
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
from safe_http import canonical_url

CHICAGO = ZoneInfo('America/Chicago')


def text_field(post, name):
    value = post.get(name, {})
    if not isinstance(value, dict):
        raise ValueError('Malformed WordPress article field')
    return value.get('raw', value.get('rendered', ''))


def source_identity(url):
    return canonical_url(html.unescape(url)).rstrip('/')


def linked_sources(post):
    links = set()
    for node in BeautifulSoup(text_field(post, 'content'), 'html.parser').find_all('a', href=True):
        try:
            links.add(source_identity(node['href']))
        except ValueError:
            continue
    return links


def local_publication_date(post):
    value = post.get('date_gmt')
    if not isinstance(value, str) or not value or value.startswith('0000-'):
        raise ValueError('Published post lacks an authoritative UTC date')
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(CHICAGO).date().isoformat()


class CoverageIndex:
    def __init__(self, posts, statuses, now=None):
        self.posts = {p['id']: p for p in posts}
        self.statuses = list(statuses)
        self.now = now or datetime.now(timezone.utc)
        self.sources = {}
        for post in self.posts.values():
            if post.get('status') not in statuses or type(post.get('id')) is not int:
                raise ValueError('Invalid reconciled post identity/status')
            for url in linked_sources(post):
                self.sources.setdefault(url, post['id'])

    def source_match(self, url):
        return self.sources.get(source_identity(url))

    def daily(self, target):
        day = self.now.astimezone(CHICAGO).date().isoformat()
        ids = sorted(p['id'] for p in self.posts.values()
                     if p['status'] == 'publish' and local_publication_date(p) == day)
        return {'date': day, 'timezone': 'America/Chicago', 'target': target,
                'published': len(ids), 'post_ids': ids, 'remaining': max(0, target-len(ids)),
                'goal_met': len(ids) >= target, 'roundup_sections_count_as_posts': False}

    def public_receipt(self, target, anchor_ids=()):
        return {'supported_statuses': self.statuses, 'posts_reconciled': len(self.posts),
                'counts_by_status': {s: sum(p['status'] == s for p in self.posts.values())
                                     for s in self.statuses},
                'anchors': [{'post_id': i, 'status': self.posts[i]['status']} for i in anchor_ids],
                'daily': self.daily(target),
                'inventory_scope': 'all supported statuses, modified in past 35 days plus fixed anchors; candidate source/title searches span all dates'}

    def event_candidates(self, title):
        stop = set('the and for from with that this will have into its news mississippi county says said today update new public'.split())
        def tokens(value):
            return {w for w in re.findall(r'[a-z0-9]+', html.unescape(value).lower())
                    if len(w) > 2 and w not in stop}
        wanted = tokens(title)
        ranked = []
        for post in self.posts.values():
            headline = text_field(post, 'title')
            headings = ' '.join(n.get_text(' ', strip=True) for n in
                               BeautifulSoup(text_field(post, 'content'), 'html.parser').find_all(['h2', 'h3']))
            common = wanted & tokens(headline + ' ' + headings)
            if len(common) >= 2 and len(common) / max(1, len(wanted)) >= 0.4:
                ranked.append((len(common), post))
        ranked.sort(key=lambda pair: (-pair[0], pair[1]['id']))
        if len(ranked) > 10:
            raise ValueError('Too many possible event matches; editorial reconciliation required')
        return [post for _, post in ranked]
