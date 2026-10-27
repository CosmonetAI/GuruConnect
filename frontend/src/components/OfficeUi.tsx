import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { useOffice } from '../contexts/OfficeContext'
import { ApiError, message, request } from '../services/officeApi'
import '../styles/office-processing.css'

const POLL_INTERVAL = 4000
const MAX_POLL_CHECKS = 180

/** Private results are bound to both the signed-in account and workspace. */
export function useOfficeResource<T>(path: string | null, shouldPoll?: (value: T) => boolean) {
  const { user, workspace } = useOffice()
  const scope = user && workspace && path ? `${user.id}:${workspace.id}:${workspace.member_id ?? user.id}:${path}` : ''
  const currentScope = useRef(scope)
  currentScope.current = scope
  const poll = useRef(shouldPoll)
  poll.current = shouldPoll
  const [state, setState] = useState<{ scope: string; data?: T; error: string; loading: boolean; refreshing: boolean; pollPaused: boolean }>({ scope: '', error: '', loading: false, refreshing: false, pollPaused: false })
  const reloadRef = useRef<() => void>(() => {})

  useEffect(() => {
    if (!scope || !workspace || !path) return
    let stopped = false
    let running = false
    let queuedReload = false
    let pollChecks = 0
    let fatalReadFailure = false
    let latest: T | undefined
    let timer: ReturnType<typeof setTimeout> | undefined
    let controller: AbortController | undefined
    const visible = () => document.visibilityState !== 'hidden' && navigator.onLine !== false
    const schedule = () => {
      if (stopped || fatalReadFailure || !poll.current || (latest && !poll.current(latest))) return
      if (pollChecks >= MAX_POLL_CHECKS) {
        setState(value => value.scope === scope ? { ...value, pollPaused: true } : value)
        return
      }
      timer = setTimeout(() => {
        if (visible()) { pollChecks += 1; void load() }
      }, POLL_INTERVAL)
    }
    const load = async (explicit = false) => {
      if (stopped || currentScope.current !== scope) return
      if (running) { if (explicit) queuedReload = true; return }
      clearTimeout(timer)
      running = true
      controller = new AbortController()
      setState(value => value.scope === scope
        ? { ...value, refreshing: !!value.data, loading: !value.data, error: '' }
        : { scope, error: '', loading: true, refreshing: false, pollPaused: false })
      try {
        const data = await request<T>(path, workspace.id, { signal: controller.signal }, workspace.member_id)
        if (stopped || currentScope.current !== scope) return
        latest = data
        fatalReadFailure = false
        setState({ scope, data, error: '', loading: false, refreshing: false, pollPaused: false })
      } catch (error) {
        if (stopped || controller.signal.aborted || currentScope.current !== scope) return
        fatalReadFailure = error instanceof ApiError && [401, 403, 404, 422].includes(error.status)
        const accessLost = error instanceof ApiError && [401, 403, 404].includes(error.status)
        setState(value => value.scope === scope
          ? { ...value, ...(accessLost ? { data: undefined } : {}), error: message(error), loading: false, refreshing: false }
          : { scope, error: message(error), loading: false, refreshing: false, pollPaused: false })
      } finally {
        running = false
        if (queuedReload && !stopped) { queuedReload = false; void load() }
        else schedule()
      }
    }
    const resume = () => { if (visible() && !fatalReadFailure && pollChecks < MAX_POLL_CHECKS && (!latest || poll.current?.(latest))) void load() }
    reloadRef.current = () => { pollChecks = 0; fatalReadFailure = false; void load(true) }
    document.addEventListener('visibilitychange', resume)
    window.addEventListener('online', resume)
    void load()
    return () => {
      stopped = true
      clearTimeout(timer)
      controller?.abort()
      document.removeEventListener('visibilitychange', resume)
      window.removeEventListener('online', resume)
    }
  }, [scope, path, workspace?.id])

  const reload = useCallback(() => reloadRef.current(), [])
  const active = state.scope === scope && !!scope
  return { data: active ? state.data : undefined, error: active ? state.error : '', loading: !!scope && (!active || state.loading), refreshing: active && state.refreshing, pollPaused: active && state.pollPaused, reload }
}

export function OfficeNotice({ children, error = false }: { children: ReactNode; error?: boolean }) {
  return <div className={`office-notice${error ? ' office-notice-error' : ''}`} role={error ? 'alert' : 'status'}>{children}</div>
}

export function OfficeStatus({ value }: { value?: string | null }) {
  const text = value || 'not_started'
  const tone = ['available', 'complete', 'completed', 'connected', 'saved', 'succeeded'].includes(text) ? 'success' : ['failed', 'invalid_credentials', 'dead', 'degraded', 'uncertain'].includes(text) ? 'error' : ['blocked', 'disabled', 'disconnected', 'partial'].includes(text) ? 'warning' : 'neutral'
  return <span className={`office-status office-status-${tone}`}>{text.replaceAll('_', ' ')}</span>
}

export function OfficeDialog({ title, children, close, busy = false, className = '' }: { title: string; children: ReactNode; close: () => void; busy?: boolean; className?: string }) {
  const id = useId()
  const panel = useRef<HTMLDivElement>(null)
  const onClose = useRef(close)
  onClose.current = close
  const isBusy = useRef(busy)
  isBusy.current = busy
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : undefined
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const focusable = () => Array.from(panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [tabindex="0"]') || [])
    ;(focusable()[0] || panel.current)?.focus()
    const keys = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !isBusy.current) { event.preventDefault(); onClose.current() }
      if (event.key !== 'Tab') return
      const elements = focusable()
      if (!elements.length) { event.preventDefault(); panel.current?.focus(); return }
      const first = elements[0]
      const last = elements.at(-1)
      if (event.shiftKey && (document.activeElement === first || !panel.current?.contains(document.activeElement))) { event.preventDefault(); last?.focus() }
      else if (!event.shiftKey && (document.activeElement === last || !panel.current?.contains(document.activeElement))) { event.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', keys)
    return () => { document.removeEventListener('keydown', keys); document.body.style.overflow = previousOverflow; previous?.focus() }
  }, [])
  return <div className="office-dialog-overlay"><div className={`office-dialog ${className}`.trim()} ref={panel} role="dialog" aria-modal="true" aria-labelledby={id} tabIndex={-1}>
    <div className="office-row office-between"><h2 id={id}>{title}</h2><button type="button" className="office-button office-secondary" aria-label="Close dialog" disabled={busy} onClick={close}>Close</button></div>
    {children}
  </div></div>
}
