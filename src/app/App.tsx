import { type FC, type ReactNode } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import AppLayout from '../layouts/AppLayout'
import '../styles/forms.css'
import Dashboard from '../pages/Dashboard/Dashboard'
import EnquiriesList from '../pages/Enquiries/EnquiriesList'
import NewEnquiry from '../pages/Enquiries/NewEnquiry'
import EnquiryDetail from '../pages/Enquiries/EnquiryDetail'
import Analytics from '../pages/Analytics'
import Settings from '../pages/Settings'
import WalkinsOverview from '../pages/Walkins/WalkinsOverview'
import WalkinRegistration from '../pages/Walkins/WalkinRegistration'
import WalkinDetail from '../pages/Walkins/WalkinDetail'
import MyWalkins from '../pages/Walkins/MyWalkins'
import WalkinHistory from '../pages/Walkins/WalkinHistory'

const ProtectedRoute: FC<{ children: ReactNode }> = ({ children }) => {
  // Placeholder for real auth. Allow access in demo mode.
  return <>{children}</>
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route
        path="/"
        element={
          <ProtectedRoute>
            <AppLayout />
          </ProtectedRoute>
        }
      >
        <Route path="dashboard" element={<Dashboard />} />
        <Route path="enquiries" element={<EnquiriesList />} />
        <Route path="enquiries/new" element={<NewEnquiry />} />
        <Route path="enquiries/:id" element={<EnquiryDetail />} />
        <Route path="walkins" element={<WalkinsOverview />} />
        <Route path="walkins/new" element={<WalkinRegistration />} />
        <Route path="walkins/mine" element={<MyWalkins />} />
        <Route path="walkins/history" element={<WalkinHistory />} />
        <Route path="walkins/:id" element={<WalkinDetail />} />
        <Route path="review-queue" element={<div style={{padding:20}}>Review Queue (TODO)</div>} />
        <Route path="quotations" element={<div style={{padding:20}}>Quotations (TODO)</div>} />
        <Route path="quotations/new" element={<div style={{padding:20}}>New Quotation (TODO)</div>} />
        <Route path="quotations/:id" element={<div style={{padding:20}}>Quotation detail (TODO)</div>} />
        <Route path="follow-ups" element={<div style={{padding:20}}>Follow-ups (TODO)</div>} />
        <Route path="analytics" element={<Analytics />} />
        <Route path="products" element={<div style={{padding:20}}>Products (TODO)</div>} />
        <Route path="integrations" element={<div style={{padding:20}}>Integrations (TODO)</div>} />
        <Route path="settings" element={<Settings />} />
      </Route>
      <Route path="*" element={<div style={{padding:20}}>Not Found</div>} />
    </Routes>
  )
}
