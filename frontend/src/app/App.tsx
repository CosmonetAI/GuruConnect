import { lazy, Suspense, type FC, type ReactNode } from 'react'
import { Routes, Route, Navigate, useLocation } from 'react-router-dom'
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
import TicketTracking from '../pages/TicketTracking'
const Zoho = lazy(() => import('../pages/Zoho'))
const Recordings = lazy(() => import('../pages/Recordings'))
const WalkinsOverview = lazy(() => import('../pages/Walkins/WalkinsOverview'))
const WalkinRegistration = lazy(() => import('../pages/Walkins/WalkinRegistration'))
const WalkinDetail = lazy(() => import('../pages/Walkins/WalkinDetail'))
const MyWalkins = lazy(() => import('../pages/Walkins/MyWalkins'))
const WalkinHistory = lazy(() => import('../pages/Walkins/WalkinHistory'))
const AIChats = lazy(() => import('../pages/VoiceSessions'))
const Products = lazy(() => import('../pages/GGPages').then(module => ({ default: module.Products })))
const Contacts = lazy(() => import('../pages/GGPages').then(module => ({ default: module.Contacts })))
const Quotes = lazy(() => import('../pages/GGPages').then(module => ({ default: module.Quotes })))
const SalesOrders = lazy(() => import('../pages/GGPages').then(module => ({ default: module.SalesOrders })))
const PurchaseOrders = lazy(() => import('../pages/GGPages').then(module => ({ default: module.PurchaseOrders })))
const Invoices = lazy(() => import('../pages/GGPages').then(module => ({ default: module.Invoices })))
const Pipelines = lazy(() => import('../pages/Pipelines'))
import { useOffice } from '../contexts/OfficeContext'

const ProtectedRoute: FC<{ children: ReactNode }> = ({ children }) => {
  const { configured, user, workspace, loading, workspacesLoading } = useOffice()
  if (!configured || loading || !user || workspacesLoading || !workspace) return <Welcome />
  return <>{children}</>
}
const RoleRoute: FC<{ roles: string[]; children: ReactNode }> = ({ roles, children }) => {
  const { workspace } = useOffice()
  return workspace && roles.includes(workspace.role) ? <>{children}</> : <Navigate to="/dashboard" replace />
}

export default function App() {
  return (
    <Suspense fallback={<p role="status" className="flow-panel">Opening screen…</p>}><Routes>
      <Route path="/reset" element={<ResetPassword />} />
      <Route path="/auth/callback" element={<Navigate to="/" replace />} />
      <Route path="/ticket-tracking" element={<TicketTracking />} />
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
        <Route path="walkins" element={<RoleRoute roles={['owner','receptionist']}><WalkinsOverview /></RoleRoute>} />
        <Route path="walkins/new" element={<RoleRoute roles={['owner','receptionist']}><WalkinRegistration /></RoleRoute>} />
        <Route path="walkins/mine" element={<RoleRoute roles={['owner','receptionist']}><MyWalkins /></RoleRoute>} />
        <Route path="walkins/history" element={<RoleRoute roles={['owner','receptionist']}><WalkinHistory /></RoleRoute>} />
        <Route path="walkins/:id" element={<RoleRoute roles={['owner','receptionist']}><WalkinDetail /></RoleRoute>} />
        <Route path="enquiries/new" element={<NewEnquiry />} />
        <Route path="enquiries/:id/edit" element={<NewEnquiry />} />
        <Route path="enquiries/:id" element={<EnquiryDetail />} />
        <Route path="analytics" element={<Analytics />} />
        <Route path="integrations" element={<Zoho />} />
        <Route path="zoho" element={<Zoho />} />
        <Route path="recordings" element={<Recordings />} />
        <Route path="recordings/:id" element={<Recordings />} />
        <Route path="settings" element={<Settings />} />
        <Route path="ai-chats" element={<AIChats />} />
        <Route path="products" element={<Products />} />
        <Route path="contacts" element={<Contacts />} />
        <Route path="quotes" element={<Quotes />} />
        <Route path="sales-orders" element={<SalesOrders />} />
        <Route path="purchase-orders" element={<PurchaseOrders />} />
        <Route path="invoices" element={<Invoices />} />
        <Route path="pipelines" element={<Pipelines />} />
      </Route>
      <Route path="*" element={<div style={{padding:20}}>Page not found. Open Overview or Enquiries from the navigation.</div>} />
    </Routes></Suspense>
  )
}

function ScopedLayout() {
  const { user, workspace } = useOffice()
  const location = useLocation()
  // Tear down pending page state when the security context changes. Async hooks
  // still check their identity tickets before accepting late responses.
  if (workspace?.role === 'receptionist' && location.pathname !== '/settings' && !location.pathname.startsWith('/walkins')) return <Navigate to="/walkins" replace />
  return <AppLayout key={`${user?.id}:${workspace?.id}:${workspace?.member_id ?? user?.id}`} />
}
