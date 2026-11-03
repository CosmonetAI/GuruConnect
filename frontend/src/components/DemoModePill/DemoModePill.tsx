import { useOffice } from '../../contexts/OfficeContext'

export default function DemoModePill() {
  const { localMode } = useOffice()
  return localMode ? <span className="demo-mode-pill" aria-label="Demo mode" title="Demo mode"><span>Demo</span><i className="demo-mode-dot" aria-hidden="true" /></span> : null
}
