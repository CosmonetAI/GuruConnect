import './BusinessTagPills.css'

type Props = { tags?: string[]; compact?: boolean; maxVisible?: number }

export default function BusinessTagPills({ tags = [], compact = false, maxVisible }: Props) {
  const visible = [...new Set(tags.map(tag => String(tag || '').trim()).filter(Boolean))]
  if (!visible.length) return null
  const limit = Number.isFinite(maxVisible) && Number(maxVisible) > 0 ? Number(maxVisible) : visible.length
  const shown = visible.slice(0, limit)
  const hidden = visible.slice(limit)
  return <div className={`business-tag-pills${compact ? ' compact' : ''}${hidden.length ? ' has-hidden-tags' : ''}`} aria-label="Business tags" title={hidden.length ? visible.join(', ') : undefined}>
    {shown.map(tag => <span className="business-tag-pill" key={tag}>{tag}</span>)}
    {hidden.length > 0 && <>
      <span className="business-tag-pill business-tag-pill-more">+{hidden.length} more</span>
      <span className="business-tag-pills-hover" role="tooltip">{hidden.map(tag => <span className="business-tag-pill" key={tag}>{tag}</span>)}</span>
    </>}
  </div>
}
