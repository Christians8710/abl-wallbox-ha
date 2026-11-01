/* Copyright (c) 2026 Christians8710. MIT License. Local board UI; no external services. */
const $ = id => document.getElementById(id);
let csrf = '', current = null, busy = false, timer = null, pending = null;
const dirty = new Set();
try { pending = JSON.parse(sessionStorage.getItem('abl_pending_command') || 'null'); } catch {}
const fields = [...document.querySelectorAll('[data-field]')];
const requests = [...document.querySelectorAll('#application form button, [data-command], #enabled')];

function notice(message, error = false) {
  $('feedback').textContent = message;
  $('feedback').className = 'abl-notice' + (error ? ' abl-error' : '');
}
async function api(path, body) {
  let response, value;
  try {
    response = await fetch('/api/v1/' + path, {
      method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: {'Content-Type': 'application/json', ...(csrf ? {'X-ABL-CSRF': csrf} : {})},
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(body === undefined ? 10000 : 80000),
    });
    value = await response.json();
  } catch {
    const error = new Error('Das Board ist gerade nicht erreichbar.'); error.uncertain = body !== undefined; throw error;
  }
  if (!response.ok) {
    if (response.status === 401) showLogin();
    const error = new Error(value.error?.message || 'Die Anfrage ist fehlgeschlagen.');
    error.code = value.error?.code; error.uncertain = ['uncertain', 'unavailable'].includes(error.code);
    error.state = value.state; throw error;
  }
  return value;
}
function showLogin() {
  clearTimeout(timer); $('login').hidden = false; $('application').hidden = true; csrf = '';
}
function format(value, unit = '') {
  if (value === undefined || value === null) return 'Nicht verfügbar';
  if (typeof value === 'boolean') return value ? 'Ja' : 'Nein';
  return (typeof value === 'number' ? new Intl.NumberFormat('de-DE', {maximumFractionDigits: 2}).format(value) : String(value)) + (unit ? ` ${unit}` : '');
}
function element(tag, text, className = '') {
  const item = document.createElement(tag); item.textContent = text; item.className = className; return item;
}
function updateControls() {
  const locked = busy || !current?.available || current.active_command || !!pending;
  for (const button of requests) button.disabled = locked;
  for (const input of fields) {
    input.disabled = locked || (input.closest('[data-operation="configure"]') && current?.data.vehicle_connected !== false);
  }
  for (const form of document.querySelectorAll('[data-operation="configure"]')) form.querySelector('button').disabled = locked || current?.data.vehicle_connected !== false;
  $('logout').disabled = busy;
  $('retry').disabled = busy;
  $('dismiss').disabled = busy;
  $('uncertain').hidden = !pending || busy;
}
function render(snapshot) {
  current = snapshot;
  const data = snapshot.data || {}, available = snapshot.available;
  if (pending && pending.controller_id !== snapshot.controller_id) {
    pending = null; sessionStorage.removeItem('abl_pending_command');
    notice('Das Board wurde gewechselt. Die bisherige Anfrage wurde nicht erneut gesendet.', true);
  }
  $('connection').textContent = available ? (data.fault ? '! Störung erkannt' : '● Verbunden') : '○ Wallbox nicht erreichbar';
  $('connection-message').textContent = available ? '' : 'Keine aktuellen Wallbox-Daten. Bedienung ist vorübergehend gesperrt.';
  $('state-description').textContent = available ? data.state_description : 'Wartet auf Verbindung';
  $('vehicle').textContent = !available ? 'Fahrzeugstatus nicht verfügbar' : data.vehicle_connected ? 'Fahrzeug angeschlossen' : 'Kein Fahrzeug angeschlossen';
  $('enabled').textContent = data.enabled ? 'Ladefreigabe sperren' : 'Ladefreigabe erteilen';
  for (const output of document.querySelectorAll('[data-value]')) output.textContent = format(available ? data[output.dataset.value] : null, output.dataset.unit || '');
  $('observed').textContent = snapshot.observed_at ? new Date(snapshot.observed_at).toLocaleString('de-DE') : 'Noch keine erfolgreiche Abfrage';
  for (const input of fields) {
    const key = input.dataset.field;
    if (!available || dirty.has(key)) continue;
    let value = data[key];
    if (key === 'imbalance') value = !data.imbalance_enabled ? 'off' : [16, 20].includes(data.imbalance_limit) ? String(data.imbalance_limit) : 'unknown';
    if (input.tagName === 'SELECT' && ![...input.options].some(option => option.value === String(value))) {
      const option = element('option', 'Nicht unterstützter Wert'); option.value = String(value); option.disabled = true; input.append(option);
    }
    if (input.type === 'checkbox') input.checked = value === true; else input.value = value ?? '';
  }
  if (available) {
    $('requested-current').max = Math.min(data.rated_current, data.installation_limit);
    $('installation-limit').max = data.rated_current;
    for (const option of $('imbalance').options) if (option.value === '16') option.disabled = !data.new_api;
  }
  $('problems').replaceChildren();
  for (const [key, label] of [['fault', 'Störung am Controller'], ['imbalance_detected', 'Schieflast erkannt'], ['overtemperature', 'Übertemperatur'], ['meter_fault', 'Strommessung gestört'], ['upstream_lost', 'Externe Steuerung ausgefallen']]) {
    if (available && data[key]) $('problems').append(element('p', `! ${label}`, 'abl-notice abl-error'));
  }
  if (!$('problems').childElementCount) $('problems').append(element('p', available ? 'Keine aktuelle Störung gemeldet.' : 'Störungsstatus nicht verfügbar.', 'abl-muted'));
  const signature = JSON.stringify([snapshot.controller_id, available, data.error_counters, snapshot.history]);
  if ($('counters').dataset.signature !== signature) {
    $('counters').dataset.signature = signature; $('counters').replaceChildren();
    for (let i = 1; i <= 17; i++) {
      const code = `F${i}`, row = document.createElement('tr');
      row.append(element('td', code), element('td', snapshot.fault_descriptions?.[code] || code), element('td', format(available ? data.error_counters?.[code] : null))); $('counters').append(row);
    }
    $('history').replaceChildren();
    for (const item of [...(snapshot.history || [])].reverse()) {
      $('history').append(element('p', `${new Date(item.time).toLocaleString('de-DE')} · ${item.code} · ${item.description}${item.type === 'counter_changed' ? ` · Zähler ${item.previous} → ${item.count}` : ''}`));
    }
    if (!$('history').childElementCount) $('history').append(element('p', 'Noch keine Fehlerereignisse auf dem Board beobachtet.', 'abl-muted'));
  }
  updateControls();
}
async function poll() {
  clearTimeout(timer);
  try { render(await api('state')); }
  catch (error) {
    if ($('application').hidden) return;
    if (current) render({...current, available: false});
    $('connection-message').textContent = 'Board nicht erreichbar. Angezeigte Zeitpunkte stammen aus der letzten erfolgreichen Abfrage.';
  }
  if (!$('application').hidden) timer = setTimeout(poll, 5000);
}
function requestId() { return [...crypto.getRandomValues(new Uint8Array(16))].map(value => value.toString(16).padStart(2, '0')).join(''); }
async function command(payload, {retry = false, field = null} = {}) {
  if (busy || (!retry && (!current?.available || pending))) return;
  const request = retry ? pending.payload : {request_id: requestId(), address: current.address, ...payload};
  pending = {controller_id: current.controller_id, payload: request, field: retry ? pending.field : field};
  sessionStorage.setItem('abl_pending_command', JSON.stringify(pending));
  busy = true; notice('Aktion wird ausgeführt …'); updateControls();
  try {
    const result = await api('command', request);
    if (pending.field) dirty.delete(pending.field);
    pending = null; sessionStorage.removeItem('abl_pending_command'); render(result);
    if (result.result?.controllers) $('scan-result').textContent = result.result.controllers.length ? JSON.stringify(result.result.controllers, null, 2) : 'Keine Controller gefunden.';
    notice(request.operation === 'restart' ? 'Neustart der Wallbox angefordert.' : 'Aktion erfolgreich ausgeführt.');
    $('technical-error').textContent = '';
  } catch (error) {
    if (error.state) render(error.state);
    $('technical-error').textContent = error.message;
    notice(error.code === 'controller_error' ? 'Aktion fehlgeschlagen. Prüfe die Hinweise der Wallbox und die Angaben unter Diagnose.' : error.message, true);
    if (!error.uncertain) { pending = null; sessionStorage.removeItem('abl_pending_command'); }
  } finally { busy = false; updateControls(); }
}
for (const input of fields) input.addEventListener('input', () => { dirty.add(input.dataset.field); input.setCustomValidity(''); });
for (const form of document.querySelectorAll('[data-operation]')) form.addEventListener('submit', event => {
  event.preventDefault(); if (busy || pending) return;
  const input = form.elements.namedItem('value'), key = input.dataset.field;
  if (input.disabled) return;
  const value = input.type === 'checkbox' ? input.checked : input.type === 'number' ? Number(input.value) : input.value;
  if (input.type === 'number' && (!Number.isInteger(value) || input.value.trim() === '' || (key === 'requested_current' && value !== 0 && value < 6))) input.setCustomValidity('Bitte einen gültigen ganzen Wert eingeben; Ladestrom 0 oder mindestens 6 A.');
  if (!form.reportValidity()) return;
  if (key === 'installation_limit' && !confirm(`Feste Stromgrenze auf ${value} A setzen? Verwende nur einen für deinen Anschluss freigegebenen Wert.`)) return;
  command(form.dataset.operation === 'configure' ? {operation: 'configure', settings: {[form.dataset.setting]: value}} : {operation: 'set_current', value}, {field: key});
});
$('enabled').addEventListener('click', () => command({operation: 'set_enabled', value: !current.data.enabled}));
for (const button of document.querySelectorAll('[data-command]')) button.addEventListener('click', () => {
  const operation = button.dataset.command;
  if (operation === 'restart' && !confirm('Wallbox wirklich neu starten? Ein Ladevorgang kann unterbrochen werden.')) return;
  if (operation === 'clear_fault_counters' && !confirm('Alle Fehlerzähler im Controller dauerhaft löschen? Dies kann nicht rückgängig gemacht werden.')) return;
  command({operation, ...(['restart', 'clear_fault_counters'].includes(operation) ? {confirm: true} : {})});
});
$('retry').addEventListener('click', () => command(null, {retry: true}));
$('dismiss').addEventListener('click', () => { pending = null; sessionStorage.removeItem('abl_pending_command'); notice('Vorgang geschlossen.'); updateControls(); });
$('login-form').addEventListener('submit', async event => {
  event.preventDefault(); const button = event.target.querySelector('button'); if (button.disabled) return;
  button.disabled = true; $('login-message').textContent = 'Anmeldung wird geprüft …';
  try { const result = await api('session', {token: $('api-key').value}); csrf = result.csrf;
    $('api-key').value = ''; $('login-message').textContent = ''; $('login').hidden = true; $('application').hidden = false; await poll();
  } catch (error) { $('login-message').textContent = error.message; }
  finally { button.disabled = false; }
});
$('logout').addEventListener('click', async () => { try { await api('logout', {}); showLogin(); } catch (error) { notice(error.message, true); } });
for (const link of document.querySelectorAll('a[href^="#"]')) link.addEventListener('click', () => {
  const target = document.querySelector(link.getAttribute('href')); if (target?.tagName === 'DETAILS') target.open = true;
});
$('api-host').textContent = location.hostname; $('api-port').textContent = location.port || '80';
(async () => {
  try { const session = await api('session'); csrf = session.csrf; $('application').hidden = false; await poll(); }
  catch { showLogin(); }
})();
