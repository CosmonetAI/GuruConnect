import { Component, type ReactNode } from 'react'

export default class AppErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  render() {
    if (this.state.failed) return <main className="welcome-page"><section className="welcome-card" role="alert">
      <h1>This screen could not open</h1><p>Reload to try again. Any audio already saved will remain available.</p>
      <button type="button" className="button button-primary" onClick={() => location.reload()}>Reload ConnectCosmos</button>
    </section></main>
    return this.props.children
  }
}
