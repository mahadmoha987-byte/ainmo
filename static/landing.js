(() => {
  'use strict';
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const nav = document.querySelector('.site-nav');
  const toggle = document.getElementById('navToggle');
  const links = document.getElementById('navLinks');
  function closeMenu() { links.classList.remove('open'); toggle.setAttribute('aria-expanded', 'false'); }
  toggle.addEventListener('click', () => { const open = links.classList.toggle('open'); toggle.setAttribute('aria-expanded', String(open)); });
  links.addEventListener('click', closeMenu);
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && links.classList.contains('open')) { closeMenu(); toggle.focus(); } });
  const updateNav = () => nav.classList.toggle('scrolled', window.scrollY > 80);
  window.addEventListener('scroll', updateNav, {passive:true}); updateNav();
  let revealObserver = null;
  function observeReveal(el, i = 0) {
    if (!revealObserver) return;
    el.classList.add('reveal', 'pending');
    el.style.setProperty('--delay', `${(i % 3) * 90}ms`);
    revealObserver.observe(el);
  }
  // Content stays readable without JS, without IntersectionObserver, and with reduced motion.
  if (!reduced && 'IntersectionObserver' in window) {
    document.documentElement.classList.add('motion');
    revealObserver = new IntersectionObserver(entries => entries.forEach(entry => {
      if (entry.isIntersecting) { entry.target.classList.remove('pending'); revealObserver.unobserve(entry.target); }
    }), {threshold:0.08});
    document.querySelectorAll('.reveal').forEach(observeReveal);
    document.addEventListener('focusin', e => e.target.closest('.reveal')?.classList.remove('pending'));
    const counter = document.querySelector('.counter');
    const observer = new IntersectionObserver(entries => {
      if (!entries.some(e => e.isIntersecting)) return;
      observer.disconnect();
      const end = Number(counter.dataset.count), start = performance.now();
      function tick(now) {
        const progress = Math.min((now - start) / 1600, 1);
        counter.textContent = Math.round(end * (1 - Math.pow(1 - progress, 3))).toLocaleString('es-CO');
        if (progress < 1) requestAnimationFrame(tick);
      }
      requestAnimationFrame(tick);
    });
    observer.observe(counter);
  }
  const track = document.getElementById('featureTrack');
  const cards = [...track.children];
  const position = document.getElementById('featurePosition');
  function cardLeft(card) { return card.offsetLeft - cards[0].offsetLeft; }
  function go(direction) {
    const max = track.scrollWidth - track.clientWidth;
    let destination = track.scrollLeft + direction * (cards[0].offsetWidth + parseFloat(getComputedStyle(track).gap));
    if (direction > 0 && track.scrollLeft >= max - 4) destination = 0;
    if (direction < 0 && track.scrollLeft <= 4) destination = max;
    track.scrollTo({left:Math.max(0, Math.min(destination, max)), behavior:reduced ? 'instant' : 'smooth'});
  }
  document.getElementById('featureNext').addEventListener('click', () => go(1));
  document.getElementById('featurePrev').addEventListener('click', () => go(-1));
  track.addEventListener('keydown', e => { if (e.target === track && ['ArrowLeft','ArrowRight'].includes(e.key)) { e.preventDefault(); go(e.key === 'ArrowRight' ? 1 : -1); } });
  function updatePosition() {
    const visible = cards.map((card, i) => ({i, left:cardLeft(card) - track.scrollLeft, width:card.offsetWidth}))
      .filter(card => card.left >= -2 && card.left + card.width <= track.clientWidth + 2);
    if (!visible.length) return;
    const first = visible[0].i + 1, last = visible[visible.length - 1].i + 1;
    position.textContent = `${first === last ? first : `${first}–${last}`} / ${cards.length}`;
  }
  track.addEventListener('scroll', updatePosition, {passive:true});
  window.addEventListener('resize', updatePosition, {passive:true});
  updatePosition();
  function safeUrl(value, image = false) {
    try { const u = new URL(value, location.origin); return u.protocol === 'https:' && ['bogota.gov.co','www.bogota.gov.co'].includes(u.hostname) && (!image || u.pathname.startsWith('/sites/default/')) ? u.href : null; } catch { return null; }
  }
  async function loadNews() {
    const status = document.getElementById('newsStatus');
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 18000);
    try {
      const response = await fetch('/api/home-news?v=2', {signal:controller.signal});
      if (!response.ok) throw new Error('news unavailable');
      const data = await response.json();
      if (!Array.isArray(data.items) || !data.items.length) throw new Error('no items');
      const articles = data.items.slice(0,3).flatMap(item => {
        const url = safeUrl(item.url); if (!url || typeof item.title !== 'string') return [];
        const article = document.createElement('article'); article.className = 'photo-card';
        const img = document.createElement('img'); img.src = safeUrl(item.image, true) || '/static/bogota-skyline.jpg'; img.alt = safeUrl(item.image, true) ? `Imagen de la publicación: ${item.title}` : 'Bogotá · imagen de contexto'; img.loading = 'lazy'; img.width = 1000; img.height = 667;
        img.addEventListener('error', () => { img.src = '/static/bogota-skyline.jpg'; img.alt = 'Bogotá · imagen de contexto'; }, {once:true});
        const copy = document.createElement('div'); copy.className = 'card-copy';
        const meta = document.createElement('span'); meta.className = 'card-kicker';
        const date = new Date(item.date); meta.textContent = `BOGOTÁ.GOV.CO · ${Number.isNaN(date.getTime()) ? 'PUBLICACIÓN OFICIAL' : date.toLocaleDateString('es-CO', {day:'numeric',month:'short',year:'numeric',timeZone:'America/Bogota'})}`;
        const heading = document.createElement('h3'), link = document.createElement('a'); link.href = url; link.target = '_blank'; link.rel = 'noopener'; link.textContent = item.title; heading.append(link);
        const p = document.createElement('p'); p.textContent = item.summary || 'Consulte la publicación completa en el portal oficial de Bogotá.';
        const cta = document.createElement('a'); cta.href = url; cta.target = '_blank'; cta.rel = 'noopener'; cta.className = 'text-link'; cta.textContent = 'Leer en la fuente oficial ↗';
        copy.append(meta, heading, p, cta); article.append(img, copy); return [article];
      });
      if (!articles.length) throw new Error('no valid items');
      document.getElementById('newsGrid').replaceChildren(...articles);
      articles.forEach(observeReveal);
      const checked = data.checked_at ? new Date(data.checked_at).toLocaleString('es-CO', {dateStyle:'short',timeStyle:'short',timeZone:'America/Bogota'}) : null;
      status.textContent = data.status === 'live' ? `Noticias oficiales · consulta ${checked} (Bogotá). Revisión automática cada 6 horas al visitar el sitio.` : data.status === 'partial' ? `Noticias oficiales · consulta parcial ${checked} (Bogotá). Una fuente no respondió; se reintentará.` : `Última selección disponible · no se pudo actualizar la fuente. Última consulta: ${checked || 'sin registro'}.`;
    } catch { status.textContent = 'Selección normativa · las noticias en vivo no están disponibles en este momento.'; }
    finally { clearTimeout(timeout); }
  }
  loadNews();
})();
