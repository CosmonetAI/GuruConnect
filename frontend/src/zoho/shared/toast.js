export function notifyToast(message, tone = 'info', duration = 2600) {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent('zoho-toast', {
    detail: { message, tone, duration },
  }));
}
