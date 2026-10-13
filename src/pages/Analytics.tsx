import StatCard from '../components/StatCard/StatCard'
import { getDashboardDemo } from '../services/demoService'
import { Funnel, FunnelChart, Tooltip, ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid } from 'recharts'

export default function Analytics() {
  const data = getDashboardDemo()
  return (
    <div className="analytics-page">
      <h1 className="page-title">Analytics</h1>

      <div className="stats-grid">
        <StatCard title="Total enquiries" value={data.totals.totalEnquiries} />
        <StatCard title="New (7d)" value={data.totals.newEnquiries} />
        <StatCard title="Pending approvals" value={data.totals.pendingApprovals} />
        <StatCard title="Conversion" value={`${data.totals.conversionRate}%`} />
      </div>

      <div className="charts-row">
        <div className="chart-card">
          <h3>Enquiry funnel</h3>
          <ResponsiveContainer width="100%" height={240}>
            <FunnelChart>
              <Tooltip />
              <Funnel dataKey="value" data={data.funnel} />
            </FunnelChart>
          </ResponsiveContainer>
        </div>

        <div className="chart-card">
          <h3>Recent enquiries (trend)</h3>
          <ResponsiveContainer width="100%" height={240}>
            <LineChart
              data={data.recent.map((r, i) => ({ name: r.date, value: i + 1 }))}
              margin={{ top: 10, right: 10, left: 0, bottom: 0 }}
            >
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="name" />
              <YAxis />
              <Tooltip />
              <Line type="monotone" dataKey="value" stroke="#8884d8" strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div style={{ marginTop: 20 }}>
        <h3>Notes</h3>
        <p>These analytics are demo-only and sourced from the in-memory demo service. Connect a real backend to show production metrics.</p>
      </div>
    </div>
  )
}
