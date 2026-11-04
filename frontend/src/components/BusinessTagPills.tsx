import './BusinessTagPills.css'

type Props = { tags?: string[]; compact?: boolean }

export default function BusinessTagPills({ tags = [], compact = false }: Props) {
  const visible = [...new Set(tags.map(tag => String(tag || '').trim()).filter(Boolean))]
  if (!visible.length) return null
  return <div className={`business-tag-pills${compact ? ' compact' : ''}`} aria-label="Business tags">
    {visible.map(tag => <span className="business-tag-pill" key={tag}>{tag}</span>)}
  </div>
}
