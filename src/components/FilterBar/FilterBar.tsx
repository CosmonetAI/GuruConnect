type Props = {
  channels: string[]
  branches: string[]
  onChange: (filters: { query?: string; channel?: string; branch?: string }) => void
}

export default function FilterBar({ channels, branches, onChange }: Props) {
  return (
    <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center' }} className="filter-compact">
      <input placeholder="Search by customer, ID or phone" onChange={(e) => onChange({ query: e.target.value })} className="responsive-input" />
      <select className="responsive-select" onChange={(e) => onChange({ channel: e.target.value })} defaultValue="">
        <option value="">All channels</option>
        {channels.map((c) => (
          <option key={c} value={c}>{c}</option>
        ))}
      </select>
      <select className="responsive-select" onChange={(e) => onChange({ branch: e.target.value })} defaultValue="">
        <option value="">All branches</option>
        {branches.map((b) => (
          <option key={b} value={b}>{b}</option>
        ))}
      </select>
      <div style={{ marginLeft: 'auto' }}>
        <a href="/enquiries/new"><button className="create-button">New Enquiry</button></a>
      </div>
    </div>
  )
}
