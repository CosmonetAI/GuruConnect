import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { supabase, message } from '../services/officeApi'
import { useOffice } from '../contexts/OfficeContext'

export default function ResetPassword() {
  const { session } = useOffice()
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!supabase || busy) return
    setBusy(true); setError('')
    try {
      const password = String(new FormData(event.currentTarget).get('password') ?? '')
      const result = await supabase.auth.updateUser({ password })
      if (result.error) throw result.error
      setDone(true)
    } catch (failure) { setError(message(failure)) } finally { setBusy(false) }
  }
  return <main className="welcome-page"><section className="welcome-card"><h1>Reset password</h1>{error && <p role="alert" className="flow-error">{error}</p>}
    {done ? <p role="status">Password updated. <Link to="/">Open your workspace</Link></p> : session ? <form onSubmit={event => void save(event)}><label>New password<input name="password" type="password" autoComplete="new-password" required minLength={12} maxLength={128} /></label><button disabled={busy} className="button button-primary">Save new password</button></form> : <p>Open the password reset link from your email. <Link to="/">Return to sign-in</Link></p>}</section></main>
}
