"""Bounded, tenant-scoped reads of observed storefront sessions; no ad writes."""
from collections import Counter
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

from .core import EVENT_COLLECTION

LABELS = {
    'snapchat': 'سناب شات', 'meta': 'ميتا', 'tiktok': 'تيك توك',
    'google_ads': 'إعلانات جوجل', 'google_organic': 'بحث جوجل الطبيعي',
    'google': 'جوجل — النوع غير محدد', 'direct': 'مباشر / دون إحالة ظاهرة',
    'referral': 'إحالات أخرى', 'other_campaign': 'حملات أخرى', 'unknown': 'غير معروف',
}
ALIASES = {'snap': 'snapchat', 'snapchat': 'snapchat', 'facebook': 'meta',
           'fb': 'meta', 'instagram': 'meta', 'ig': 'meta', 'meta': 'meta',
           'tiktok': 'tiktok', 'tik_tok': 'tiktok', 'google': 'google',
           'google_ads': 'google_ads', 'google_organic': 'google_organic'}
PAID = {'cpc', 'ppc', 'paid', 'paid_social', 'paid_search', 'display', 'cpm'}


def classify_entry(event):
    """Ignore persisted acquisition source: the snippet retains it for 90 days."""
    try:
        page = urlsplit(event.get('page_url') or '')
        ref = urlsplit(event.get('referrer') or '')
        if page.scheme not in ('http', 'https') or not page.hostname:
            return 'unknown', False
        query = parse_qs(page.query)
        source = (query.get('mz_source') or query.get('utm_source') or [''])[0].lower().strip()
        medium = (query.get('utm_medium') or [''])[0].lower().strip()
        if source:
            channel = ALIASES.get(source, 'other_campaign')
            paid = medium in PAID
            if channel == 'google':
                channel = 'google_ads' if paid else ('google_organic' if medium == 'organic' else 'google')
            return channel, paid or channel == 'google_ads'
        if any(query.get(key) for key in ('gclid', 'gbraid', 'wbraid')):
            return 'google_ads', True
        if query.get('ScCid') or query.get('sccid'):
            return 'snapchat', True
        if query.get('ttclid'):
            return 'tiktok', True
        if not event.get('referrer'):
            return 'direct', False
        host = (ref.hostname or '').lower()
        if not host or ref.scheme not in ('http', 'https'):
            return 'unknown', False
        if host.removeprefix('www.') == page.hostname.lower().removeprefix('www.'):
            return 'unknown', False
        for domain, channel in [('snapchat.com', 'snapchat'), ('facebook.com', 'meta'),
                                ('instagram.com', 'meta'), ('tiktok.com', 'tiktok'),
                                ('google.com', 'google_organic'), ('google.com.sa', 'google_organic')]:
            if host == domain or host.endswith('.' + domain):
                return channel, False
        return 'referral', False
    except (ValueError, TypeError):
        return 'unknown', False


def summarize(events, now):
    # Rows arrive oldest first. One first observed entry per store/visitor/session.
    sessions = {}
    for event in events:
        if not event.get('session_id') or not event.get('visitor_id'):
            continue
        key = (event.get('store_id'), event['visitor_id'], event['session_id'])
        sessions.setdefault(key, classify_entry(event))
    counts = Counter(channel for channel, _ in sessions.values())
    paid = Counter(channel for channel, evidence in sessions.values() if evidence)
    total = len(sessions)
    return {
        'window_minutes': 30, 'metric': 'observed_sessions',
        'generated_at': now.isoformat(), 'window_start': (now - timedelta(minutes=30)).isoformat(),
        'last_event_at': events[-1].get('received_at') if events else None,
        'total_sessions': total,
        'known_source_percent': round(100 * (total - counts['unknown']) / total, 1) if total else None,
        'sources': [{'key': key, 'label': label, 'sessions': counts[key],
                     'paid_evidence_sessions': paid[key],
                     'percent': round(counts[key] * 100 / total, 1) if total else 0}
                    for key, label in LABELS.items()],
    }


async def read_live_sources(db, user_id, now=None):
    now = now or datetime.now(timezone.utc)
    start = (now - timedelta(minutes=30)).isoformat()
    # Server receive time avoids a visitor clock widening the indexed window.
    query = {'user_id': user_id, 'store_scope': 'production',
             'received_at': {'$gte': start, '$lte': now.isoformat()}}
    projection = {'_id': 0, 'store_id': 1, 'visitor_id': 1, 'session_id': 1,
                  'page_url': 1, 'referrer': 1, 'received_at': 1}
    events = await db[EVENT_COLLECTION].find(query, projection).sort(
        'received_at', 1).limit(20001).max_time_ms(2500).to_list(20001)
    if len(events) > 20000:
        raise ValueError('live_sources_window_limit')
    return summarize(events, now)
