const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '..');

function boardFeedback() {
  const source = fs.readFileSync(path.join(root, 'controller/web/app.js'), 'utf8');
  const context = vm.createContext({
    document: {querySelectorAll: () => [], getElementById: () => ({addEventListener() {}})},
    sessionStorage: {getItem: () => null}, location: {hostname: 'localhost', port: '8080'},
  });
  // Omit the automatic network bootstrap; exercise the actual UI logic without a wallbox.
  vm.runInContext(source.slice(0, source.lastIndexOf('(async () => {')), context);
  return vm.runInContext('commandMessage', context);
}

test('board confirms read-back values and distinguishes unavailable or differing values', () => {
  const message = boardFeedback();
  const request = {operation: 'set_current', value: 12};
  assert.equal(message(request, {available: true, data: {requested_current: 12}}), 'Ladestromvorgabe auf 12 A gesetzt.');
  for (const result of [{available: false}, {available: true, data: {requested_current: 6}}]) {
    assert.match(message(request, result), /erneut abgefragt/);
    assert.doesNotMatch(message(request, result), /12 A gesetzt/);
  }
  assert.match(message({operation: 'restart'}, {}), /angefordert/);
  assert.match(message({operation: 'scan'}, {result: {controllers: [{address: 1}]}}), /1 Controller gefunden/);
});

function tokens(block) {
  return Object.fromEntries([...block.matchAll(/(--abl-[a-z-]+):\s*(#[a-f0-9]{6})/g)].map(match => [match[1], match[2]]));
}
function luminance(hex) {
  const rgb = hex.slice(1).match(/../g).map(value => parseInt(value, 16) / 255)
    .map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4);
  return rgb[0] * .2126 + rgb[1] * .7152 + rgb[2] * .0722;
}
function contrast(a, b) {
  const values = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (values[0] + .05) / (values[1] + .05);
}

test('both palettes keep text and control borders accessible and board styles synchronized', () => {
  const css = fs.readFileSync(path.join(root, 'frontend/abl-design.css'), 'utf8');
  assert.equal(fs.readFileSync(path.join(root, 'bridge/ui/abl-design.css'), 'utf8'), css);
  const dark = tokens(css.slice(0, css.indexOf('/* HA')));
  const light = {...dark, ...tokens(css.match(/:host\(\[data-abl-theme="light"\]\)\s*\{([^}]+)/)[1])};
  for (const palette of [dark, light]) {
    for (const background of ['--abl-card', '--abl-bg']) {
      assert.ok(contrast(palette['--abl-control-border'], palette[background]) >= 3, `Control border against ${background}`);
      for (const text of ['--abl-text', '--abl-muted', '--abl-accent']) {
        assert.ok(contrast(palette[text], palette[background]) >= 4.5, `${text} against ${background}`);
      }
    }
    assert.ok(contrast(palette['--abl-on-primary'], palette['--abl-gold']) >= 4.5);
    assert.ok(contrast(palette['--abl-text'], palette['--abl-secondary']) >= 4.5);
  }
});
