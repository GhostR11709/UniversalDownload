(() => {
  'use strict';

  const entries = new Map();
  const PREFS_KEY = 'prefs';
  const MIN_WIDTH = 160;
  const MIN_HEIGHT = 90;
  const BUTTON_SIZE = 26; // deliberately tiny: it sits on top of the video
  const GAP = 10;
  const DEFAULT_GLYPH = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 3h2v10.2l3.6-3.6L18 11l-6 6-6-6 1.4-1.4L11 13.2z"/><path d="M5 18h14v2H5z"/></svg>';

  const prefs = { folder: '', floating: true };

  function loadPrefs() {
    chrome.storage.local.get({ [PREFS_KEY]: {}, folder: '' }).then((stored) => {
      const saved = stored[PREFS_KEY] || {};
      prefs.floating = saved.floating !== false;
      prefs.folder = stored.folder || '';
      for (const entry of entries.values()) entry.host.hidden = !prefs.floating;
      scan();
    }).catch(() => {});
  }

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'local') return;
    if (changes.folder) prefs.folder = changes.folder.newValue || '';
    if (changes[PREFS_KEY]) {
      const saved = changes[PREFS_KEY].newValue || {};
      prefs.floating = saved.floating !== false;
      for (const entry of entries.values()) entry.host.hidden = !prefs.floating;
    }
  });

  function targetUrl(video) {
    const source = video.currentSrc || video.src || '';
    return /^https?:/i.test(source) ? source : location.href;
  }

  function visibleRect(video) {
    if (!video.isConnected) return null;
    const rect = video.getBoundingClientRect();
    if (rect.width < MIN_WIDTH || rect.height < MIN_HEIGHT || rect.bottom <= 0 || rect.top >= innerHeight) return null;
    return rect;
  }

  function defaultOffset(rect) {
    return { x: rect.width - BUTTON_SIZE - GAP, y: GAP };
  }

  function setPosition(entry, rect) {
    const offset = entry.offset || defaultOffset(rect);
    const maxX = Math.max(GAP, rect.width - BUTTON_SIZE - GAP);
    const maxY = Math.max(GAP, rect.height - BUTTON_SIZE - GAP);
    const x = Math.max(GAP, Math.min(maxX, offset.x));
    const y = Math.max(GAP, Math.min(maxY, offset.y));
    entry.host.style.left = `${Math.round(rect.left + x)}px`;
    entry.host.style.top = `${Math.round(rect.top + y)}px`;
    entry.host.hidden = false;
  }

  function render(entry) {
    if (!prefs.floating) { entry.host.hidden = true; return; }
    const rect = visibleRect(entry.video);
    if (!rect) { entry.host.hidden = true; return; }
    setPosition(entry, rect);
  }

  function createEntry(video) {
    const host = document.createElement('div');
    host.setAttribute('data-universal-download', 'true');
    host.style.cssText = `position:fixed;z-index:2147483647;width:${BUTTON_SIZE}px;height:${BUTTON_SIZE}px;display:block;pointer-events:auto;`;
    const shadow = host.attachShadow({ mode: 'closed' });
    shadow.innerHTML = `<style>
      :host { all: initial; }
      button {
        display:grid; place-items:center; box-sizing:border-box;
        width:${BUTTON_SIZE}px; height:${BUTTON_SIZE}px; padding:0; margin:0;
        border:1px solid rgba(255,255,255,.65); border-radius:50%;
        color:#fff; background:rgba(0,0,0,.55); backdrop-filter:blur(2px);
        cursor:pointer; font:700 13px/1 system-ui,-apple-system,"Segoe UI",sans-serif;
      }
      button:hover { background:#000; border-color:#fff; }
      button:active, button.dragging { cursor:grabbing; }
      button.busy { cursor:progress; opacity:.7; }
      button svg { width:13px; height:13px; fill:currentColor; pointer-events:none; }
    </style><button type="button" title="Download this video" aria-label="Download this video">${DEFAULT_GLYPH}</button>`;
    document.documentElement.appendChild(host);
    const button = shadow.querySelector('button');
    const entry = { video, host, button, offset: null, pointer: null, moved: false, busy: false };
    entries.set(video, entry);

    const stop = (event) => { event.preventDefault(); event.stopPropagation(); };
    const setGlyph = (glyph) => { button.innerHTML = glyph; };

    button.addEventListener('pointerdown', (event) => {
      stop(event);
      const rect = video.getBoundingClientRect();
      entry.pointer = {
        id: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        offset: entry.offset || defaultOffset(rect),
      };
      entry.moved = false;
      button.setPointerCapture?.(event.pointerId);
      button.classList.add('dragging');
    });

    button.addEventListener('pointermove', (event) => {
      if (!entry.pointer || entry.pointer.id !== event.pointerId) return;
      stop(event);
      const dx = event.clientX - entry.pointer.startX;
      const dy = event.clientY - entry.pointer.startY;
      if (Math.abs(dx) + Math.abs(dy) > 3) entry.moved = true;
      if (!entry.moved) return;
      entry.offset = { x: entry.pointer.offset.x + dx, y: entry.pointer.offset.y + dy };
      render(entry);
    });

    const release = (event) => {
      if (entry.pointer && event && entry.pointer.id !== event.pointerId) return;
      if (event) { stop(event); button.releasePointerCapture?.(event.pointerId); }
      entry.pointer = null;
      button.classList.remove('dragging');
    };
    button.addEventListener('pointerup', release);
    button.addEventListener('pointercancel', release);

    button.addEventListener('click', (event) => {
      stop(event);
      if (entry.moved) { entry.moved = false; return; }
      if (entry.busy) return;
      entry.busy = true;
      button.classList.add('busy');
      setGlyph('&#8230;');
      chrome.runtime.sendMessage({
        type: 'download-context',
        url: targetUrl(video),
        pageUrl: location.href,
        title: document.title,
        folder: prefs.folder,
      }, (response) => {
        if (chrome.runtime.lastError || !response?.ok) {
          setGlyph('!');
          window.setTimeout(() => { setGlyph(DEFAULT_GLYPH); button.classList.remove('busy'); entry.busy = false; }, 2200);
          return;
        }
        setGlyph('&#10003;');
        window.setTimeout(() => { setGlyph(DEFAULT_GLYPH); button.classList.remove('busy'); entry.busy = false; }, 1800);
      });
    });

    render(entry);
  }

  function scan() {
    document.querySelectorAll('video').forEach((video) => {
      if (!entries.has(video)) createEntry(video);
    });
    for (const [video, entry] of entries) {
      if (!video.isConnected) {
        entry.host.remove();
        entries.delete(video);
      } else {
        render(entry);
      }
    }
  }

  loadPrefs();
  const observer = new MutationObserver(scan);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  addEventListener('scroll', scan, { passive: true });
  addEventListener('resize', scan, { passive: true });
  window.setInterval(scan, 1200);
  scan();
})();
