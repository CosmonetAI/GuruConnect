type Props = {
  title: string
  value: string | number
  subtitle?: string
}

export default function StatCard({ title, value, subtitle }: Props) {
  return (
    <div className="stat-card">
      <div className="stat-title">{title}</div>
      <div className="stat-value">{value}</div>
      {subtitle && <div className="stat-sub">{subtitle}</div>}
    </div>
  )
}
