import { useCallback, useEffect, useRef, useState } from 'react'
import { useOffice } from '../contexts/OfficeContext'
import { ApiError, request, message } from '../services/officeApi'

export function useScopedResource<T>(path: string | null) {
  const { user, workspace } = useOffice()
  const scope = user && workspace && path ? `${user.id}/${workspace.id}/${workspace.member_id ?? user.id}/${path}` : ''
  const [state, setState] = useState<{ scope: string; data?: T; error: string; loading: boolean }>({ scope: '', error: '', loading: false })
  const current = useRef<{ scope: string; abort: AbortController; loading: boolean } | null>(null)
  const reload = useCallback(async () => {
    const operation = current.current
    if (!operation || !scope || !workspace || !path || operation.loading || operation.abort.signal.aborted) return
    operation.loading = true
    setState(old => ({ scope, data: old.scope === scope ? old.data : undefined, error: '', loading: true }))
    try {
      const data = await request<T>(path, workspace.id, { signal: operation.abort.signal }, workspace.member_id ?? workspace.id)
      if (current.current === operation && !operation.abort.signal.aborted) setState({ scope, data, error: '', loading: false })
    } catch (failure) {
      const lostAccess = failure instanceof ApiError && [401, 403, 404].includes(failure.status)
      if (current.current === operation && !operation.abort.signal.aborted) setState(old => ({ scope, data: !lostAccess && old.scope === scope ? old.data : undefined, error: message(failure), loading: false }))
    } finally { operation.loading = false }
  }, [path, scope, workspace?.id])
  useEffect(() => {
    const operation = { scope, abort: new AbortController(), loading: false }
    current.current = operation
    void reload()
    return () => { operation.abort.abort(); if (current.current === operation) current.current = null }
  }, [reload, scope])
  return { data: state.scope === scope && scope ? state.data : undefined, error: state.scope === scope && scope ? state.error : '', loading: !!scope && (state.scope !== scope || state.loading), reload }
}
