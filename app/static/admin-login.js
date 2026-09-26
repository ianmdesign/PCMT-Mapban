const form = document.getElementById('admin-login-form');
if (location.protocol !== 'https:' && !['localhost', '127.0.0.1', '[::1]'].includes(location.hostname)) {
  form.querySelector('button').disabled = true;
  document.getElementById('organization-code').disabled = true;
  document.getElementById('login-error').textContent = 'Open this site using HTTPS before entering the organization code.';
}
form.addEventListener('submit', async event => {
  event.preventDefault();
  const codeInput = document.getElementById('organization-code');
  const error = document.getElementById('login-error');
  const button = form.querySelector('button');
  error.textContent = '';
  button.disabled = true;
  try {
    const response = await fetch('/api/admin/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: codeInput.value.trim() }),
      cache: 'no-store',
    });
    codeInput.value = '';
    if (!response.ok) {
      const result = await response.json();
      throw new Error(result.detail || 'Could not sign in');
    }
    location.reload();
  } catch (cause) {
    error.textContent = cause.message;
    button.disabled = false;
  }
});
