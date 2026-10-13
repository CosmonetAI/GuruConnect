import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import { OfficeProvider } from './contexts/OfficeContext'
import App from './app/App'
import './styles/variables.css'
import './styles/global.css'
import './styles/workspace.css'

createRoot(document.getElementById('app')!).render(
  <StrictMode>
    <OfficeProvider><RouterProvider router={createBrowserRouter([{ path: '*', element: <App /> }])} /></OfficeProvider>
  </StrictMode>
)
