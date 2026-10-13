import StatCard from '../../components/StatCard/StatCard'
import { getDashboardDemo } from '../../services/demoService'
import { Funnel, FunnelChart, Tooltip, ResponsiveContainer } from 'recharts'

export default function Dashboard() {
  const data = getDashboardDemo()
  return (
    <div className="dashboard-page">
      <h1 className="page-title">Dashboard</h1>
      <div className="stats-grid">
        <StatCard title="Total enquiries" value={data.totals.totalEnquiries} />
        <StatCard title="New enquiries awaiting action" value={data.totals.newEnquiries} />
        <StatCard title="Quotations pending approval" value={data.totals.pendingApprovals} />
        <StatCard title="Quotations sent" value={data.totals.sentQuotations} />
        <StatCard title="Overdue follow-ups" value={data.totals.overdueFollowups} />
        <StatCard title="Conversion rate" value={`${data.totals.conversionRate}%`} />
      </div>

      <div className="charts-row">
        <div className="chart-card">
          <h3>Enquiry funnel</h3>
          <ResponsiveContainer width="100%" height={250}>
            <FunnelChart>
              <Tooltip />
              <Funnel dataKey="value" data={data.funnel} />
            </FunnelChart>
          </ResponsiveContainer>
        </div>
        <div className="recent-card">
          <h3>Recent enquiries</h3>
          <table className="recent-table">
            <thead>
              <tr><th>ID</th><th>Customer</th><th>Channel</th><th>Date</th></tr>
            </thead>
            <tbody>
              {data.recent.map((r) => (
                <tr key={r.id}><td>{r.id}</td><td>{r.customer}</td><td>{r.channel}</td><td>{r.date}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
