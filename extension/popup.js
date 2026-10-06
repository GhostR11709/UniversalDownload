const API = 'http://127.0.0.1:8756';
const $ = (selector) => document.querySelector(selector);
const PREFS_KEY = 'prefs';
const RECENT_KEY = 'recentFolders';
const BOUNDS_KEY = 'windowBounds';

const state = {
  preview: null,
  server: false,
  health: null,
  poll: null,
  folder: '',
  pickerFolder: '',
  pickerBefore: '',
  previewExt: '',
  prefs: {
    metadata: false,
    embed: true,
    notify: true,
    floating: true,
    quality: 'best',
    maxHeight: 0,
    parallel: 2,
    avgSpeed: 0,
  },
};

async function request(path, options = {}) {
  const response = await fetch(`${API}${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.ok === false) throw new Error(data.hint || data.error || `Request failed (${response.status})`);
  return data;
}

function showToast(message, isError = false) {
  const toast = $('#toast');
  toast.textContent = message;
  toast.className = `toast show${isError ? ' error' : ''}`;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => { toast.className = 'toast'; }, 3800);
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[char]));
}

function escapeAttr(value) { return escapeHtml(value).replace(/`/g, '&#96;'); }

function formatSize(bytes) {
  if (!bytes || bytes <= 0) return '';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) { value /= 1024; index += 1; }
  return `${value.toFixed(index ? 1 : 0)} ${units[index]}`;
}

function formatSpeed(bytesPerSecond) {
  return bytesPerSecond ? `${formatSize(bytesPerSecond)}/s` : '';
}

// "1h 04m" / "3m 20s" / "42s" - the number people actually want to see.
function formatDuration(seconds) {
  if (seconds === null || seconds === undefined || !Number.isFinite(Number(seconds)) || Number(seconds) < 0) return '';
  const total = Math.round(Number(seconds));
  if (total < 1) return 'almost done';
  if (total < 60) return `${total}s`;
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (hours) return `${hours}h ${String(minutes).padStart(2, '0')}m`;
  return `${minutes}m ${String(total % 60).padStart(2, '0')}s`;
}

const FALLBACK_SPEED = 4 * 1024 * 1024;

// Rolling average of real speeds, so "time left" is based on what this machine
// actually manages instead of a guess.
function observedSpeed(bytesPerSecond) {
  if (!bytesPerSecond || bytesPerSecond < 1024) return;
  const previous = state.prefs.avgSpeed || bytesPerSecond;
  state.prefs.avgSpeed = Math.round(previous * 0.6 + bytesPerSecond * 0.4);
  savePrefs();
}

function savePrefs() { chrome.storage.local.set({ [PREFS_KEY]: state.prefs, folder: state.folder }); }

function joinPath(folder, name) {
  if (!folder) return name || '';
  const separator = folder.includes('\\') && !folder.includes('/') ? '\\' : '/';
  return `${folder.replace(/[\\/]+$/, '')}${separator}${name || ''}`;
}

// --------------------------------------------------------------------------- //
// engine
// --------------------------------------------------------------------------- //
function setServerStatus(online, label) {
  state.server = online;
  $('#status-dot').className = `status-dot ${online ? 'online' : 'offline'}`;
  $('#engine-status').textContent = label || (online ? 'Local engine ready' : 'Engine offline');
}

async function checkHealth() {
  try {
    const health = await request('/api/health');
    state.health = health;
    state.server = true;
    setServerStatus(true, `Engine ready · ${health.active || 0} active · ${health.ffmpeg ? 'FFmpeg found' : 'FFmpeg missing'}`);
    const notes = [];
    if (!health.ffmpeg || !health.ffprobe) notes.push('FFmpeg/FFprobe was not found, so quality fitting is unavailable.');
    if (!health.cookies && !health.cookies_from_browser) notes.push('No cookies are set, so private or age-restricted posts may fail.');
    $('#engine-note').textContent = notes.join(' ');
    if (!state.folder) {
      state.folder = health.default_folder || state.folder;
      $('#location-input').value = state.folder;
      renderPathPreview();
    }
    if (!Number(state.prefs.maxHeight)) {
      state.prefs.maxHeight = health.max_height || 0;
      $('#height-select').value = String(state.prefs.maxHeight);
    }
    if (!Number(state.prefs.parallel)) {
      state.prefs.parallel = health.max_concurrent || 2;
      $('#parallel-select').value = String(state.prefs.parallel);
    }
  } catch {
    state.server = false;
    setServerStatus(false, 'Engine offline — start it with setup.bat or start_server.bat');
    $('#engine-note').textContent = 'The local engine is not answering on 127.0.0.1:8756, so nothing can be downloaded yet.';
  }
}

// --------------------------------------------------------------------------- //
// which page is the user on?
// This page lives in its own window, so the active tab here is always us.
// The service worker remembers the last real web tab instead.
// --------------------------------------------------------------------------- //
async function rememberedTab() {
  const fromQuery = new URLSearchParams(location.search).get('url');
  if (fromQuery && /^https?:/i.test(fromQuery)) return { url: fromQuery };
  try {
    const response = await chrome.runtime.sendMessage({ type: 'get-active-tab' });
    if (response?.ok && response.tab?.url) return response.tab;
  } catch { /* worker asleep */ }
  // Last resort: any other browser window's active tab.
  try {
    const self = await chrome.windows.getCurrent();
    const windows = await chrome.windows.getAll({ populate: true });
    for (const win of windows) {
      if (win.id === self?.id) continue;
      const tab = (win.tabs || []).find((item) => item.active && /^https?:/i.test(item.url || ''));
      if (tab) return tab;
    }
  } catch { /* nothing readable */ }
  return null;
}

// --------------------------------------------------------------------------- //
// filename + destination
// --------------------------------------------------------------------------- //
function originalName() {
  return state.preview?.suggested_filename || state.preview?.title || '';
}

function renderPathPreview() {
  const folder = state.folder || '(no folder picked yet)';
  const name = $('#filename-input').value.trim() || originalName() || 'video';
  const full = state.folder ? joinPath(state.folder, `${name}${state.previewExt}`) : name;
  const target = $('#path-preview');
  target.innerHTML = `Will save to: <span>${escapeHtml(full)}</span>`;
  target.title = full;
}

function rememberFolder(folder) {
  if (!folder) return;
  chrome.storage.local.get(RECENT_KEY).then((stored) => {
    const list = [folder, ...(stored[RECENT_KEY] || []).filter((item) => item !== folder)].slice(0, 6);
    chrome.storage.local.set({ [RECENT_KEY]: list });
  });
}

// --------------------------------------------------------------------------- //
// link preview
// --------------------------------------------------------------------------- //
function pickFormats(media, quality) {
  const formats = media.formats || [];
  if (quality === 'audio') {
    return formats.filter((f) => f.acodec && f.acodec !== 'none' && (!f.vcodec || f.vcodec === 'none'));
  }
  const target = Number(quality) || Number(state.health?.max_height) || 1080;
  const usable = formats.filter((f) => f.height && f.height <= target);
  return usable.length ? usable : formats.filter((f) => f.height);
}

function estimateSize(media, quality) {
  const pool = pickFormats(media, quality);
  if (!pool.length) return null;
  if (quality === 'audio') return Math.max(...pool.map((f) => f.filesize || 0)) || null;
  const withVideo = pool.filter((f) => f.vcodec && f.vcodec !== 'none' && f.acodec && f.acodec !== 'none');
  const chosen = (withVideo.length ? withVideo : pool).sort((a, b) => (b.height || 0) - (a.height || 0))[0];
  return chosen?.filesize || null;
}

function extensionFor(media, quality) {
  if (quality === 'audio') return '.mp3';
  const pool = pickFormats(media, quality);
  const ext = pool.map((f) => f.ext).find((value) => value && value !== 'unknown');
  return ext ? `.${ext}` : '';
}

function renderPreview(media) {
  state.preview = media;
  const quality = $('#quality-select').value;
  state.previewExt = extensionFor(media, quality);
  $('#preview').classList.remove('hidden');
const size = estimateSize(media, quality);
  const speed = state.prefs.avgSpeed || FALLBACK_SPEED;
  const eta = size ? formatDuration(size / speed) : '';
  const facts = [
    media.height ? `${media.width || '?'}×${media.height}` : null,
    media.duration_text,
    formatSize(size),
    eta ? `${eta} left` : null,
    media.is_live ? 'LIVE' : null,
  ].filter(Boolean);

  $('#preview').innerHTML = `
    <div class="preview-top">
      ${media.thumbnail ? `<img class="preview-thumb" src="${escapeAttr(media.thumbnail)}" alt="" referrerpolicy="no-referrer">` : '<div class="preview-thumb"></div>'}
      <div class="preview-copy">
        <p class="eyebrow">${escapeHtml(media.emoji || 'WEB')} ${escapeHtml(media.platform || 'Web')}</p>
        <div class="preview-title">${escapeHtml(media.title || 'Untitled media')}</div>
        <div class="preview-meta">${escapeHtml(media.uploader || 'Unknown creator')}${media.extractor ? ` · ${escapeHtml(media.extractor)}` : ''}</div>
        <div class="preview-facts">${facts.map((fact) => `<span>${escapeHtml(fact)}</span>`).join('')}</div>
      </div>
    </div>
    <div class="preview-footer">
      <button class="primary-button" id="queue-button" type="button"><span>Download</span><b>↓</b></button>
    </div>`;

  $('#queue-button').addEventListener('click', queuePreview);
  if (!$('#filename-input').value.trim() || $('#filename-input').dataset.auto === '1') {
    $('#filename-input').value = originalName();
    $('#filename-input').dataset.auto = '1';
  }
  renderPathPreview();
}

async function inspectLink(event) {
  event?.preventDefault();
  const url = $('#url-input').value.trim();
  if (!url) return;
  if (!state.server) { showToast('The local engine is offline. Start it first.', true); return; }
  const button = $('#inspect-button');
  button.disabled = true;
  button.querySelector('span').textContent = 'Inspecting…';
  try {
    const result = await request('/api/detect', { method: 'POST', body: JSON.stringify({ url }) });
    renderPreview(result.media);
  } catch (error) {
    $('#preview').classList.add('hidden');
    showToast(error.message, true);
  } finally {
    button.disabled = false;
    button.querySelector('span').textContent = 'Inspect link';
  }
}

async function queuePreview() {
  if (!state.preview) return;
  const button = $('#queue-button');
  button.disabled = true;
  try {
    state.folder = ($('#location-input').value || '').trim() || state.folder;
    const result = await request('/api/downloads', {
      method: 'POST',
      body: JSON.stringify({
        url: state.preview.url,
        title: state.preview.title,
        quality: $('#quality-select').value,
        filename: $('#filename-input').value.trim(),
        folder: state.folder,
        metadata: state.prefs.metadata,
        embed_tags: state.prefs.embed !== false,
      }),
    });
    rememberFolder(state.folder);
    chrome.runtime.sendMessage({ type: 'watch-job', jobId: result.job.id }).catch(() => {});
    showToast(`Downloading to ${state.folder || 'the default folder'}.`);
    startPolling();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

// --------------------------------------------------------------------------- //
// queue
// --------------------------------------------------------------------------- //
async function cancelJob(id) {
  try {
    await request(`/api/jobs/${encodeURIComponent(id)}/cancel`, { method: 'POST', body: '{}' });
    startPolling();
  } catch (error) { showToast(error.message, true); }
}

async function retryJob(job) {
  try {
    const result = await request('/api/downloads', {
      method: 'POST',
      body: JSON.stringify({
        url: job.url,
        title: job.title,
        quality: job.quality,
        filename: job.requested_filename,
        folder: job.folder,
        metadata: state.prefs.metadata,
        embed_tags: state.prefs.embed !== false,
      }),
    });
    chrome.runtime.sendMessage({ type: 'watch-job', jobId: result.job.id }).catch(() => {});
    startPolling();
  } catch (error) { showToast(error.message, true); }
}

async function reveal(id, type) {
  try {
    await chrome.runtime.sendMessage({ type, jobId: id });
  } catch (error) {
    showToast(error.message || 'Could not open it on the server.', true);
  }
}

async function deleteFile(id) {
  try {
    await request(`/api/jobs/${encodeURIComponent(id)}/file`, { method: 'DELETE' });
    showToast('File deleted from disk.');
    startPolling();
  } catch (error) { showToast(error.message, true); }
}

async function copyLink(job) {
  try {
    await navigator.clipboard.writeText(job.url);
    showToast('Link copied.');
  } catch {
    showToast('Chrome blocked clipboard access.', true);
  }
}

function renderJobs(jobs) {
  const queue = $('#queue');
  const active = jobs.some((job) => ['queued', 'running'].includes(job.state));
  jobs.filter((job) => job.state === 'running' && job.progress?.speed)
    .forEach((job) => observedSpeed(job.progress.speed));
  $('#clear-history').classList.toggle('hidden', !jobs.some((job) => ['done', 'error', 'cancelled'].includes(job.state)));
  if (!jobs.length) {
    queue.innerHTML = '<div class="empty-state"><div class="empty-icon">↓</div><p>No downloads yet.</p><span>Paste a link above, pick a folder, press Download.</span></div>';
    return;
  }
  queue.innerHTML = jobs.slice(0, 12).map((job) => {
    const percent = Math.max(0, Math.min(100, Number(job.progress?.percent || 0)));
    const running = ['queued', 'running'].includes(job.state);
    const done = job.state === 'done';
    const status = done ? 'Saved'
      : job.state === 'error' ? 'Failed'
      : job.state === 'cancelled' ? 'Cancelled'
      : (job.progress?.stage === 'downloading' && job.progress?.eta
        ? `${formatDuration(job.progress.eta)} left`
        : (job.progress?.stage || `${percent.toFixed(0)}%`));

    const bits = [job.platform || 'Web', job.quality];
    if (job.size) bits.push(formatSize(job.size));
    if (job.width && job.height) bits.push(`${job.width}×${job.height}`);
    if (running) {
      if (job.progress?.speed) bits.push(formatSpeed(job.progress.speed));
      const left = job.progress?.eta;
      if (left !== null && left !== undefined) bits.push(`${formatDuration(left)} left`);
    }
    if (done) {
      if (job.tags_embedded) bits.push('tags embedded');
      else if (job.metadata_filename) bits.push('metadata saved');
    }

    const actions = [];
    if (running) actions.push(`<button data-cancel="${job.id}">Cancel</button>`);
    if (done) {
      actions.push(`<button class="primary-action" data-open="${job.id}">Open file</button>`);
      actions.push(`<button data-folder="${job.id}">Show folder</button>`);
      actions.push(`<button data-copy="${job.id}">Copy link</button>`);
      actions.push(`<button data-delete="${job.id}">Delete file</button>`);
    }
    if (job.state === 'error' || job.state === 'cancelled') {
      actions.push(`<button class="primary-action" data-retry="${job.id}">Retry</button>`);
      actions.push(`<button data-copy="${job.id}">Copy link</button>`);
    }

    const details = job.state === 'error'
      ? `<div class="job-error">${escapeHtml(job.error || 'Download failed.')}${job.hint ? `<div class="job-hint">${escapeHtml(job.hint.replace(/<[^>]*>/g, ''))}</div>` : ''}</div>`
      : done ? `<div class="job-path">${escapeHtml(job.saved_path || job.filename || '')}</div>` : '';

    return `<article class="job${running ? ' active' : ''}">
      <div class="queue-head">
        <div class="job-icon">${escapeHtml(job.emoji || '↓')}</div>
        <div class="job-copy">
          <div class="job-title">${escapeHtml(job.title || job.url || 'Untitled download')}</div>
          <div class="job-meta">${bits.filter(Boolean).map((bit) => `<b>${escapeHtml(bit)}</b>`).join(' · ')}</div>
        </div>
        <span class="job-status ${job.state}">${escapeHtml(status)}</span>
      </div>
      <div class="progress-track"><div class="progress-bar" style="width:${done ? 100 : percent}%"></div></div>
      ${details}
      ${actions.length ? `<div class="job-actions">${actions.join('')}</div>` : ''}
    </article>`;
  }).join('');

  const byId = (id) => jobs.find((job) => job.id === id);
  queue.querySelectorAll('[data-cancel]').forEach((el) => el.addEventListener('click', () => cancelJob(el.dataset.cancel)));
  queue.querySelectorAll('[data-open]').forEach((el) => el.addEventListener('click', () => reveal(el.dataset.open, 'open')));
  queue.querySelectorAll('[data-folder]').forEach((el) => el.addEventListener('click', () => reveal(el.dataset.folder, 'reveal')));
  queue.querySelectorAll('[data-delete]').forEach((el) => el.addEventListener('click', () => deleteFile(el.dataset.delete)));
  queue.querySelectorAll('[data-copy]').forEach((el) => el.addEventListener('click', () => copyLink(byId(el.dataset.copy))));
  queue.querySelectorAll('[data-retry]').forEach((el) => el.addEventListener('click', () => retryJob(byId(el.dataset.retry))));

  if (active) state.poll = setTimeout(loadJobs, 1100); else clearTimeout(state.poll);
}

async function loadJobs() {
  try { renderJobs(await request('/api/jobs')); } catch { /* the health row reports offline */ }
}

function startPolling() { clearTimeout(state.poll); loadJobs(); }

// --------------------------------------------------------------------------- //
// folder picker
// --------------------------------------------------------------------------- //
async function paintFolder(path) {
  state.pickerFolder = path;
  $('#picker-crumbs').textContent = path;
  const list = $('#picker-list');
  list.innerHTML = '<button class="empty" disabled>Loading…</button>';
  try {
    const data = await request(`/api/fs/list?path=${encodeURIComponent(path)}`);
    state.pickerFolder = data.path;
    $('#picker-crumbs').textContent = data.path;
    $('#picker-up').disabled = !data.parent;
    $('#picker-up').dataset.path = data.parent || '';
    if (!data.writable) {
      list.innerHTML = '<button class="empty" disabled>This folder cannot be written to. Pick another one.</button>';
    } else if (!data.entries.length) {
      list.innerHTML = '<button class="empty" disabled>No sub-folders here. You can still use this folder.</button>';
    } else {
      list.innerHTML = data.entries.map((entry) => `<button data-path="${escapeAttr(entry.path)}">📁 ${escapeHtml(entry.name)}</button>`).join('');
      list.querySelectorAll('[data-path]').forEach((el) => el.addEventListener('click', () => paintFolder(el.dataset.path)));
    }
  } catch (error) {
    list.innerHTML = `<button class="empty" disabled>${escapeHtml(error.message)}</button>`;
  }
  // Show the destination live while browsing, before anything is confirmed.
  state.folder = state.pickerFolder;
  $('#location-input').value = state.pickerFolder;
  renderPathPreview();
}

async function paintRecents() {
  const stored = await chrome.storage.local.get(RECENT_KEY);
  const recent = (stored[RECENT_KEY] || []).slice(0, 6);
  $('#picker-recent').innerHTML = recent.length
    ? recent.map((path) => `<button data-path="${escapeAttr(path)}" title="${escapeAttr(path)}">${escapeHtml(path)}</button>`).join('')
    : '<span class="field-note">Folders you pick show up here.</span>';
  $('#picker-recent').querySelectorAll('[data-path]').forEach((el) => el.addEventListener('click', () => paintFolder(el.dataset.path)));
}

async function openPicker() {
  state.pickerBefore = state.folder;
  $('#picker').classList.remove('hidden');
  const list = $('#picker-list');
  list.innerHTML = '<button class="empty" disabled>Loading…</button>';
  paintRecents();
  try {
    const roots = await request('/api/fs/roots');
    const quick = [...roots.special, ...roots.roots.filter((root) => !roots.special.some((item) => item.path.toLowerCase().startsWith(root.path.toLowerCase())))];
    list.innerHTML = quick.map((entry) => `<button data-path="${escapeAttr(entry.path)}">${escapeHtml(entry.label === entry.path ? entry.path : `${entry.label} — ${entry.path}`)}</button>`).join('');
    list.querySelectorAll('[data-path]').forEach((el) => el.addEventListener('click', () => paintFolder(el.dataset.path)));
  } catch (error) {
    list.innerHTML = `<button class="empty" disabled>${escapeHtml(error.message)}</button>`;
  }
}

function closePicker(apply) {
  $('#picker').classList.add('hidden');
  if (!apply) {
    state.folder = state.pickerBefore;
    $('#location-input').value = state.folder;
    renderPathPreview();
  } else {
    rememberFolder(state.folder);
    savePrefs();
  }
}

// --------------------------------------------------------------------------- //
// wiring
// --------------------------------------------------------------------------- //
$('#download-form').addEventListener('submit', inspectLink);

$('#paste-button').addEventListener('click', async () => {
  try {
    $('#url-input').value = await navigator.clipboard.readText();
    $('#url-input').focus();
  } catch { showToast('Chrome blocked clipboard access. Paste with Ctrl+V.', true); }
});

$('#current-tab').addEventListener('click', async () => {
  const tab = await rememberedTab();
  if (!tab?.url) { showToast('No web page is open to read. Paste a link instead.', true); return; }
  $('#url-input').value = tab.url;
  inspectLink();
});

$('#filename-input').addEventListener('input', (event) => { event.target.dataset.auto = '0'; renderPathPreview(); });
$('#quality-select').addEventListener('change', () => {
  state.prefs.quality = $('#quality-select').value;
  savePrefs();
  if (state.preview) renderPreview(state.preview);
});

$('#original-name').addEventListener('click', () => {
  const input = $('#filename-input');
  input.value = originalName();
  input.dataset.auto = '1';
  renderPathPreview();
});

$('#location-input').addEventListener('input', (event) => { state.folder = event.target.value.trim(); renderPathPreview(); });
$('#location-input').addEventListener('change', () => { state.folder = $('#location-input').value.trim(); rememberFolder(state.folder); savePrefs(); renderPathPreview(); });
$('#pick-location').addEventListener('click', () => { if (state.server) openPicker(); else showToast('The local engine is offline.', true); });
$('#picker-up').addEventListener('click', (event) => { const target = event.currentTarget.dataset.path; if (target) paintFolder(target); });
$('#picker-close').addEventListener('click', () => closePicker(false));
$('#picker-cancel').addEventListener('click', () => closePicker(false));
$('#picker-apply').addEventListener('click', () => closePicker(true));
$('#picker').addEventListener('mousedown', (event) => { if (event.target === $('#picker')) closePicker(false); });

$('#picker-new').addEventListener('click', async () => {
  const name = window.prompt('New folder name', 'New folder');
  if (!name) return;
  try {
    const created = await request('/api/fs/mkdir', { method: 'POST', body: JSON.stringify({ path: state.pickerFolder, name }) });
    await paintFolder(created.path);
  } catch (error) { showToast(error.message, true); }
});

$('#metadata-checkbox').addEventListener('change', (event) => { state.prefs.metadata = event.target.checked; savePrefs(); });
$('#embed-checkbox').addEventListener('change', (event) => { state.prefs.embed = event.target.checked; savePrefs(); });
$('#notify-checkbox').addEventListener('change', (event) => { state.prefs.notify = event.target.checked; savePrefs(); });
$('#floating-checkbox').addEventListener('change', (event) => { state.prefs.floating = event.target.checked; savePrefs(); });

$('#height-select').addEventListener('change', async (event) => {
  state.prefs.maxHeight = Number(event.target.value);
  savePrefs();
  try { await request('/api/settings', { method: 'POST', body: JSON.stringify({ max_height: state.prefs.maxHeight }) }); }
  catch (error) { showToast(error.message, true); }
});

$('#parallel-select').addEventListener('change', async (event) => {
  state.prefs.parallel = Number(event.target.value);
  savePrefs();
  try { await request('/api/settings', { method: 'POST', body: JSON.stringify({ max_concurrent: state.prefs.parallel }) }); }
  catch (error) { showToast(error.message, true); }
});

$('#reset-options').addEventListener('click', async () => {
  $('#filename-input').value = originalName();
  $('#filename-input').dataset.auto = '1';
  $('#url-input').value = '';
  state.preview = null;
  state.previewExt = '';
  $('#preview').classList.add('hidden');
  renderPathPreview();
  const health = state.health;
  if (health?.default_folder) { state.folder = health.default_folder; $('#location-input').value = state.folder; renderPathPreview(); }
});

$('#clear-history').addEventListener('click', async () => {
  try { await request('/api/jobs', { method: 'DELETE' }); startPolling(); } catch (error) { showToast(error.message, true); }
});

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && !$('#picker').classList.contains('hidden')) { closePicker(false); return; }
  if (!(event.ctrlKey || event.metaKey)) return;
  if (event.key === 'Enter') {
    event.preventDefault();
    if (!$('#preview').classList.contains('hidden')) queuePreview(); else inspectLink();
  }
});

chrome.runtime.onMessage.addListener((message) => {
  if (message?.type === 'load-url') {
    loadFrom(message.url, message.title);
    return;
  }
  if (message?.type === 'job-finished') {
    if (message.job?.state === 'done') showToast(`Saved: ${message.job.filename}`);
    startPolling();
  }
  if (message?.type === 'download-status' && message.state === 'error') showToast(message.error || 'Download failed.', true);
});

// The window was opened (or reused) by the small button on a video: fill it in
// and inspect, so the user can still change the name, folder and quality.
async function loadFrom(url, title) {
  if (!url) return;
  $('#url-input').value = url;
  state.preview = null;
  state.previewExt = '';
  $('#filename-input').value = '';
  $('#filename-input').dataset.auto = '1';
  $('#preview').classList.add('hidden');
  const note = $('#ready-note');
  note.classList.remove('hidden');
  note.innerHTML = title
    ? `Picked up from the page: <b>${escapeHtml(title.slice(0, 90))}</b><br>Check the name and folder below, then press Download.`
    : 'Picked up from the page. Check the name and folder below, then press Download.';
  if (state.server) await inspectLink();
}

// Remember this window's size and position so it reopens the same way.
let boundsTimer = null;
function rememberBounds() {
  clearTimeout(boundsTimer);
  boundsTimer = setTimeout(async () => {
    try {
      const win = await chrome.windows.getCurrent();
      if (win && typeof win.left === 'number') {
        await chrome.storage.local.set({ [BOUNDS_KEY]: { left: win.left, top: win.top, width: win.width, height: win.height } });
      }
    } catch { /* not a real window */ }
  }, 350);
}
addEventListener('resize', rememberBounds);
addEventListener('pagehide', rememberBounds);

// --------------------------------------------------------------------------- //
// boot
// --------------------------------------------------------------------------- //
(async function boot() {
  const stored = await chrome.storage.local.get({
    [PREFS_KEY]: {},
    folder: '',
    notify: true,
  });
  const prefs = stored[PREFS_KEY] || {};
  state.prefs = { ...state.prefs, ...prefs };
  state.prefs.metadata = prefs.metadata === true;
  state.prefs.embed = prefs.embed !== false;
  state.prefs.notify = prefs.notify !== false && stored.notify !== false;
  state.folder = stored.folder || '';

  $('#metadata-checkbox').checked = state.prefs.metadata;
  $('#embed-checkbox').checked = state.prefs.embed;
  $('#notify-checkbox').checked = state.prefs.notify;
  $('#floating-checkbox').checked = state.prefs.floating !== false;
  $('#quality-select').value = state.prefs.quality || 'best';
  $('#height-select').value = String(state.prefs.maxHeight || 0);
  $('#parallel-select').value = String(state.prefs.parallel || 2);
  $('#location-input').value = state.folder;
  renderPathPreview();

  await checkHealth();
  if (!state.folder && state.health?.default_folder) {
    state.folder = state.health.default_folder;
    $('#location-input').value = state.folder;
    renderPathPreview();
  }
  startPolling();

  // Arrive already knowing what we are downloading, then inspect it.
  const params = new URLSearchParams(location.search);
  const startUrl = params.get('url');
  if (startUrl) {
    await loadFrom(startUrl, params.get('title') || '');
  } else if (!$('#url-input').value.trim()) {
    const tab = await rememberedTab();
    if (tab?.url) {
      $('#url-input').value = tab.url;
      if (state.server) inspectLink();
    }
  }
})();
