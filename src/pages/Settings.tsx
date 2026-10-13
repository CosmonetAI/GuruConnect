import { useState } from 'react'

export default function Settings() {
  const [companyName, setCompanyName] = useState(() => localStorage.getItem('demo:companyName') || 'Guru Group')
  const [enableRecording, setEnableRecording] = useState(() => localStorage.getItem('demo:enableRecording') === 'true')
  const [defaultBranch, setDefaultBranch] = useState(() => localStorage.getItem('demo:defaultBranch') || '')

  function save() {
    localStorage.setItem('demo:companyName', companyName)
    localStorage.setItem('demo:enableRecording', String(enableRecording))
    localStorage.setItem('demo:defaultBranch', defaultBranch)
    alert('Settings saved (demo-only)')
  }

  return (
    <div className="settings-page">
      <h1 className="page-title">Settings</h1>

      <section className="card">
        <h3>General</h3>
        <label style={{ display: 'block', marginBottom: 8 }}>
          Company name
          <input style={{ display: 'block', marginTop: 6, padding: 8, width: '100%' }} value={companyName} onChange={(e) => setCompanyName(e.target.value)} />
        </label>

        <label style={{ display: 'block', marginBottom: 8 }}>
          Default branch
          <input style={{ display: 'block', marginTop: 6, padding: 8, width: '100%' }} value={defaultBranch} onChange={(e) => setDefaultBranch(e.target.value)} />
        </label>

        <button onClick={save} style={{ marginTop: 10, padding: '8px 12px' }}>Save</button>
      </section>

      <section className="card" style={{ marginTop: 16 }}>
        <h3>Recording</h3>
        <label style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <input type="checkbox" checked={enableRecording} onChange={(e) => setEnableRecording(e.target.checked)} />
          Enable browser recording (demo only)
        </label>
        <p style={{ marginTop: 8, color: '#666' }}>Note: This toggle is local to the browser and does not change server-side configuration in the demo app.</p>
      </section>

    </div>
  )
}
