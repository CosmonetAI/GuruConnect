type Props = { channels: string[]; branches: string[]; onChange: (filters: { query?: string; channel?: string; branch?: string }) => void }
export default function FilterBar({ channels, branches, onChange }: Props) {
  return <div className="filter-compact"><label>Search<input placeholder="Customer, enquiry number or phone" maxLength={120} onChange={event => onChange({ query: event.target.value })} className="responsive-input" /></label>
    <label>Channel<select className="responsive-select" onChange={event => onChange({ channel: event.target.value })} defaultValue=""><option value="">All channels</option>{channels.map(channel => <option key={channel}>{channel}</option>)}</select></label>
    <label>Branch<input className="responsive-input" list="filter-branches" maxLength={120} placeholder="All branches" onChange={event => onChange({ branch: event.target.value })} /><datalist id="filter-branches">{branches.map(branch => <option key={branch}>{branch}</option>)}</datalist></label></div>
}
