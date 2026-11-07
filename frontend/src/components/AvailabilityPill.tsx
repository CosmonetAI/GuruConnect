import { useEffect, useState } from 'react'
import { json, message, request } from '../services/officeApi'
import { useOffice } from '../contexts/OfficeContext'

type Status = 'active' | 'inactive' | 'on_leave' | 'be_right_back'
type Profile = { display_name: string; phone?: string | null; avatar_path?: string | null; availability_status?: Status; leave_end_date?: string | null; final_status?: Status; final_status_until?: string | null; preferences?: Record<string, unknown> }

const labels: Record<Status, string> = { active: 'Active', inactive: 'Inactive', on_leave: 'On leave', be_right_back: 'Be right back' }

export default function AvailabilityPill() {
  const { workspace, readOnly } = useOffice()
  const [profile, setProfile] = useState<Profile | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    void request<Profile>('/profile').then(value => { if (alive) setProfile(value) }).catch(failure => { if (alive) setError(message(failure)) })
    return () => { alive = false }
  }, [])

  if (!workspace) return null
  const workspaceId = workspace.id
  const receptionist = workspace.role === 'receptionist'
  const status = receptionist && (profile?.final_status ?? profile?.availability_status) !== 'active' ? 'inactive' : (profile?.final_status ?? profile?.availability_status ?? 'active')
  const availableStatuses: Status[] = receptionist ? ['active', 'inactive'] : ['active', 'inactive', 'on_leave', 'be_right_back']

  async function changeStatus(next: Status) {
    if (!profile || busy || readOnly || next === status) return
    let leaveEndDate: string | null = null
    if (next === 'on_leave') {
      leaveEndDate = window.prompt('Leave end date (YYYY-MM-DD)', profile.leave_end_date || new Date().toISOString().slice(0, 10))
      if (!leaveEndDate) return
    }
    setBusy(true); setError('')
    try {
      const updated = await request<Profile>('/profile', workspaceId, json('PATCH', {
        display_name: profile.display_name,
        phone: profile.phone || null,
        avatar_path: profile.avatar_path || null,
        availability_status: next,
        leave_end_date: leaveEndDate,
        final_status: next,
        final_status_until: leaveEndDate,
        preferences: profile.preferences || {},
      }))
      setProfile(updated)
      window.dispatchEvent(new Event('guruconnect-profile-updated'))
    } catch (failure) {
      setError(message(failure))
    } finally { setBusy(false) }
  }

  return <label className={`availability-pill availability-pill-${status}`} title={error || 'Change your availability status'}><span className="availability-pill-dot" /><select aria-label="Availability status" value={status} disabled={busy || readOnly || !profile} onChange={event => void changeStatus(event.target.value as Status)}>{availableStatuses.map(value => <option key={value} value={value}>{value === 'on_leave' && (profile?.final_status_until || profile?.leave_end_date) ? `Leave till ${profile.final_status_until || profile.leave_end_date}` : labels[value]}</option>)}</select></label>
}
