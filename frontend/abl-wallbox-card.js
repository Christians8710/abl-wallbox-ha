/* Copyright (c) 2026 Christians8710 · MIT License. No external dependencies. */
const KEYS = {
  sensor: ['state', 'state_description', 'rated_current', 'installation_limit', 'effective_current',
    'duty_cycle', 'current_l1', 'current_l2', 'current_l3', 'estimated_power', 'firmware',
    'hardware', 'system_flags', 'last_fault', ...Array.from({length: 17}, (_, i) => `F${i + 1}`)],
  binary_sensor: ['vehicle_connected', 'charging', 'fault', 'imbalance_detected', 'overtemperature',
    'meter_fault', 'upstream_lost', 'en1_closed', 'en2_closed'],
  number: ['requested_current', 'installation_limit', 'address'],
  select: ['mode', 'imbalance'], switch: ['enabled', 'fallback_enabled', 'start_on_boot'],
  button: ['refresh', 'restart'],
};
const missing = state => !state || ['unknown', 'unavailable'].includes(state.state);
function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

class ABLWallboxCard extends HTMLElement {
  constructor() {
    super(); this.attachShadow({mode: 'open'});
    this._entities = {}; this._registry = null; this._busy = false; this._dirty = new Set();
  }
  setConfig(config) {
    this._config = {...config}; this._entry = config.config_entry_id || '';
    this._entities = {}; this._registry = null; this._dirty.clear();
    this._build(); this._discover();
  }
  set hass(hass) { this._hass = hass; this._discover(); this._update(); }
  getCardSize() { return 20; }
  getGridOptions() { return {columns: 'full', rows: 'auto'}; }
  static getStubConfig() { return {type: 'custom:abl-wallbox-card'}; }
  _build() {
    this.shadowRoot.replaceChildren();
    const css = node('link'); css.rel = 'stylesheet';
    const cssUrl = new URL('./abl-design.css', import.meta.url);
    cssUrl.search = new URL(import.meta.url).search;
    css.href = cssUrl.href;
    this.shadowRoot.append(css);
    const app = node('div', undefined, 'abl-app');
    const shell = node('div', undefined, 'abl-shell'); app.append(shell); this.shadowRoot.append(app);
    const header = node('header', undefined, 'abl-header');
    const logo = node('img', undefined, 'abl-logo'); logo.alt = ''; logo.width = logo.height = 76;
    logo.src = new URL('./icon.png', import.meta.url).href;
    const title = node('div'); title.append(node('div', 'LOKALE STEUERUNG', 'abl-eyebrow'),
      node('h1', this._config.title || 'ABL Wallbox'), node('p', 'Laden, Status und Einstellungen.', 'abl-muted'));
    header.append(logo, title); shell.append(header);
    const nav = node('nav', undefined, 'abl-nav'); nav.ariaLabel = 'Wallbox-Bereiche';
    for (const [id, label] of [['laden', 'Übersicht'], ['einstellungen', 'Einstellungen'], ['diagnose', 'Diagnose']]) {
      const a = node('a', label); a.href = `#${id}`;
      a.addEventListener('click', event => {
        event.preventDefault(); const section = this.shadowRoot.getElementById(id);
        if (section.tagName === 'DETAILS') section.open = true;
        section.scrollIntoView({block: 'start'}); section.focus({preventScroll: true});
      }); nav.append(a);
    }
    shell.append(nav);
    this._chooseArea = node('div', undefined, 'abl-field');
    const chooseLabel = node('label', 'Wallbox auswählen'); chooseLabel.htmlFor = 'choose';
    this._choose = node('select'); this._choose.id = 'choose';
    this._choose.addEventListener('change', () => {
      this._entry = this._choose.value; this._dirty.clear(); this._mapEntities(); this._update();
    }); this._chooseArea.append(chooseLabel, this._choose); shell.append(this._chooseArea);
    this._notice = node('p', 'Wallbox wird geladen …', 'abl-notice'); this._notice.role = 'status';
    this._notice.setAttribute('aria-live', 'polite'); shell.append(this._notice);
    this._feedback = node('p', '', 'abl-notice'); this._feedback.role = 'status';
    this._feedback.setAttribute('aria-live', 'polite'); shell.append(this._feedback);
    const grid = node('main', undefined, 'abl-grid'); shell.append(grid);
    const main = node('div', undefined, 'abl-stack'); const side = node('div', undefined, 'abl-stack');
    grid.append(main, side);
    const charge = node('section', undefined, 'abl-card'); charge.id = 'laden'; charge.tabIndex = -1;
    charge.append(node('h2', 'Laden')); main.append(charge);
    this._badge = node('span', 'Verbindung wird geprüft', 'abl-badge'); charge.append(this._badge);
    this._state = node('p', 'Noch keine Werte', 'abl-status'); charge.append(this._state);
    this._vehicle = node('p', '', 'abl-muted'); charge.append(this._vehicle);
    const metrics = node('div', undefined, 'abl-metrics'); this._metricValues = {};
    for (const [key, label] of [['estimated_power', 'Leistung · geschätzt'], ['effective_current', 'Effektive Freigabe']]) {
      const metric = node('div', undefined, 'abl-metric'); const value = node('strong', '—');
      metric.append(node('span', label), value); metrics.append(metric); this._metricValues[key] = value;
    } charge.append(metrics);
    this._controls = []; this._fields = {};
    this._field(charge, 'number.requested_current', 'Ladestromvorgabe', 'number',
      '0 A pausiert das Laden. Ab 6 A bis zur festen Stromgrenze.');
    this._saveCurrent = this._button(charge, 'Ladestrom übernehmen', () => this._saveNumber('requested_current'), true);
    this._saveCurrent.dataset.entityKey = 'number.requested_current';
    this._enable = this._button(charge, 'Ladefreigabe ändern', () => {
      const state = this._get('switch.enabled');
      return this._call('switch', state?.state === 'on' ? 'turn_off' : 'turn_on', {entity_id: this._entities['switch.enabled']});
    });
    const overview = node('section', undefined, 'abl-card'); overview.append(node('h2', 'Auf einen Blick')); side.append(overview);
    this._values = {};
    this._readings(overview, [
      ['sensor.installation_limit', 'Feste Stromgrenze'], ['sensor.rated_current', 'Nennstrom'],
      ['sensor.current_l1', 'Strom L1'], ['sensor.current_l2', 'Strom L2'], ['sensor.current_l3', 'Strom L3'],
    ]);
    const problems = node('section', undefined, 'abl-card'); side.append(problems); problems.append(node('h2', 'Aktuelle Hinweise'));
    this._problems = node('div'); problems.append(this._problems);
    const settings = node('details', undefined, 'abl-card'); settings.id = 'einstellungen'; settings.tabIndex = -1;
    settings.append(node('summary', 'Einstellungen')); main.append(settings);
    settings.append(node('p', 'Feste Einstellungen lassen sich nur ohne angeschlossenes Fahrzeug ändern.', 'abl-muted'));
    this._field(settings, 'number.installation_limit', 'Feste Stromgrenze (A)', 'number',
      'Nur auf einen für deinen Anschluss freigegebenen Wert setzen.');
    this._button(settings, 'Stromgrenze speichern', () => this._saveNumber('installation_limit')).dataset.entityKey = 'number.installation_limit';
    for (const [key, label] of [['mode', 'Betriebsart'], ['imbalance', 'Schieflasterkennung']]) {
      this._field(settings, `select.${key}`, label, 'select');
      this._button(settings, `${label} speichern`, () => this._saveSetting(`select.${key}`)).dataset.entityKey = `select.${key}`;
    }
    this._field(settings, 'number.address', 'Busadresse', 'number');
    this._button(settings, 'Busadresse speichern', () => this._saveNumber('address')).dataset.entityKey = 'number.address';
    for (const [key, label] of [['fallback_enabled', 'Fallback bei Steuerungsausfall'], ['start_on_boot', 'Ladefreigabe nach Neustart']]) {
      this._field(settings, `switch.${key}`, label, 'checkbox');
      this._button(settings, `${label} speichern`, () => this._saveSetting(`switch.${key}`)).dataset.entityKey = `switch.${key}`;
    }
    const diag = node('details', undefined, 'abl-card abl-full'); diag.id = 'diagnose'; diag.tabIndex = -1;
    diag.append(node('summary', 'Diagnose und Fehlerverlauf')); grid.append(diag);
    this._readings(diag, [['sensor.firmware', 'Firmware'], ['sensor.hardware', 'Hardware'],
      ['sensor.state', 'Betriebszustand'], ['sensor.duty_cycle', 'PWM'], ['sensor.system_flags', 'Systemflags'],
      ['binary_sensor.en1_closed', 'Freigabeeingang EN1'], ['binary_sensor.en2_closed', 'Freigabeeingang EN2']]);
    this._button(diag, 'Werte aktualisieren', () => this._call('button', 'press', {entity_id: this._entities['button.refresh']}));
    diag.append(node('h3', 'Fehlerzähler'));
    this._counters = node('div', undefined, 'abl-table-wrap'); this._counters.tabIndex = 0;
    this._counters.setAttribute('role', 'region'); this._counters.setAttribute('aria-label', 'Fehlerzähler'); diag.append(this._counters);
    diag.append(node('h3', 'In Home Assistant beobachtet'));
    this._history = node('div'); diag.append(this._history);
    diag.append(node('p', 'Zählerstände sind gespeicherte Ereignisse und zeigen keine aktuelle Störung an. Die Zeitpunkte stammen aus Home Assistant.', 'abl-muted'));
    const maintenance = node('details'); maintenance.append(node('summary', 'Wartung')); diag.append(maintenance);
    this._technicalError = node('pre'); this._technicalError.style.whiteSpace = 'pre-wrap';
    const errorDetails = node('details'); errorDetails.append(node('summary', 'Technische Fehlermeldung'), this._technicalError); diag.append(errorDetails);
    this._button(maintenance, 'Bus durchsuchen', async () => {
      const result = await this._hass.callWS({type: 'call_service', domain: 'abl_wallbox', service: 'scan_bus',
        service_data: {config_entry_id: this._entry}, return_response: true});
      const controllers = result?.response?.controllers || [];
      this._scanResult.textContent = controllers.length ? JSON.stringify(controllers, null, 2) : 'Keine weiteren Controller gefunden.';
    });
    this._scanResult = node('pre'); this._scanResult.style.whiteSpace = 'pre-wrap'; maintenance.append(this._scanResult);
    this._button(maintenance, 'Wallbox neu starten', () => this._call('button', 'press', {entity_id: this._entities['button.restart']}),
      false, 'Wallbox wirklich neu starten? Ein Ladevorgang kann unterbrochen werden.');
    this._button(maintenance, 'Fehlerzähler löschen', () => this._call('abl_wallbox', 'clear_fault_counters',
      {config_entry_id: this._entry, confirm: true}), false, 'Alle Fehlerzähler im Controller dauerhaft löschen? Dies kann nicht rückgängig gemacht werden.');
    const footer = node('footer', undefined, 'abl-footer');
    footer.append(node('span', '© 2026 Christians8710'));
    const license = node('a', 'MIT-Lizenz'); license.href = new URL('./LICENSE', import.meta.url).href; footer.append(license); shell.append(footer);
    this._update();
  }
  _field(parent, key, label, type, hint) {
    const field = node('div', undefined, 'abl-field'); const id = `abl-${key}`;
    const labelNode = node('label', label); labelNode.htmlFor = id;
    const input = node(type === 'select' ? 'select' : 'input'); input.id = id;
    if (type !== 'select') input.type = type;
    if (type === 'number') { input.step = '1'; input.inputMode = 'numeric'; }
    input.addEventListener('input', () => { this._dirty.add(key); input.setCustomValidity(''); });
    input.addEventListener('change', () => this._dirty.add(key));
    if (type === 'checkbox') { labelNode.className = 'abl-check'; labelNode.prepend(input); field.append(labelNode); }
    else field.append(labelNode, input);
    if (hint) { const help = node('small', hint); help.id = `${id}-help`; input.setAttribute('aria-describedby', help.id); field.append(help); }
    this._fields[key] = input; parent.append(field);
  }
  _button(parent, text, action, primary = false, confirmation = '') {
    const button = node('button', text, primary ? 'abl-primary' : ''); button.type = 'button';
    button.style.margin = '0 8px 16px 0';
    button.addEventListener('click', async () => {
      if (this._busy || button.disabled || !this._available()) return;
      if (confirmation && !window.confirm(confirmation)) return;
      this._busy = true; this._feedback.textContent = 'Änderung wird ausgeführt …'; this._feedback.className = 'abl-notice'; this._update();
      try { const result = await action(); this._feedback.textContent = result === false ? 'Aktion abgebrochen.' : 'Aktion erfolgreich ausgeführt.'; }
      catch (error) {
        const detail = error.message || String(error);
        this._technicalError.textContent = detail;
        this._feedback.textContent = /^(Bitte |Vorher |Einstellung |Wallbox )/.test(detail) ? detail :
          'Aktion fehlgeschlagen. Prüfe die Verbindung und die Hinweise der Wallbox. Weitere Angaben stehen unter Diagnose.';
        this._feedback.className = 'abl-notice abl-error';
      }
      finally { this._busy = false; this._update(); }
    }); parent.append(button); this._controls.push(button); return button;
  }
  _readings(parent, definitions) {
    const dl = node('dl', undefined, 'abl-list');
    for (const [key, label] of definitions) { const row = node('div'); const value = node('dd', '—');
      row.append(node('dt', label), value); dl.append(row); this._values[key] = value; }
    parent.append(dl);
  }
  async _discover() {
    if (!this._config || !this._hass || this._discovering || this._registry) return;
    this._discovering = true;
    try {
      this._registry = (await this._hass.callWS({type: 'config/entity_registry/list'})).filter(e => e.platform === 'abl_wallbox');
      const entries = [...new Set(this._registry.map(e => e.config_entry_id))];
      this._choose.replaceChildren(node('option', 'Wallbox auswählen'));
      this._choose.firstChild.value = '';
      for (const entry of entries) {
        const entity = this._registry.find(e => e.config_entry_id === entry && e.entity_id.startsWith('sensor.') && e.unique_id === `${entry}_state`);
        const option = node('option', this._hass.states[entity?.entity_id]?.attributes.friendly_name?.replace(/ Betriebszustand$/, '') || `Wallbox ${entries.indexOf(entry) + 1}`);
        option.value = entry; this._choose.append(option);
      }
      if (!this._entry && entries.length === 1) this._entry = entries[0];
      this._mapEntities();
    } catch (error) {
      this._registry = []; this._feedback.className = 'abl-notice abl-error';
      this._feedback.textContent = 'Wallbox konnte nicht zugeordnet werden. Prüfe deine Berechtigung und lade die Ansicht erneut.';
    } finally { this._discovering = false; this._update(); }
  }
  _mapEntities() {
    this._entities = {};
    for (const entity of this._registry || []) {
      if (entity.config_entry_id !== this._entry || entity.disabled_by) continue;
      const domain = entity.entity_id.split('.')[0];
      const key = KEYS[domain]?.find(k => entity.unique_id === `${this._entry}_${k}`);
      if (key) this._entities[`${domain}.${key}`] = entity.entity_id;
    }
  }
  _get(key) { return this._hass?.states[this._entities[key]]; }
  _available() { return !!this._hass?.connected && !missing(this._get('sensor.state')); }
  _format(key) {
    const state = this._get(key); if (missing(state)) return 'Nicht verfügbar';
    if (key.startsWith('binary_sensor.')) return state.state === 'on' ? 'Ja' : 'Nein';
    const unit = state.attributes.unit_of_measurement || '';
    const value = unit && Number.isFinite(Number(state.state)) ? new Intl.NumberFormat('de-DE', {maximumFractionDigits: 2}).format(Number(state.state)) : state.state;
    return `${value}${unit ? ` ${unit}` : ''}`;
  }
  async _call(domain, service, data) {
    if (!this._available() || ('entity_id' in data && !data.entity_id)) throw new Error('Wallbox oder Bedienelement ist nicht verfügbar.');
    await this._hass.callService(domain, service, data);
  }
  async _saveNumber(key) {
    const fieldKey = `number.${key}`; const input = this._fields[fieldKey]; const state = this._get(fieldKey);
    const value = Number(input.value); const {min, max} = state?.attributes || {};
    const valid = input.value.trim() !== '' && Number.isInteger(value) && value >= min && value <= max &&
      (key !== 'requested_current' || value === 0 || value >= 6);
    if (!valid) {
      const message = key === 'requested_current' ? `Bitte 0 oder einen ganzen Wert von 6 bis ${max} A eingeben.` : `Bitte einen ganzen Wert von ${min} bis ${max} eingeben.`;
      input.setCustomValidity(message); throw new Error(message);
    }
    if (key === 'installation_limit' && !window.confirm(`Feste Stromgrenze auf ${value} A setzen? Verwende nur einen für deinen Anschluss freigegebenen Wert.`)) return false;
    if (key !== 'requested_current' && this._get('binary_sensor.vehicle_connected')?.state !== 'off') throw new Error('Vorher das Fahrzeug trennen.');
    await this._call('number', 'set_value', {entity_id: this._entities[fieldKey], value});
    this._dirty.delete(fieldKey);
  }
  async _saveSetting(key) {
    const state = this._get(key); const field = this._fields[key];
    if (missing(state) || this._get('binary_sensor.vehicle_connected')?.state !== 'off') throw new Error('Einstellung nicht verfügbar. Fahrzeug vorher trennen.');
    if (key.startsWith('select.')) {
      if (!state.attributes.options.includes(field.value)) throw new Error('Bitte eine gültige Einstellung auswählen.');
      await this._call('select', 'select_option', {entity_id: this._entities[key], option: field.value});
    } else await this._call('switch', field.checked ? 'turn_on' : 'turn_off', {entity_id: this._entities[key]});
    this._dirty.delete(key);
  }
  _update() {
    if (!this._notice || !this._hass) return;
    const available = this._available();
    this._chooseArea.hidden = (this._choose?.options.length || 0) <= 2 && !!this._entry;
    this._choose.value = this._entry;
    this._choose.disabled = this._busy;
    this._notice.textContent = !this._registry ? 'Wallbox wird geladen …' : !this._entry ?
      (this._registry.length ? 'Wähle eine Wallbox aus.' : 'Noch keine ABL Wallbox eingerichtet. Füge die Integration unter Geräte & Dienste hinzu.') :
      !available ? 'Keine Verbindung zur Wallbox. Die Bedienung ist vorübergehend gesperrt.' : '';
    this._notice.className = `abl-notice${this._entry && !available ? ' abl-error' : ''}`;
    const activeFault = this._get('binary_sensor.fault')?.state === 'on';
    this._badge.textContent = !available ? '○ Nicht verbunden' : activeFault ? '! Störung erkannt' : '● Verbunden';
    this._state.textContent = available ? this._format('sensor.state_description') : 'Wallbox nicht erreichbar';
    this._vehicle.textContent = missing(this._get('binary_sensor.vehicle_connected')) ? 'Fahrzeugstatus nicht verfügbar' :
      this._get('binary_sensor.vehicle_connected').state === 'on' ? 'Fahrzeug angeschlossen' : 'Kein Fahrzeug angeschlossen';
    for (const [key, element] of Object.entries(this._metricValues)) element.textContent = this._format(`sensor.${key}`);
    for (const [key, element] of Object.entries(this._values)) element.textContent = this._format(key);
    for (const button of this._controls) button.disabled = this._busy || !available;
    this._enable.textContent = this._get('switch.enabled')?.state === 'on' ? 'Ladefreigabe sperren' : 'Ladefreigabe erteilen';
    this._enable.disabled ||= missing(this._get('switch.enabled'));
    this._saveCurrent.disabled ||= missing(this._get('number.requested_current'));
    for (const [key, input] of Object.entries(this._fields)) {
      const state = this._get(key); input.disabled = this._busy || !available || missing(state) ||
        (key !== 'number.requested_current' && this._get('binary_sensor.vehicle_connected')?.state !== 'off');
      if (input.tagName === 'SELECT') {
        const options = state?.attributes.options || [];
        if (JSON.stringify(options) !== input.dataset.options) {
          input.replaceChildren(...options.map(option => { const item = node('option', option); item.value = option; return item; }));
          input.dataset.options = JSON.stringify(options);
        }
      }
      if (input.type === 'number') { input.min = state?.attributes.min ?? 0; input.max = state?.attributes.max ?? 0; }
      if (!this._dirty.has(key)) { if (input.type === 'checkbox') input.checked = state?.state === 'on'; else input.value = missing(state) ? '' : state.state; }
    }
    for (const button of this._controls) {
      const field = this._fields[button.dataset.entityKey];
      if (field) button.disabled ||= field.disabled;
    }
    this._problems.replaceChildren();
    const labels = [['fault', 'Störung am Controller'], ['imbalance_detected', 'Schieflast erkannt'],
      ['overtemperature', 'Übertemperatur'], ['meter_fault', 'Strommessung gestört'], ['upstream_lost', 'Externe Steuerung ausgefallen']];
    for (const [key, label] of labels) if (this._get(`binary_sensor.${key}`)?.state === 'on') this._problems.append(node('p', `! ${label}`, 'abl-notice abl-error'));
    if (!this._problems.childElementCount) this._problems.append(node('p', available && !missing(this._get('binary_sensor.fault')) ? 'Keine aktuelle Störung gemeldet.' : 'Störungsstatus nicht verfügbar.', 'abl-muted'));
    const signature = JSON.stringify([this._entry, ...Array.from({length: 17}, (_, i) => this._get(`sensor.F${i + 1}`)?.state), this._get('sensor.last_fault')]);
    if (signature !== this._diagSignature) {
      this._diagSignature = signature; const table = node('table');
      table.append(node('caption', 'Gespeicherte Fehlerereignisse'));
      const head = node('tr'); for (const title of ['Code', 'Beschreibung', 'Anzahl']) head.append(node('th', title));
      const thead = node('thead'); thead.append(head); table.append(thead); const body = node('tbody');
      for (let i = 1; i <= 17; i++) {
        const row = node('tr'); const state = this._get(`sensor.F${i}`);
        row.append(node('td', `F${i}`), node('td', state?.attributes.description || 'Keine Beschreibung'), node('td', this._format(`sensor.F${i}`))); body.append(row);
      } table.append(body); this._counters.replaceChildren(table);
      this._history.replaceChildren(); const history = this._get('sensor.last_fault')?.attributes.history || [];
      if (!history.length) this._history.append(node('p', 'Noch keine Fehlerereignisse in Home Assistant beobachtet.', 'abl-muted'));
      for (const event of [...history].reverse()) {
        const time = new Date(event.time); const stamp = Number.isNaN(time.getTime()) ? 'Zeitpunkt unbekannt' : time.toLocaleString('de-DE');
        this._history.append(node('p', `${stamp} · ${event.code} · ${event.description || ''}${event.type === 'counter_changed' ? ` · Zähler ${event.previous} → ${event.count}` : ''}`));
      }
    }
  }
}
if (!customElements.get('abl-wallbox-card')) customElements.define('abl-wallbox-card', ABLWallboxCard);
window.customCards = window.customCards || [];
if (!window.customCards.some(card => card.type === 'abl-wallbox-card')) window.customCards.push({
  type: 'abl-wallbox-card', name: 'ABL Wallbox', description: 'Lokale Wallbox-Bedienung mit Status, Einstellungen und Diagnose.', preview: true,
});
