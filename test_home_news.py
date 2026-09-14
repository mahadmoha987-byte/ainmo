import asyncio
from datetime import datetime, timezone
import html

import httpx
import pytest

import home_news as news

NOW = datetime(2026, 9, 14, 16, tzinfo=timezone.utc)


def feed(title='Nueva norma de vivienda en Bogotá', url='https://bogota.gov.co/mi-ciudad/planeacion/norma', date='Sat, 12 Sep 2026 03:00:00 +0000', description=''):
    return f'<rss><channel><item><title>{html.escape(title)}</title><link>{html.escape(url)}</link><pubDate>{date}</pubDate><description>{html.escape(description)}</description></item></channel></rss>'.encode()


def test_relevance_excludes_daily_disruptions():
    assert news.relevance('Cortes de agua en urbanizaciones') == 0
    assert news.relevance('El área licenciada para construcciones repuntó') > 0
    assert news.relevance('Fontibón y su Distrito Aeroportuario') > 0
    assert news.relevance('Becas para estudiar en Canadá') == 0


def test_safe_images_and_plain_feed_content():
    items = news.parse_feed(feed(description='<img src="https://evil.test/tracker"><img src="https://bogota.gov.co/sites/default/a.jpg"><script>alert(1)</script>'), NOW)
    assert len(items) == 1
    assert items[0]['image'] == 'https://bogota.gov.co/sites/default/a.jpg'
    assert '<script' not in items[0]['summary']


@pytest.mark.parametrize('url', ['https://evil.test/a', 'http://bogota.gov.co/mi-ciudad/a', 'https://bogota.gov.co@evil.test/mi-ciudad/a', 'https://bogota.gov.co:8000/mi-ciudad/a', 'https://bogota.gov.co/no-publicada'])
def test_reject_external_or_invalid_links(url):
    assert news.parse_feed(feed(url=url), NOW) == []


@pytest.mark.parametrize('date', ['invalid', 'Sat, 12 Sep 2020 03:00:00 +0000', 'Sat, 12 Sep 2027 03:00:00 +0000'])
def test_reject_invalid_stale_and_future_dates(date):
    assert news.parse_feed(feed(date=date), NOW) == []


@pytest.mark.parametrize('content', [b'<!DOCTYPE rss><rss/>', b'<!ENTITY test "oops">', b'x' * (news.MAX_BYTES + 1), b'<html/>'])
def test_reject_unsafe_or_wrong_payload(content):
    with pytest.raises(ValueError):
        news.parse_feed(content, NOW)


def test_cache_single_flight_and_last_good_on_failure(monkeypatch):
    monkeypatch.setattr(news, '_next_check', 0)
    monkeypatch.setattr(news, '_cache', {'items': [], 'status': 'unavailable', 'checked_at': None})
    calls = []
    async def fetch(client, url):
        calls.append(url)
        await asyncio.sleep(0)
        return news.parse_feed(feed(), NOW)
    monkeypatch.setattr(news, '_fetch', fetch)
    async def image(client, item):
        return None
    monkeypatch.setattr(news, '_article_image', image)
    async def run():
        monkeypatch.setattr(news, '_lock', asyncio.Lock())
        results = await asyncio.gather(news.get_news(), news.get_news())
        assert len(calls) == 2  # Two sources, not two fetches per visitor.
        assert results[0]['status'] == 'live'
        assert len(results[0]['items']) == 1  # Duplicate URLs deduplicated.
        old_date = results[0]['checked_at']
        async def fail(client, url):
            raise httpx.ConnectError('offline')
        monkeypatch.setattr(news, '_fetch', fail)
        monkeypatch.setattr(news, '_next_check', 0)
        result = await news.get_news()
        assert result['status'] == 'stale'
        assert result['checked_at'] == old_date
        assert len(result['items']) == 1
        assert news._next_check > 0
    asyncio.run(run())


def test_empty_feed_is_not_a_live_claim(monkeypatch):
    async def fetch(client, url):
        return []
    monkeypatch.setattr(news, '_fetch', fetch)
    monkeypatch.setattr(news, '_next_check', 0)
    monkeypatch.setattr(news, '_cache', {'items': [], 'status': 'unavailable', 'checked_at': None})
    async def run():
        monkeypatch.setattr(news, '_lock', asyncio.Lock())
        result = await news.get_news()
        assert result['status'] == 'unavailable'
    asyncio.run(run())
