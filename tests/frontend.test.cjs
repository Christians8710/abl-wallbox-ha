const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../frontend/abl-wallbox-card.js'), 'utf8')
  .replaceAll('import.meta.url', '"http://localhost/frontend/abl-wallbox-card.js"');
const classes = new Map();
vm.runInNewContext(source, {
  HTMLElement: class { attachShadow() {} }, document: {},
  customElements: {get: key => classes.get(key), define: (key, value) => classes.set(key, value)},
  window: {confirm: () => true}, URL, Intl, console,
});
function fixture() {
  const card = new (classes.get('abl-wallbox-card'))();
  card._entry = 'entry'; card._entities = {'number.requested_current': 'number.renamed_target',
    'number.installation_limit': 'number.limit', 'select.mode': 'select.mode', 'binary_sensor.vehicle_connected': 'binary_sensor.vehicle'};
  const calls = [];
  card._hass = {connected: true, states: {
    'sensor.state': {state: 'A1'}, 'number.renamed_target': {state: '16', attributes: {min: 0, max: 16}},
    'number.limit': {state: '16', attributes: {min: 6, max: 32}},
    'binary_sensor.vehicle': {state: 'off'}, 'select.mode': {state: 'Standalone', attributes: {options: ['Standalone', 'RFID / lokale Freigabe']}},
  }, callService: async (...args) => calls.push(args)};
  card._entities['sensor.state'] = 'sensor.state';
  const field = value => ({value, setCustomValidity(message) { this.validationMessage = message; }, reportValidity() {}});
  card._fields = {'number.requested_current': field('16'), 'number.installation_limit': field('16'), 'select.mode': field('Standalone')};
  return {card, calls};
}
test('renamed entities map by unique identity and domain, disabled entities stay excluded', () => {
  const {card} = fixture(); card._registry = [
    {config_entry_id: 'entry', entity_id: 'number.my_target', unique_id: 'entry_requested_current'},
    {config_entry_id: 'entry', entity_id: 'sensor.my_limit', unique_id: 'entry_installation_limit'},
    {config_entry_id: 'entry', entity_id: 'number.my_limit', unique_id: 'entry_installation_limit'},
    {config_entry_id: 'entry', entity_id: 'sensor.flags', unique_id: 'entry_system_flags', disabled_by: 'integration'},
    {config_entry_id: 'other', entity_id: 'number.other', unique_id: 'other_requested_current'},
  ]; card._mapEntities();
  assert.equal(card._entities['number.requested_current'], 'number.my_target');
  assert.equal(card._entities['sensor.installation_limit'], 'sensor.my_limit');
  assert.equal(card._entities['number.installation_limit'], 'number.my_limit');
  assert.equal(card._entities['sensor.system_flags'], undefined);
});
test('current 0 and 6..limit reach only the existing HA number service', async () => {
  for (const value of [0, 6, 16]) {
    const {card, calls} = fixture(); card._fields['number.requested_current'].value = String(value);
    await card._saveNumber('requested_current');
    assert.deepEqual(Array.from(calls[0]).slice(0, 2), ['number', 'set_value']);
    assert.equal(calls[0][2].entity_id, 'number.renamed_target'); assert.equal(calls[0][2].value, value);
  }
});
test('invalid, fractional and above-limit currents never write and retain the draft', async () => {
  for (const value of ['', '1', '5', '6.5', '17', '-1', 'hello']) {
    const {card, calls} = fixture(); card._fields['number.requested_current'].value = value;
    await assert.rejects(card._saveNumber('requested_current')); assert.equal(calls.length, 0);
    assert.equal(card._fields['number.requested_current'].value, value);
  }
});
test('disconnected and unavailable wallbox cannot write', async () => {
  for (const scenario of ['disconnected', 'unavailable']) {
    const {card, calls} = fixture();
    if (scenario === 'disconnected') card._hass.connected = false;
    else card._hass.states['sensor.state'].state = 'unavailable';
    await assert.rejects(card._saveNumber('requested_current')); assert.equal(calls.length, 0);
  }
});
test('fixed configuration cannot write while a vehicle is connected or status unknown', async () => {
  for (const state of ['on', 'unavailable']) {
    const {card, calls} = fixture(); card._hass.states['binary_sensor.vehicle'].state = state;
    await assert.rejects(card._saveNumber('installation_limit'));
    await assert.rejects(card._saveSetting('select.mode')); assert.equal(calls.length, 0);
  }
});
test('select rejects an option absent from the controller and preserves the value', async () => {
  const {card, calls} = fixture(); card._fields['select.mode'].value = 'invented';
  await assert.rejects(card._saveSetting('select.mode')); assert.equal(calls.length, 0);
  assert.equal(card._fields['select.mode'].value, 'invented');
});
test('failed service retains a dirty numeric input', async () => {
  const {card} = fixture(); card._dirty.add('number.requested_current');
  card._hass.callService = async () => {throw new Error('Simulated transport failure');};
  await assert.rejects(card._saveNumber('requested_current'));
  assert.equal(card._dirty.has('number.requested_current'), true);
});
test('unknown readings are not converted to zero; values use German formatting', () => {
  const {card} = fixture(); assert.equal(card._format('sensor.current_l1'), 'Nicht verfügbar');
  card._entities['sensor.current_l1'] = 'sensor.l1'; card._hass.states['sensor.l1'] = {state: '6.25', attributes: {unit_of_measurement: 'A'}};
  assert.equal(card._format('sensor.current_l1'), '6,25 A');
});
