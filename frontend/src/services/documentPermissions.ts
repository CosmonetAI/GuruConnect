import { useEffect, useMemo, useState } from 'react'
import { call } from '../zoho/shared/api.js'
import { useOffice } from '../contexts/OfficeContext'

export const DOCUMENT_TYPES = ['quote', 'invoice', 'sales-order', 'purchase-order'] as const
export type DocumentType = typeof DOCUMENT_TYPES[number]
export type DocumentAction = 'create' | 'edit' | 'delete' | 'accept' | 'mark-paid'
export type DocumentPermission = Record<DocumentAction, boolean>
export type RoleDocumentPermissions = Record<DocumentType, DocumentPermission>
export type RoleDocumentPermissionResponse = { roles: Record<string, RoleDocumentPermissions> }

const actions = (): DocumentPermission => ({ create: true, edit: true, delete: true, accept: true, 'mark-paid': true })
export const defaultRoleDocumentPermissions = (role?: string): RoleDocumentPermissions => {
  const enabled = role !== 'receptionist'
  return Object.fromEntries(DOCUMENT_TYPES.map(type => [type, enabled ? actions() : { create: false, edit: false, delete: false, accept: false, 'mark-paid': false }])) as RoleDocumentPermissions
}

export function canDocumentAction(permissions: RoleDocumentPermissions | undefined, type: string, action: DocumentAction) {
  return Boolean(permissions?.[type as DocumentType]?.[action])
}

export function useDocumentPermissions() {
  const { workspace, localMode } = useOffice()
  const role = workspace?.role
  const [permissions, setPermissions] = useState<RoleDocumentPermissions>(() => defaultRoleDocumentPermissions(role))
  const [loading, setLoading] = useState(!localMode && !!workspace)
  useEffect(() => {
    let alive = true
    setPermissions(defaultRoleDocumentPermissions(role))
    if (localMode || !workspace || !role) { setLoading(false); return () => { alive = false } }
    setLoading(true)
    call('/role-document-permissions')
      .then(result => { if (alive) setPermissions(result.roles?.[role || ''] || result.permissions || defaultRoleDocumentPermissions(role)) })
      .catch(() => { if (alive) setPermissions(defaultRoleDocumentPermissions(role)) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [localMode, role, workspace?.id, workspace?.member_id])
  const can = useMemo(() => (type: string, action: DocumentAction) => canDocumentAction(permissions, type, action), [permissions])
  return { permissions, can, loading }
}
