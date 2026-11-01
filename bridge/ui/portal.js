/* Copyright (c) 2026 Christians8710 · MIT License. */
const password = document.getElementById('password');
document.getElementById('show-password')?.addEventListener('change', event => {
  password.type = event.target.checked ? 'text' : 'password';
});
document.getElementById('wifi-form')?.addEventListener('submit', event => {
  const form = event.target;
  if (form.dataset.pending === 'true') { event.preventDefault(); return; }
  if (!form.reportValidity()) { event.preventDefault(); return; }
  form.dataset.pending = 'true'; form.setAttribute('aria-busy', 'true');
  form.querySelector('button[type=submit]').disabled = true;
  document.getElementById('submit-status').textContent = 'Verbindung wird angefordert …';
});
// A restored page must allow retrying without losing its form values.
window.addEventListener('pageshow', () => {
  const form = document.getElementById('wifi-form');
  if (!form) return;
  form.dataset.pending = 'false'; form.removeAttribute('aria-busy');
  form.querySelector('button[type=submit]').disabled = false;
  document.getElementById('submit-status').textContent = '';
});
document.getElementById('locate')?.addEventListener('click', async event => {
  const button = event.currentTarget; button.disabled = true;
  const message = document.getElementById('locate-status');
  try { const response = await fetch('/blink'); if (!response.ok) throw new Error();
    message.textContent = 'Blinken angefordert.';
  } catch { message.textContent = 'Board konnte nicht angesprochen werden. Bitte erneut versuchen.'; }
  finally { button.disabled = false; }
});
