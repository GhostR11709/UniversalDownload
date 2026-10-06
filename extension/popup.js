const API = 'http://127.0.0.1:8756';
const $ = (selector) => document.querySelector(selector);
const state = { preview: null, server: false, poll: null };

async function request(path, options = {}) {
  const response = await fetch(`${API}${path}`, { ...options, headers: { 'Content-Type': 'application/json', ...(options.headers || {}) } });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.ok === false) throw new Error(data.hint || data.error || `Request failed (${response.status})`);
  return data;
}
function showToast(message, isError = false) { const toast = $('#toast'); toast.textContent = message; toast.className = `toast show${isError ? ' error' : ''}`; clearTimeout(showToast.timer); showToast.timer = setTimeout(() => { toast.className = 'toast'; }, 3200); }
function formatSize(bytes) { if (!bytes) return ''; const units = ['B', 'KB', 'MB', 'GB']; let value = bytes; let index = 0; while (value >= 1024 && index < units.length - 1) { value /= 1024; index += 1; } return `${value.toFixed(index ? 1 : 0)} ${units[index]}`; }
function setServerStatus(online, label) { state.server = online; $('#status-dot').className = `status-dot ${online ? 'online' : 'offline'}`; $('#engine-status').textContent = label || (online ? 'Local engine ready' : 'Engine offline'); }
async function checkHealth() { try { const health = await request('/api/health'); setServerStatus(true, `Engine ready · ${health.active || 0} active`); } catch { setServerStatus(false, 'Start the local engine with setup.bat'); } }

function renderPreview(media) {
  state.preview = media; $('#preview').classList.remove('hidden');
  $('#preview').innerHTML = `<div class="preview-top">${media.thumbnail ? `<img class="preview-thumb" src="${escapeAttribute(media.thumbnail)}" alt="">` : '<div class="preview-thumb"></div>'}<div class="preview-copy"><div class="eyebrow accent">${escapeHtml(media.emoji || '✦')} ${escapeHtml(media.platform || 'WEB')}</div><div class="preview-title">${escapeHtml(media.title || 'Untitled media')}</div><div class="preview-meta">${escapeHtml(media.uploader || 'Unknown creator')}${media.duration_text ? ` · ${escapeHtml(media.duration_text)}` : ''}</div></div></div><div class="preview-footer"><select id="quality-select" aria-label="Download quality"><option value="best">Best available</option><option value="2160">2160p · 4K</option><option value="1440">1440p</option><option value="1080">1080p · Full HD</option><option value="720">720p · HD</option><option value="480">480p</option><option value="audio">Audio only</option></select><button class="primary-button" id="queue-button" type="button"><span>Queue download</span><b>↓</b></button></div>`;
  $('#queue-button').addEventListener('click', queuePreview);
}
async function inspectLink(event) { event?.preventDefault(); const url = $('#url-input').value.trim(); if (!url) return; if (!state.server) { showToast('Start the local engine first.', true); return; } const button = $('#inspect-button'); button.disabled = true; button.querySelector('span').textContent = 'Inspecting…'; try { const result = await request('/api/detect', { method: 'POST', body: JSON.stringify({ url }) }); renderPreview(result.media); } catch (error) { $('#preview').classList.add('hidden'); showToast(error.message, true); } finally { button.disabled = false; button.querySelector('span').textContent = 'Inspect link'; } }
async function queuePreview() { if (!state.preview) return; const quality = $('#quality-select').value; try { await request('/api/downloads', { method: 'POST', body: JSON.stringify({ url: state.preview.url, title: state.preview.title, quality }) }); showToast('Queued. The engine is on it.'); $('#preview').classList.add('hidden'); startPolling(); } catch (error) { showToast(error.message, true); } }
async function saveJob(job) { try { await chrome.downloads.download({ url: `${API}/files/${encodeURIComponent(job.id)}`, filename: job.filename || undefined, saveAs: true }); showToast('Chrome is saving your file.'); } catch (error) { showToast(error.message, true); } }
async function cancelJob(id) { try { await request(`/api/jobs/${encodeURIComponent(id)}/cancel`, { method: 'POST', body: '{}' }); startPolling(); } catch (error) { showToast(error.message, true); } }
async function revealJob(id) { try { await request('/api/reveal', { method: 'POST', body: JSON.stringify({ id }) }); } catch (error) { showToast(error.message, true); } }

function renderJobs(jobs) {
  const queue = $('#queue'); const active = jobs.some((job) => ['queued', 'running'].includes(job.state)); $('#clear-history').classList.toggle('hidden', !jobs.some((job) => ['done', 'error', 'cancelled'].includes(job.state)));
  if (!jobs.length) { queue.innerHTML = '<div class="empty-state" id="empty-state"><div class="empty-icon">✦</div><p>No downloads yet.</p><span>Paste a link above and let the local engine work.</span></div>'; return; }
  queue.innerHTML = jobs.slice(0, 8).map((job) => { const percent = Math.max(0, Math.min(100, Number(job.progress?.percent || 0))); const status = job.state === 'done' ? 'Ready' : job.state === 'error' ? 'Failed' : job.state === 'cancelled' ? 'Cancelled' : (job.progress?.stage || `${percent.toFixed(0)}%`); const actions = job.state === 'done' ? `<button class="save" data-save="${job.id}">Save file</button><button data-reveal="${job.id}">Show folder</button>` : ['queued', 'running'].includes(job.state) ? `<button data-cancel="${job.id}">Cancel</button>` : ''; return `<article class="job"><div class="queue-head"><div class="job-icon">${escapeHtml(job.emoji || '✦')}</div><div class="job-copy"><div class="job-title">${escapeHtml(job.title || job.url || 'Untitled download')}</div><div class="job-meta">${escapeHtml(job.platform || 'Web')}${job.size ? ` · ${formatSize(job.size)}` : ''}</div></div><span class="job-status ${job.state}">${escapeHtml(status)}</span></div><div class="progress-track"><div class="progress-bar" style="width:${job.state === 'done' ? 100 : percent}%"></div></div><div class="job-actions">${actions}</div></article>`; }).join('');
  queue.querySelectorAll('[data-save]').forEach((button) => button.addEventListener('click', () => saveJob(jobs.find((job) => job.id === button.dataset.save)))); queue.querySelectorAll('[data-reveal]').forEach((button) => button.addEventListener('click', () => revealJob(button.dataset.reveal))); queue.querySelectorAll('[data-cancel]').forEach((button) => button.addEventListener('click', () => cancelJob(button.dataset.cancel))); if (active) state.poll = setTimeout(loadJobs, 1100); else clearTimeout(state.poll);
}
async function loadJobs() { try { renderJobs(await request('/api/jobs')); } catch { /* health status handles offline state */ } }
function startPolling() { clearTimeout(state.poll); loadJobs(); }
function escapeHtml(value) { return String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char])); }
function escapeAttribute(value) { return escapeHtml(value).replace(/`/g, '&#96;'); }

$('#download-form').addEventListener('submit', inspectLink);
$('#paste-button').addEventListener('click', async () => { try { $('#url-input').value = await navigator.clipboard.readText(); $('#url-input').focus(); } catch { showToast('Chrome blocked clipboard access. Paste with Ctrl+V.', true); } });
$('#current-tab').addEventListener('click', async () => { const [tab] = await chrome.tabs.query({ active: true, currentWindow: true }); if (tab?.url) { $('#url-input').value = tab.url; inspectLink(); } else showToast('This tab has no readable URL.', true); });
$('#open-dashboard').addEventListener('click', () => chrome.tabs.create({ url: API })); $('#open-options').addEventListener('click', () => chrome.runtime.openOptionsPage()); $('#clear-history').addEventListener('click', async () => { try { await request('/api/jobs', { method: 'DELETE' }); startPolling(); } catch (error) { showToast(error.message, true); } });
checkHealth(); startPolling();
