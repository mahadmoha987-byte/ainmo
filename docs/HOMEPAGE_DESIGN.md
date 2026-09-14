# Homepage redesign — 14 September 2026

## Reference and implementation

Inspected and scrolled https://themewagon.github.io/welfare/ through hero,
overlapping counter band, services, carousel, gallery, news and footer.
Computed styles confirmed Dosis headings (hero 60px/72px in reference) and
Overpass body (300, 16px/28.8px); reference uses 0.5-second fadeInUp reveals.
Implementation is original vanilla HTML/CSS/JS, not copied template code.
Uses the same font families, centered photo hero, overlapping three-column
band, generous vertical rhythm, inset image cards and staggered reveals.
Palette is cream with restrained pearlescent gradients. No calculation changes.

## Content provenance

- Bogotá property count: 2,965,917 (2026 inventory, NOT Ainmo usage or coverage).
  https://www.catastrobogota.gov.co/recurso/resultados-censo-inmobiliario-2025
  Despite the URL title, the official content reports the 2026 census.
- Hero: Random Institute, Unsplash https://unsplash.com/photos/QmxXYlyYgL8
- Aerial: Victor Rosario, Unsplash https://unsplash.com/photos/pk0TD7xmvXE
- Local files: static/bogota-skyline.jpg and static/bogota-aerial.jpg.
- Normative fallbacks link existing reviewed local articles and original acts.

## Automatic news

GET /api/home-news reads only the official Bogotá topic feeds:

- Hábitat: https://bogota.gov.co/taxonomy/term/4/feed
- Planeación: https://bogota.gov.co/taxonomy/term/47/feed

Category IDs verified against published Drupal currentPath metadata.
The generic /rss.xml currently serves unrelated international opportunities;
it is deliberately not used. Topic feeds contain 10 recent items each, so this
is a selected feed, NOT an exhaustive legal monitoring service.

Six-hour demand-driven refresh per process; ten-minute failure backoff;
async single-flight lock; two bounded feed requests plus at most three
bounded article-image requests. No credentials or database queries.
Only validated HTTPS bogota.gov.co URLs, no redirect following, byte limits,
timeouts and rejection of XML declarations of entities/DOCTYPE. Titles are
rendered as textContent, never HTML. Relevance excludes utility disruptions
and unrelated topics. Ranking is by recent week then keyword relevance/date.
News never modifies regulatory calculations. Visible fallback distinguishes
live, partial, stale and unavailable states; missing images use labelled
context photography. Without JS, the reviewed normative cards remain readable.

## Verification

- Existing 42 tests passed; 16 additional feed tests initially passed.
- Desktop 1280px and mobile 390px: no page overflow, same font families,
  menu, anchor navigation, carousel, images and console checked.
- Reduced-motion CSS/JS disables counters/reveals; content is visible without JS.
