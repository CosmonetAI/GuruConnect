import React, { useEffect, useState } from 'react';

let nextToastId = 1;

export default function ToastHost() {
  const [toasts, setToasts] = useState([]);

  useEffect(() => {
    const onToast = event => {
      const detail = event.detail || {};
      const id = nextToastId++;
      const toast = {
        id,
        message: detail.message || 'Action completed.',
        tone: detail.tone || 'info',
      };
      setToasts(current => [...current.slice(-2), toast]);
      window.setTimeout(() => setToasts(current => current.filter(item => item.id !== id)), detail.duration || 2600);
    };
    window.addEventListener('zoho-toast', onToast);
    return () => window.removeEventListener('zoho-toast', onToast);
  }, []);

  if (!toasts.length) return null;
  return <div className="zoho-toast-host" role="status" aria-live="polite">
    {toasts.map(toast => <div className={`zoho-toast zoho-toast-${toast.tone}`} key={toast.id}>
      <span aria-hidden="true">{toast.tone === 'success' ? '✓' : toast.tone === 'error' ? '!' : toast.tone === 'loading' ? '…' : 'i'}</span>
      <span>{toast.message}</span>
    </div>)}
  </div>;
}
