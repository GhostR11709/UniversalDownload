(() => {
  'use strict';

  const entries = new Map();
  const MIN_WIDTH = 180;
  const MIN_HEIGHT = 100;
  const BUTTON_WIDTH = 124;
  const BUTTON_HEIGHT = 36;

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

  function setPosition(entry, rect) {
    const offset = entry.offset || { x: rect.width - BUTTON_WIDTH - 12, y: 12 };
    const maxX = Math.max(8, rect.width - BUTTON_WIDTH - 8);
    const maxY = Math.max(8, rect.height - BUTTON_HEIGHT - 8);
    const x = Math.max(8, Math.min(maxX, offset.x));
    const y = Math.max(8, Math.min(maxY, offset.y));
    entry.host.style.left = `${Math.round(rect.left + x)}px`;
    entry.host.style.top = `${Math.round(rect.top + y)}px`;
    entry.host.hidden = false;
  }

  function render(entry) {
    const rect = visibleRect(entry.video);
    if (!rect) {
      entry.host.hidden = true;
      return;
    }
    setPosition(entry, rect);
  }

  function createEntry(video) {
    const host = document.createElement('div');
    host.setAttribute('data-ghostr-download', 'true');
    host.style.cssText = 'position:fixed;z-index:2147483647;width:124px;height:36px;display:block;pointer-events:auto;';
    const shadow = host.attachShadow({ mode: 'closed' });
    shadow.innerHTML = `<style>
      :host { all: initial; }
      button { width:124px; height:36px; padding:0 12px; border:1px solid rgba(196,181,253,.65); border-radius:11px; color:#fff; background:linear-gradient(110deg,#6d4ee8,#b06bd2); box-shadow:0 8px 24px rgba(24,12,58,.38); cursor:grab; font:700 12px/1 system-ui,-apple-system,"Segoe UI",sans-serif; letter-spacing:.01em; text-shadow:0 1px 2px rgba(0,0,0,.25); }
      button:hover { filter:brightness(1.1); }
      button:active, button.dragging { cursor:grabbing; }
      button.busy { cursor:wait; opacity:.82; }
    </style><button type="button" aria-label="Download this video">↓ GhostR</button>`;
    document.documentElement.appendChild(host);
    const button = shadow.querySelector('button');
    const entry = { video, host, button, offset: null, pointer: null, moved: false, busy: false };
    entries.set(video, entry);

    const stop = (event) => {
      event.preventDefault();
      event.stopPropagation();
    };
    button.addEventListener('pointerdown', (event) => {
      stop(event);
      const rect = video.getBoundingClientRect();
      entry.pointer = { id: event.pointerId, startX: event.clientX, startY: event.clientY, rect, offset: entry.offset || { x: rect.width - BUTTON_WIDTH - 12, y: 12 } };
      entry.moved = false;
      button.setPointerCapture?.(event.pointerId);
      button.classList.add('dragging');
    });
    button.addEventListener('pointermove', (event) => {
      if (!entry.pointer || entry.pointer.id !== event.pointerId) return;
      stop(event);
      const dx = event.clientX - entry.pointer.startX;
      const dy = event.clientY - entry.pointer.startY;
      if (Math.abs(dx) + Math.abs(dy) > 4) entry.moved = true;
      if (!entry.moved) return;
      entry.offset = { x: entry.pointer.offset.x + dx, y: entry.pointer.offset.y + dy };
      render(entry);
    });
    button.addEventListener('pointerup', (event) => {
      if (!entry.pointer || entry.pointer.id !== event.pointerId) return;
      stop(event);
      button.releasePointerCapture?.(event.pointerId);
      entry.pointer = null;
      button.classList.remove('dragging');
    });
    button.addEventListener('pointercancel', () => { entry.pointer = null; button.classList.remove('dragging'); });
    button.addEventListener('click', (event) => {
      stop(event);
      if (entry.moved || entry.busy) {
        entry.moved = false;
        return;
      }
      entry.busy = true;
      button.classList.add('busy');
      button.textContent = 'Ghosting…';
      chrome.runtime.sendMessage({ type: 'download-context', url: targetUrl(video), pageUrl: location.href, title: document.title }, (response) => {
        if (chrome.runtime.lastError || !response?.ok) {
          entry.busy = false;
          button.classList.remove('busy');
          button.textContent = 'Try again';
          window.setTimeout(() => { if (!entry.busy) button.textContent = '↓ GhostR'; }, 2400);
          return;
        }
        button.textContent = 'Queued ✓';
        window.setTimeout(() => { entry.busy = false; button.classList.remove('busy'); button.textContent = '↓ GhostR'; }, 2400);
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

  const observer = new MutationObserver(scan);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  addEventListener('scroll', scan, { passive: true });
  addEventListener('resize', scan, { passive: true });
  window.setInterval(scan, 1000);
  scan();
})();
