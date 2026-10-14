import { lazy, Suspense, type FC, type ReactNode } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import AppLayout from '../layouts/AppLayout'
import '../styles/forms.css'
const Dashboard = lazy(() => import('../pages/Dashboard/Dashboard'))
const EnquiriesList = lazy(() => import('../pages/Enquiries/EnquiriesList'))
const NewEnquiry = lazy(() => import('../pages/Enquiries/NewEnquiry'))
const EnquiryDetail = lazy(() => import('../pages/Enquiries/EnquiryDetail'))
const Analytics = lazy(() => import('../pages/Analytics'))
const Settings = lazy(() => import('../pages/Settings'))
import Welcome from '../pages/Welcome'
import ResetPassword from '../pages/ResetPassword'
const Zoho = lazy(() => import('../pages/Zoho'))
const Recordings = lazy(() => import('../pages/Recordings'))
const WalkinsOverview = lazy(() => import('../pages/Walkins/WalkinsOverview'))
const WalkinRegistration = lazy(() => import('../pages/Walkins/WalkinRegistration'))
const WalkinDetail = lazy(() => import('../pages/Walkins/WalkinDetail'))
const MyWalkins = lazy(() => import('../pages/Walkins/MyWalkins'))
const WalkinHistory = lazy(() => import('../pages/Walkins/WalkinHistory'))
import { useOffice } from '../contexts/OfficeContext'

const ProtectedRoute: FC<{ children: ReactNode }> = ({ children }) => {
  const { configured, user, workspace, loading, workspacesLoading } = useOffice()
  if (!configured || loading || !user || workspacesLoading || !workspace) return <Welcome />
  return <>{children}</>
}

export default function App() {
  return (
    <Suspense fallback={<p role="status" className="flow-panel">Opening screen…</p>}><Routes>
      <Route path="/reset" element={<ResetPassword />} />
      <Route path="/auth/callback" element={<Navigate to="/" replace />} />
      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route
        path="/"
        element={
          <ProtectedRoute>
            <ScopedLayout />
          </ProtectedRoute>
        }
      >
        <Route path="dashboard" element={<Dashboard />} />
        <Route path="enquiries" element={<EnquiriesList />} />
        <Route path="walkins" element={<WalkinsOverview />} />
        <Route path="walkins/new" element={<WalkinRegistration />} />
        <Route path="walkins/mine" element={<MyWalkins />} />
        <Route path="walkins/history" element={<WalkinHistory />} />
        <Route path="walkins/:id" element={<WalkinDetail />} />
        <Route path="enquiries/new" element={<NewEnquiry />} />
        <Route path="enquiries/:id" element={<EnquiryDetail />} />
        <Route path="analytics" element={<Analytics />} />
        <Route path="integrations" element={<Zoho />} />
        <Route path="zoho" element={<Zoho />} />
        <Route path="recordings" element={<Recordings />} />
        <Route path="recordings/:id" element={<Recordings />} />
        <Route path="settings" element={<Settings />} />
      </Route>
      <Route path="*" element={<div style={{padding:20}}>Page not found. Open Overview or Enquiries from the navigation.</div>} />
    </Routes></Suspense>
  )
}

function ScopedLayout() {
  const { user, workspace } = useOffice()
  // Tear down pending page state when the security context changes. Async hooks
  // still check their identity tickets before accepting late responses.
  return <AppLayout key={`${user?.id}:${workspace?.id}`} />
}
