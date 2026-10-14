import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import { OfficeProvider } from './contexts/OfficeContext'
import App from './app/App'
import AppErrorBoundary from './components/AppErrorBoundary'
import './styles/variables.css'
import './styles/global.css'
import './styles/workspace.css'

createRoot(document.getElementById('app')!).render(
  <StrictMode>
    <AppErrorBoundary><OfficeProvider><RouterProvider router={createBrowserRouter([{ path: '*', element: <App />, errorElement: <main className="welcome-page"><section className="welcome-card" role="alert"><h1>This screen could not open</h1><p>Reload to try again.</p><a href="/">Open GuruConnect</a></section></main> }])} /></OfficeProvider></AppErrorBoundary>
  </StrictMode>
)
