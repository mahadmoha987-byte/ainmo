"""Bounded, read-only official news feed. Never feeds the regulatory engine.

Refresh is demand-driven (six hours), with a ten-minute failure backoff and
last-good fallback. No database, credentials, arbitrary URLs or scheduled job.
"""
import asyncio
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import time
import unicodedata
from urllib.parse import urlparse, urljoin
import xml.etree.ElementTree as ET

import httpx

FEEDS = (
    'https://bogota.gov.co/taxonomy/term/4/feed',  # Hábitat
    'https://bogota.gov.co/taxonomy/term/47/feed',  # Planeación
)
TTL = 6 * 60 * 60
BACKOFF = 10 * 60
MAX_BYTES = 2_000_000
_cache = {'items': [], 'checked_at': None, 'status': 'unavailable'}
_next_check = 0.0
_lock = asyncio.Lock()


def safe_url(value: str, image: bool = False) -> str | None:
    if not value:
        return None
    url = urljoin('https://bogota.gov.co/', value)
    p = urlparse(url)
    if (p.scheme != 'https' or p.hostname not in ('bogota.gov.co', 'www.bogota.gov.co')
            or p.username or p.password or p.port not in (None, 443)):
        return None
    if not p.path.startswith('/sites/default/' if image else '/mi-ciudad/'):
        return None
    return url


class ImageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.image = None

    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        if tag == 'meta' and data.get('property') == 'og:image':
            self.image = safe_url(data.get('content', ''), image=True)
        if tag == 'img' and not self.image:
            self.image = safe_url(data.get('src', ''), image=True)


def relevance(title: str) -> int:
    title = ''.join(c for c in unicodedata.normalize('NFD', title.lower())
                    if unicodedata.category(c) != 'Mn')
    if any(t in title for t in ('cortes de agua', 'cortes de luz', 'sisben', 'ecopuntos',
                                'datos curiosos', 'ciclos deliberativos')):
        return 0
    return sum(weight for term, weight in (
        ('ordenamiento', 5), ('decreto', 5), ('norma', 4), ('licencia', 5),
        ('suelo', 4), ('urban', 4), ('renovacion', 4), ('construccion', 3),
        ('vivienda', 3), ('predial', 4), ('distrito aeroportuario', 4),
        ('ondas metropolitanas', 3), ('bronx distrito', 3), ('sostenible', 2),
    ) if term in title)


def topic_summary(title: str) -> str:
    title = title.lower()
    if 'aeroportuario' in title:
        return 'El entorno del aeropuerto está en discusión. Conozca el espacio de participación y los temas de transformación territorial que plantea la ciudad.'
    if 'bronx' in title:
        return 'Una mirada a la transformación del centro de Bogotá. Consulte el avance del proyecto y el contexto de su recuperación urbana.'
    if 'ondas metropolitanas' in title:
        return 'Movilidad y transformación urbana se encuentran en este proyecto. Conozca la propuesta y su relación con el territorio.'
    if 'licencia' in title:
        return 'Siga la actividad constructora de la ciudad a través de la información oficial sobre licenciamiento y nuevos proyectos.'
    if 'vivienda' in title:
        return 'Novedades del sector vivienda en Bogotá. Revise el alcance, las condiciones y los detalles publicados por el Distrito.'
    return 'Una actualización para seguir la transformación de Bogotá. Consulte los detalles y el alcance en la publicación oficial.'


def parse_feed(content: bytes, now: datetime | None = None) -> list[dict]:
    if len(content) > MAX_BYTES or b'<!DOCTYPE' in content.upper() or b'<!ENTITY' in content.upper():
        raise ValueError('Unsupported feed payload')
    now = now or datetime.now(timezone.utc)
    root = ET.fromstring(content)
    if root.tag != 'rss':
        raise ValueError('Expected RSS')
    items = []
    for item in root.findall('./channel/item')[:60]:
        title = ' '.join((item.findtext('title') or '').split())[:220]
        score = relevance(title)
        try:
            url = safe_url(item.findtext('link') or '')
            date = parsedate_to_datetime(item.findtext('pubDate') or '')
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError, OverflowError):
            continue
        if not url or not score or not now - timedelta(days=120) <= date <= now + timedelta(hours=1):
            continue
        parser = ImageParser()
        try:
            parser.feed((item.findtext('description') or '')[:100_000])
        except ValueError:
            pass
        items.append({'title': title, 'url': url, 'image': parser.image,
                      'date': date.isoformat(), 'score': score,
                      'summary': topic_summary(title)})
    return items


async def _fetch(client: httpx.AsyncClient, url: str) -> list[dict]:
    async with client.stream('GET', url) as response:
        response.raise_for_status()
        chunks, size = [], 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > MAX_BYTES:
                raise ValueError('Feed too large')
            chunks.append(chunk)
    return parse_feed(b''.join(chunks))


async def _article_image(client: httpx.AsyncClient, item: dict) -> None:
    if item['image']:
        return
    try:
        # Only already-validated official article links, never user input.
        async with client.stream('GET', item['url'], timeout=3) as response:
            response.raise_for_status()
            parser, size = ImageParser(), 0
            async for chunk in response.aiter_text():
                size += len(chunk)
                if size > 400_000:
                    break
                parser.feed(chunk)
                if parser.image:
                    item['image'] = parser.image
                    break
    except (httpx.HTTPError, ValueError):
        pass  # A missing image does not discard a useful article.


async def get_news() -> dict:
    global _cache, _next_check
    if time.monotonic() < _next_check:
        return _cache
    async with _lock:
        if time.monotonic() < _next_check:
            return _cache
        try:
            # Fixed HTTPS origins; redirects intentionally not followed.
            async with httpx.AsyncClient(timeout=8, headers={'User-Agent': 'Ainmo-News/1.0 (+https://ainmo.uk)'}) as client:
                results = await asyncio.wait_for(asyncio.gather(*(_fetch(client, url) for url in FEEDS), return_exceptions=True), timeout=10)
            good = [result for result in results if isinstance(result, list)]
            unique = {item['url']: item for result in good for item in result}
            # Recent items first, with relevance as a tie-breaker inside each week.
            ordered = sorted(unique.values(), key=lambda item: (
                int(datetime.fromisoformat(item['date']).timestamp()) // (7 * 86400),
                item['score'], item['date']), reverse=True)[:3]
            if ordered:
                async with httpx.AsyncClient(timeout=3, headers={'User-Agent': 'Ainmo-News/1.0 (+https://ainmo.uk)'}) as client:
                    try:
                        await asyncio.wait_for(asyncio.gather(*(_article_image(client, item) for item in ordered)), timeout=4)
                    except asyncio.TimeoutError:
                        pass
                _cache = {'items': ordered, 'checked_at': datetime.now(timezone.utc).isoformat(),
                          'status': 'live' if len(good) == len(FEEDS) else 'partial'}
                _next_check = time.monotonic() + (TTL if len(good) == len(FEEDS) else BACKOFF)
            else:
                _cache = {**_cache, 'status': 'stale' if _cache['items'] else 'unavailable'}
                _next_check = time.monotonic() + BACKOFF
        except (httpx.HTTPError, ValueError, asyncio.TimeoutError):
            _cache = {**_cache, 'status': 'stale' if _cache['items'] else 'unavailable'}
            _next_check = time.monotonic() + BACKOFF
        return _cache
