import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router-dom'
import { CalendarDays, ChevronDown, Clock3, ExternalLink, IndianRupee, MoreHorizontal, Percent, Plus, RefreshCw, Save, Search, Target, Trash2, UserRound } from 'lucide-react'
import { call } from '../shared/api'
import PageLoader from '../components/PageLoader'
import { useOffice } from '../contexts/OfficeContext'
import '../styles/pipelines.css'

type Layout = { id: string; name: string; stages?: string[] }
type RecordItem = { id: string; Deal_Name?: string; Stage?: string; Amount?: number; Expected_Revenue?: number; Probability?: number; Closing_Date?: string; Next_Step?: string; Contact_Name?: { id?: string; name?: string }; Owner?: { id?: string; name?: string }; Pipeline?: { id?: string; name?: string } | string; Sub_Pipeline?: string; Modified_Time?: string; Description?: string; sourceEnquiryId?: string; internal_enquiry?: boolean; Phone?: string; Email?: string; [key: string]: unknown }
type DeletedRecord = { id?: string; name?: string; Deal_Name?: string; deleted_time?: string; deleted_by?: { id?: string; name?: string }; [key: string]: unknown }
type PipelineForm = { Deal_Name: string; Stage: string; Sub_Pipeline: string; Amount: string; Expected_Revenue: string; Probability: string; Closing_Date: string; Next_Step: string; Description: string; Contact_Id: string; Owner_Id: string }
type CustomerSearchResult = { status?: string; matched_by?: string; contacts?: { id?: string; name?: string; phone?: string; email?: string }[]; deals?: { deal_id?: string; deal_name?: string; stage?: string; pipeline_name?: string; amount?: number; closing_date?: string; owner?: string; contact_name?: string; contact_phone?: string; contact_email?: string }[] }

const money = (value?: number) => value == null ? '' : `₹${Number(value).toLocaleString('en-IN', { minimumFractionDigits: 2 })}`
const relative = (value?: string) => value ? new Intl.DateTimeFormat('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }).format(new Date(value)) : '—'
const syncLabel = (value?: string | null) => value ? new Intl.DateTimeFormat('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }).format(new Date(value)) : 'Not yet synced'
const emptyForm = (stage = ''): PipelineForm => ({ Deal_Name: '', Stage: stage, Sub_Pipeline: 'Sales Pipeline Standard', Amount: '', Expected_Revenue: '', Probability: '', Closing_Date: '', Next_Step: '', Description: '', Contact_Id: '', Owner_Id: '' })
const labelForKey = (key: string) => key.replace(/^\$/, '').replace(/__s$/, '').replace(/_/g, ' ').replace(/\b\w/g, char => char.toUpperCase())
const stageOrder = ['new enquiry', 'enquiry', 'proposal/price quote', 'negotiation/review', 'closed won', 'closed lost']

function DetailValue({ value }: { value: unknown }) {
  if (value == null || value === '') return <span className="detail-empty">—</span>
  if (typeof value === 'string') {
    const trimmed = value.trim()
    if ((trimmed.startsWith('{') && trimmed.endsWith('}')) || (trimmed.startsWith('[') && trimmed.endsWith(']'))) {
      try { return <DetailValue value={JSON.parse(trimmed)} /> } catch { /* display the original value */ }
    }
    return <span>{value}</span>
  }
  if (typeof value === 'number' || typeof value === 'boolean') return <span>{String(value)}</span>
  if (Array.isArray(value)) {
    if (!value.length) return <span className="detail-empty">—</span>
    if (value.every(item => item == null || ['string', 'number', 'boolean'].includes(typeof item))) return <span>{value.map(item => item == null ? '—' : String(item)).join(', ')}</span>
    return <div className="detail-nested-list">{value.map((item, index) => <div className="detail-nested-item" key={index}><small>Item {index + 1}</small><DetailValue value={item} /></div>)}</div>
  }
  return <div className="detail-nested-grid">{Object.entries(value as Record<string, unknown>).map(([key, nestedValue]) => <div className="detail-nested-field" key={key}><small>{labelForKey(key)}</small><DetailValue value={nestedValue} /></div>)}</div>
}

const opportunityFieldKeys = new Set(['Deal_Name', 'Stage', 'Pipeline', 'Amount', 'Expected_Revenue', 'Probability', 'Closing_Date', 'Next_Step', 'Description', 'Contact_Name', 'Owner', 'Sub_Pipeline'])
const technicalFieldKeys = new Set(['id', '$layout_id', '$review_process', '$approval', '$editable', '$zia_owner_assignment', 'Created_Time', 'Modified_Time', 'Created_By', 'Modified_By'])

function OpportunityDetails({ record }: { record: RecordItem }) {
  const additionalFields = Object.entries(record).filter(([key, value]) => !opportunityFieldKeys.has(key) && !technicalFieldKeys.has(key) && value != null && value !== '')
  const pipelineName = record.Pipeline && typeof record.Pipeline === 'object' ? record.Pipeline.name : record.Pipeline
  const contactName = record.Contact_Name?.name || 'No contact assigned'
  const ownerName = record.Owner?.name || 'No owner assigned'
  const initials = (record.Deal_Name || 'Opportunity').split(/\s+/).slice(0, 2).map(part => part[0]).join('').toUpperCase()
  return <div className="opportunity-details">
    <div className="opportunity-summary">
      <div className="opportunity-avatar" aria-hidden="true">{initials}</div>
      <div className="opportunity-summary-copy">
        <span className="opportunity-summary-label">Sales opportunity</span>
        <strong>{record.Deal_Name || 'Untitled opportunity'}</strong>
        <span>{pipelineName || 'Pipeline'} · {record.Sub_Pipeline || 'Standard sales pipeline'}</span>
      </div>
      <div className="opportunity-status"><span className="status-dot" />{record.Stage || 'Unassigned'}</div>
    </div>
    <section className="opportunity-section opportunity-metrics" aria-label="Opportunity summary">
      <div className="opportunity-metric"><IndianRupee size={17} /><span>Deal value</span><strong>{record.Amount == null ? 'Not set' : money(record.Amount)}</strong></div>
      <div className="opportunity-metric"><Target size={17} /><span>Expected revenue</span><strong>{record.Expected_Revenue == null ? 'Not set' : money(record.Expected_Revenue)}</strong></div>
      <div className="opportunity-metric"><Percent size={17} /><span>Probability</span><strong>{record.Probability == null ? 'Not set' : `${record.Probability}%`}</strong></div>
      <div className="opportunity-metric"><CalendarDays size={17} /><span>Closing date</span><strong>{record.Closing_Date ? relative(record.Closing_Date) : 'Not set'}</strong></div>
    </section>
    <div className="opportunity-content">
      <section className="opportunity-section">
        <div className="opportunity-section-heading"><div><span className="section-kicker">Relationship</span><h4>People & next action</h4></div></div>
        <div className="opportunity-people">
          <div className="person-row"><UserRound size={17} /><div><small>Contact</small><strong>{contactName}</strong>{record.Phone && <span>{record.Phone}</span>}{record.Email && <span>{record.Email}</span>}</div></div>
          <div className="person-row"><UserRound size={17} /><div><small>Owner</small><strong>{ownerName}</strong></div></div>
        </div>
        <div className="next-step"><small>Next step</small><strong>{record.Next_Step || 'No next step added yet'}</strong></div>
      </section>
      <section className="opportunity-section">
        <div className="opportunity-section-heading"><div><span className="section-kicker">Context</span><h4>Description</h4></div></div>
        <p className={`opportunity-description${record.Description ? '' : ' is-empty'}`}>{record.Description || 'No description has been added for this opportunity.'}</p>
      </section>
    </div>
    {additionalFields.length > 0 && <section className="opportunity-section additional-fields"><div className="opportunity-section-heading"><div><span className="section-kicker">CRM metadata</span><h4>Additional information</h4></div><span className="additional-count">{additionalFields.length} fields</span></div><div className="additional-field-grid">{additionalFields.map(([key, value]) => <div className="additional-field" key={key}><small>{labelForKey(key)}</small><div><DetailValue value={value} /></div></div>)}</div></section>}
  </div>
}

export default function Pipelines() {
  const { workspace } = useOffice()
  const [searchParams] = useSearchParams()
  const linkedDeal = searchParams.get('deal') || '', linkedPipeline = searchParams.get('pipeline') || ''
  const [pinned, setPinned] = useState<RecordItem | null>(null)
  const [layouts, setLayouts] = useState<Layout[]>([]), [selected, setSelected] = useState('')
  const [records, setRecords] = useState<RecordItem[]>([]), [query, setQuery] = useState(''), [searchResults, setSearchResults] = useState<RecordItem[]>([])
  const [newEnquiries, setNewEnquiries] = useState<RecordItem[]>([])
  const [busy, setBusy] = useState(false), [syncing, setSyncing] = useState(false), [error, setError] = useState(''), [lastSync, setLastSync] = useState<string | null>(null), [hasMore, setHasMore] = useState(false), [page, setPage] = useState(1)
  const [opened, setOpened] = useState<RecordItem | null>(null), [customerSearch, setCustomerSearch] = useState<CustomerSearchResult | null>(null), [customerSearchBusy, setCustomerSearchBusy] = useState(false), [menuStage, setMenuStage] = useState<string | null>(null), [editor, setEditor] = useState<{ mode: 'add' | 'edit'; record?: RecordItem } | null>(null), [form, setForm] = useState<PipelineForm>(emptyForm()), [saving, setSaving] = useState(false)
  const [draggingId, setDraggingId] = useState<string | null>(null), [movingId, setMovingId] = useState<string | null>(null), [dropStage, setDropStage] = useState<string | null>(null)
  const [recycleOpen, setRecycleOpen] = useState(false), [recycleBusy, setRecycleBusy] = useState(false), [deletedPipelines, setDeletedPipelines] = useState<DeletedRecord[]>([]), [deletedTasks, setDeletedTasks] = useState<DeletedRecord[]>([])

  const loadLayouts = async () => { if (!workspace?.id) return; try { const result = await call('/pipelines/layouts', {}, workspace.id); const next = result.layouts || []; setLayouts(next); setSelected(current => current || (linkedPipeline && next.some((layout: Layout) => layout.id === linkedPipeline) ? linkedPipeline : next[0]?.id) || '') } catch (e) { setError((e as Error).message) } }
  const loadRecords = async (pipelineId = selected) => { if (!pipelineId || !workspace?.id) return; setBusy(true); setError(''); try { const result = await call(`/pipelines?pipeline_id=${encodeURIComponent(pipelineId)}&page=1`, {}, workspace.id); setRecords(result.records || []); setLastSync(result.last_sync || null); setHasMore(Boolean(result.has_more)); setPage(1) } catch (e) { setError((e as Error).message) } finally { setBusy(false) } }
  const loadNewEnquiries = async () => { if (!workspace?.id) return; try { const result = await call('/pipelines/new-enquiries', {}, workspace.id); setNewEnquiries(result.records || []) } catch (e) { setError((e as Error).message) } }
  useEffect(() => { const customer = searchParams.get('customer'); if (customer && !linkedDeal) setQuery(customer) }, [searchParams])
  useEffect(() => {
    if (!linkedDeal || !workspace?.id) return
    let current = true
    setQuery(''); setPinned(null)
    if (linkedPipeline) setSelected(linkedPipeline)
    call(`/pipelines/record/${encodeURIComponent(linkedDeal)}`, {}, workspace.id).then(result => {
      const record = result.record as RecordItem | null
      if (!current) return
      if (!record?.id) { setError('The linked CRM deal could not be found in Zoho Bigin.'); return }
      const pipelineId = record.Pipeline && typeof record.Pipeline === 'object' ? record.Pipeline.id : (record.$layout_id as { id?: string } | undefined)?.id
      if (pipelineId) setSelected(pipelineId)
      setPinned(record); setOpened(record)
    }).catch(() => { if (current) setError('Unable to open the linked CRM deal. Please try again.') })
    return () => { current = false }
  }, [linkedDeal, linkedPipeline, workspace?.id])
  useEffect(() => { if (linkedDeal) document.querySelector(`[data-deal-id="${CSS.escape(linkedDeal)}"]`)?.scrollIntoView?.({ block: 'center', inline: 'center', behavior: 'smooth' }) }, [linkedDeal, pinned, records, busy])
  useEffect(() => { if (workspace?.id) void loadLayouts() }, [workspace?.id])
  useEffect(() => { if (workspace?.id) void loadNewEnquiries() }, [workspace?.id])
  useEffect(() => { if (selected) void loadRecords(selected) }, [selected])
  const sync = async () => { if (!workspace?.id) return; setSyncing(true); setError(''); try { const result = await call('/pipelines/sync', { method: 'POST' }, workspace.id); if (result.last_sync) setLastSync(result.last_sync); await loadLayouts(); await loadRecords(selected); await loadNewEnquiries() } catch (e) { setError((e as Error).message) } finally { setSyncing(false) } }
  useEffect(() => { if (layouts.length > 0 && workspace?.id) void sync() }, [layouts.length, workspace?.id])
  const showMore = async () => { if (!hasMore || busy || !workspace?.id) return; setBusy(true); try { const result = await call(`/pipelines?pipeline_id=${encodeURIComponent(selected)}&page=${page + 1}`, {}, workspace.id); setRecords(current => { const ids = new Set(current.map(item => item.id)); return [...current, ...(result.records || []).filter((item: RecordItem) => !ids.has(item.id))] }); setPage(current => current + 1); setHasMore(Boolean(result.has_more)) } catch (e) { setError((e as Error).message) } finally { setBusy(false) } }
  useEffect(() => { const timer = setTimeout(async () => { const cleanedQuery = query.replace(/\\/g, '').trim(); if (!cleanedQuery || !workspace?.id) { setSearchResults([]); if (!cleanedQuery) setError(''); return }; try { const result = await call(`/pipelines/search?word=${encodeURIComponent(cleanedQuery)}`, {}, workspace.id); setSearchResults(result.records || []); setError('') } catch { setSearchResults([]); setError('Search is temporarily unavailable. Please try again.') } }, 350); return () => clearTimeout(timer) }, [query, workspace?.id])

  const stages = useMemo(() => {
    const layoutStages = layouts.find(layout => layout.id === selected)?.stages || []
    const names = Array.from(new Map(['New enquiry', ...layoutStages, ...records.map(item => item.Stage || 'Unassigned')].map(stage => [stage.trim().toLowerCase(), stage])).values())
    const ordered = names.sort((left, right) => {
      const leftIndex = stageOrder.indexOf(left.trim().toLowerCase())
      const rightIndex = stageOrder.indexOf(right.trim().toLowerCase())
      return (leftIndex === -1 ? stageOrder.length : leftIndex) - (rightIndex === -1 ? stageOrder.length : rightIndex)
    })
    return ordered.length > 1 ? ordered : ['New enquiry', 'Enquiry', 'Proposal/Price Quote', 'Negotiation/Review', 'Closed Won', 'Closed Lost']
  }, [layouts, selected, records])
  const pinnedPipelineId = pinned && (pinned.Pipeline && typeof pinned.Pipeline === 'object' ? pinned.Pipeline.id : (pinned.$layout_id as { id?: string } | undefined)?.id)
  const baseRecords = pinned && !records.some(item => item.id === pinned.id) && (!pinnedPipelineId || pinnedPipelineId === selected) ? [pinned, ...records] : records
  const allRecords = [...newEnquiries, ...baseRecords]
  const visibleRecords = query.trim() ? searchResults : allRecords
  const focusedStage = (pinned?.Stage as string | undefined) || searchParams.get('stage') || ''
  const openAdd = (stage: string) => { setMenuStage(null); setOpened(null); setForm(emptyForm(stage)); setEditor({ mode: 'add' }) }
  const searchCustomer = async (record: RecordItem) => {
    if (!workspace?.id || customerSearchBusy) return
    const name = record.Contact_Name?.name || ''
    if (!name && !record.Phone && !record.Email) return
    setCustomerSearchBusy(true); setError('')
    try {
      const params = new URLSearchParams()
      if (name) params.set('name', name)
      if (record.Phone) params.set('phone', record.Phone)
      if (record.Email) params.set('email', record.Email)
      const result = await call(`/bigin/crm-lead?${params.toString()}`, {}, workspace.id)
      setCustomerSearch(result as CustomerSearchResult)
    } catch (e) { setError((e as Error).message) } finally { setCustomerSearchBusy(false) }
  }
  const openEdit = (record: RecordItem) => { setOpened(null); setForm({ Deal_Name: record.Deal_Name || '', Stage: record.Stage || '', Sub_Pipeline: record.Sub_Pipeline || 'Sales Pipeline Standard', Amount: record.Amount == null ? '' : String(record.Amount), Expected_Revenue: record.Expected_Revenue == null ? '' : String(record.Expected_Revenue), Probability: record.Probability == null ? '' : String(record.Probability), Closing_Date: record.Closing_Date || '', Next_Step: record.Next_Step || '', Description: record.Description || '', Contact_Id: record.Contact_Name?.id || '', Owner_Id: record.Owner?.id || '' }); setEditor({ mode: 'edit', record }) }
  const saveEditor = async (event: FormEvent) => { event.preventDefault(); if (!workspace?.id || saving || !editor) return; setSaving(true); setError(''); const payload: Record<string, unknown> = { Deal_Name: form.Deal_Name.trim(), Stage: form.Stage, Sub_Pipeline: form.Sub_Pipeline.trim(), Pipeline: { id: selected } }; if (form.Amount !== '') payload.Amount = Number(form.Amount); if (form.Expected_Revenue !== '') payload.Expected_Revenue = Number(form.Expected_Revenue); if (form.Probability !== '') payload.Probability = Number(form.Probability); if (form.Closing_Date) payload.Closing_Date = form.Closing_Date; if (form.Next_Step.trim()) payload.Next_Step = form.Next_Step.trim(); if (form.Description.trim()) payload.Description = form.Description.trim(); if (form.Contact_Id.trim()) payload.Contact_Name = { id: form.Contact_Id.trim() }; if (form.Owner_Id.trim()) payload.Owner = { id: form.Owner_Id.trim() }; try { const result = editor.mode === 'edit' && editor.record ? await call(`/pipelines/record/${editor.record.id}`, { method: 'PUT', body: JSON.stringify(payload) }, workspace.id) : await call('/pipelines', { method: 'POST', body: JSON.stringify(payload) }, workspace.id); const savedRecord = result.record as RecordItem | undefined; if (savedRecord?.id) { const stageRecord = { ...savedRecord, Stage: savedRecord.Stage || form.Stage }; setRecords(current => editor.mode === 'edit' ? current.map(item => item.id === stageRecord.id ? { ...item, ...stageRecord } : item) : [stageRecord, ...current].slice(0, 50)); } setEditor(null); setOpened(null); if (editor.mode === 'add') await loadRecords(selected) } catch (e) { setError((e as Error).message) } finally { setSaving(false) } }
  const moveRecord = async (record: RecordItem, targetStage: string) => { if (!workspace?.id || movingId || !record.id || (record.Stage || 'Unassigned') === targetStage) return; const previousStage = record.Stage; setMovingId(record.id); setDraggingId(null); setDropStage(null); setError(''); if (record.internal_enquiry && targetStage !== 'New enquiry') { setNewEnquiries(current => current.filter(item => item.id !== record.id)); try { const result = await call('/pipelines/from-enquiry', { method: 'POST', body: JSON.stringify({ enquiry_id: record.sourceEnquiryId, pipeline_id: selected, stage: targetStage }) }, workspace.id); const savedRecord = result.record as RecordItem | undefined; if (savedRecord?.id) setRecords(current => [savedRecord, ...current].slice(0, 50)); else await loadRecords(selected); await sync() } catch (e) { setNewEnquiries(current => [record, ...current]); setError((e as Error).message || 'Unable to move enquiry into Zoho Bigin.') } finally { setMovingId(null); setDraggingId(null); setDropStage(null) } return } if (record.internal_enquiry) { setMovingId(null); return } setRecords(current => current.map(item => item.id === record.id ? { ...item, Stage: targetStage } : item)); try { const result = await call(`/pipelines/record/${record.id}`, { method: 'PUT', body: JSON.stringify({ Stage: targetStage, Pipeline: { id: selected } }) }, workspace.id); const savedRecord = result.record as RecordItem | undefined; if (savedRecord?.id) setRecords(current => current.map(item => item.id === savedRecord.id ? { ...item, ...savedRecord, Stage: savedRecord.Stage || targetStage } : item)); await sync() } catch (e) { setRecords(current => current.map(item => item.id === record.id ? { ...item, Stage: previousStage } : item)); setError((e as Error).message || 'Unable to move deal.') } finally { setMovingId(null); setDraggingId(null); setDropStage(null) } }
  const deleteRecord = async (record: RecordItem) => { if (!workspace?.id || saving || !window.confirm(`Delete ${record.Deal_Name || 'this opportunity'} from the sales pipeline?`)) return; setSaving(true); setError(''); try { await call(`/pipelines/record/${record.id}`, { method: 'DELETE' }, workspace.id); setRecords(current => current.filter(item => item.id !== record.id)); setOpened(null) } catch (e) { setError((e as Error).message) } finally { setSaving(false) } }

  const loadRecycleBin = async () => {
    if (!workspace?.id) return
    setRecycleBusy(true)
    setError('')
    try {
      const [pipelineResult, taskResult] = await Promise.all([
        call('/bigin/deleted/Pipelines?type=recycle&page=1', {}, workspace.id),
        call('/bigin/deleted/Tasks?type=recycle&page=1', {}, workspace.id)
      ])
      setDeletedPipelines(Array.isArray(pipelineResult.data) ? pipelineResult.data : [])
      setDeletedTasks(Array.isArray(taskResult.data) ? taskResult.data : [])
      setRecycleOpen(true)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setRecycleBusy(false)
    }
  }

  const deletedLabel = (record: DeletedRecord, fallback: string) => record.Deal_Name || record.name || record.id || fallback
  const deletedDate = (record: DeletedRecord) => record.deleted_time || String(record.deleted_at || record.deleted_on || '')

  return <section className="pipelines-page">
    <div className="pipelines-hero"><div><span className="eyebrow">SALES PIPELINE</span><h2>Pipelines</h2><p>Keep every opportunity moving with a clear view of stages, ownership, value, and next steps.</p></div><div className="sync-meta"><button className="sync-button" onClick={sync} disabled={syncing}><RefreshCw size={16} className={syncing ? 'spin' : ''} />{syncing ? 'Syncing…' : 'Refresh'}</button><span><Clock3 size={14} />{lastSync ? `Last sync ${syncLabel(lastSync)}` : 'Not yet synced'}</span></div></div>
    <div className="pipeline-toolbar"><label className="pipeline-select"><span>Pipeline</span><select value={selected} onChange={e => setSelected(e.target.value)} disabled={layouts.length <= 1}>{layouts.map(layout => <option value={layout.id} key={layout.id}>{layout.name}</option>)}</select><ChevronDown size={16} /></label><label className="pipeline-search"><Search size={17} /><input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search opportunities across all pipelines…" /></label><span className="record-count">{visibleRecords.length} opportunities</span></div>
    <div className="pipeline-actions"><button className="recycle-button" onClick={() => void loadRecycleBin()} disabled={recycleBusy}><Trash2 size={16} />{recycleBusy ? 'Loading recycle bin...' : 'Recycle bin'}</button></div>
    {error && <div className="pipeline-alert">{error}</div>}
    {busy && !visibleRecords.length ? <PageLoader kind="pipelines" label="Loading sales pipeline" detail="Organising opportunities by stage and next action." /> : <div className="kanban-scroll"><div className="kanban-board">{stages.map(stage => { const stageRecords = visibleRecords.filter(item => (item.Stage || 'Unassigned') === stage); return <div className={`kanban-column${dropStage === stage ? ' drop-target' : ''}${focusedStage === stage ? ' focused-stage' : ''}`} key={stage} onDragOver={event => { event.preventDefault(); event.dataTransfer.dropEffect = 'move'; setDropStage(stage) }} onDragLeave={event => { if (event.currentTarget === event.target) setDropStage(null) }} onDrop={event => { event.preventDefault(); const id = event.dataTransfer.getData('text/plain'); const record = allRecords.find(item => item.id === id); setDropStage(null); if (record) void moveRecord(record, stage) }}><div className="column-head"><div><strong>{stage}</strong><small>{stageRecords.length} opportunities</small></div><div className="column-menu"><button aria-label={`More options for ${stage}`} onClick={() => setMenuStage(menuStage === stage ? null : stage)}><MoreHorizontal size={18} /></button>{menuStage === stage && <div className="column-menu-popover"><button onClick={() => openAdd(stage)}><Plus size={14} /> Add opportunity</button></div>}</div></div><div className="stage-line" />{stageRecords.map(record => <button draggable={!record.internal_enquiry || stage === 'New enquiry'} data-deal-id={record.id} className={`deal-card${draggingId === record.id ? ' dragging' : ''}${linkedDeal === record.id ? ' linked-deal' : ''}`} key={record.id} onDragStart={event => { event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData('text/plain', record.id); setDraggingId(record.id) }} onDragEnd={() => { setDraggingId(null); setDropStage(null) }} onClick={async () => { if (movingId === record.id) return; if (record.internal_enquiry) { setOpened(record); return } try { const detail = await call(`/pipelines/record/${record.id}`, {}, workspace?.id); setOpened(detail.record || record) } catch { setOpened(record) } }}><strong>{record.Deal_Name || 'Untitled deal'}</strong>{record.Contact_Name?.name ? <span className="deal-customer-name" role="button" tabIndex={0} onClick={event => { event.stopPropagation(); void searchCustomer(record) }} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); event.stopPropagation(); void searchCustomer(record) } }}>{record.Contact_Name.name}</span> : <span>No contact assigned</span>}<div><b>{money(record.Amount)}</b><time>{relative(record.Closing_Date)}</time></div></button>)}{stageRecords.length === 0 && <div className="empty-stage">No opportunities in this stage</div>}</div> })}</div></div>}
    {hasMore && !query.trim() && <button className="show-more" onClick={showMore} disabled={busy}>{busy ? 'Loading…' : 'Show more opportunities'}</button>}
    {opened && <div className="deal-modal-backdrop" onClick={() => setOpened(null)}><article className="deal-modal opportunity-modal" onClick={e => e.stopPropagation()}><button className="modal-close" aria-label="Close opportunity details" onClick={() => setOpened(null)}>×</button><span className="eyebrow">OPPORTUNITY DETAILS</span><h3>{opened.Deal_Name || 'Untitled deal'}</h3><OpportunityDetails record={opened} /><div className="deal-actions"><button className="deal-action" onClick={() => openEdit(opened)}><Save size={15} /> Edit opportunity</button><button className="deal-action danger" onClick={() => void deleteRecord(opened)} disabled={saving}><Trash2 size={15} /> Delete opportunity</button></div><a href="https://bigin.zoho.com" target="_blank" rel="noreferrer"><ExternalLink size={15} />Open connected CRM</a></article></div>}
    {customerSearch && <div className="deal-modal-backdrop" onClick={() => setCustomerSearch(null)}><article className="deal-modal customer-search-modal" onClick={e => e.stopPropagation()}><button className="modal-close" onClick={() => setCustomerSearch(null)}>×</button><span className="eyebrow">CUSTOMER SEARCH</span><h3>Matching Bigin leads</h3><p className="modal-stage">{customerSearch.matched_by ? `Matched using ${customerSearch.matched_by}.` : 'Matching results from Zoho Bigin.'}</p>{customerSearch.deals?.length ? <div className="detail-grid customer-search-results">{customerSearch.deals.map(deal => <div className="detail-field" key={deal.deal_id}><strong>{deal.deal_name || 'Untitled lead'}</strong><small>{deal.stage || 'Unassigned'} · {deal.pipeline_name || 'Pipeline'}</small><div className="detail-value">{deal.contact_name || 'No contact'}{deal.contact_phone && ` · ${deal.contact_phone}`}{deal.contact_email && ` · ${deal.contact_email}`}</div><div className="detail-value">{deal.amount == null ? 'No amount' : money(deal.amount)}{deal.closing_date ? ` · Closing ${relative(deal.closing_date)}` : ''}</div></div>)}</div> : <div className="product-empty">No matching Bigin leads found.</div>}</article></div>}
    {editor && <div className="deal-modal-backdrop" onClick={() => setEditor(null)}><article className="deal-modal pipeline-editor" onClick={e => e.stopPropagation()}><button className="modal-close" onClick={() => setEditor(null)}>×</button><span className="eyebrow">{editor.mode === 'add' ? 'ADD OPPORTUNITY' : 'EDIT OPPORTUNITY'}</span><h3>{editor.mode === 'add' ? 'Create sales opportunity' : 'Update sales opportunity'}</h3><form className="pipeline-form" onSubmit={saveEditor}><label>Opportunity name<input required value={form.Deal_Name} onChange={e => setForm({ ...form, Deal_Name: e.target.value })} /></label><label>Stage<select required value={form.Stage} onChange={e => setForm({ ...form, Stage: e.target.value })}>{stages.map(stage => <option key={stage} value={stage}>{stage}</option>)}</select></label><label>Sub-pipeline<input required value={form.Sub_Pipeline} onChange={e => setForm({ ...form, Sub_Pipeline: e.target.value })} /></label><label>Amount<input type="number" min="0" step="0.01" value={form.Amount} onChange={e => setForm({ ...form, Amount: e.target.value })} /></label><label>Expected revenue<input type="number" min="0" step="0.01" value={form.Expected_Revenue} onChange={e => setForm({ ...form, Expected_Revenue: e.target.value })} /></label><label>Probability (%)<input type="number" min="0" max="100" value={form.Probability} onChange={e => setForm({ ...form, Probability: e.target.value })} /></label><label>Closing date<input type="date" value={form.Closing_Date} onChange={e => setForm({ ...form, Closing_Date: e.target.value })} /></label><label>Next step<input value={form.Next_Step} onChange={e => setForm({ ...form, Next_Step: e.target.value })} /></label><label>Customer contact ID<input value={form.Contact_Id} onChange={e => setForm({ ...form, Contact_Id: e.target.value })} placeholder="Customer contact ID" /></label><label>Sales owner ID<input value={form.Owner_Id} onChange={e => setForm({ ...form, Owner_Id: e.target.value })} placeholder="Sales owner ID" /></label><label className="pipeline-form-wide">Description<textarea value={form.Description} onChange={e => setForm({ ...form, Description: e.target.value })} /></label><div className="pipeline-form-actions"><button type="button" className="deal-action" onClick={() => setEditor(null)}>Cancel</button><button type="submit" className="deal-action primary-action" disabled={saving}>{saving ? 'Saving…' : editor.mode === 'add' ? 'Add opportunity' : 'Update opportunity'}</button></div></form></article></div>}
    {recycleOpen && <div className="deal-modal-backdrop" onClick={() => setRecycleOpen(false)}><article className="deal-modal recycle-modal" onClick={e => e.stopPropagation()}><button className="modal-close" onClick={() => setRecycleOpen(false)}>×</button><span className="eyebrow">RECYCLE BIN</span><h3>Deleted records</h3><p className="recycle-note">Deleted opportunities and follow-up tasks are listed here according to your retention policy.</p><div className="recycle-section"><div className="recycle-section-heading"><strong>Pipeline cards</strong><span>{deletedPipelines.length}</span></div>{deletedPipelines.length ? deletedPipelines.map((record, index) => <div className="recycle-row" key={String(record.id || index)}><div><b>{deletedLabel(record, 'Deleted pipeline')}</b><small>{deletedDate(record) || 'Deleted record'}</small></div><code>{record.id || '—'}</code></div>) : <p className="recycle-empty">No deleted pipeline cards found.</p>}</div><div className="recycle-section"><div className="recycle-section-heading"><strong>Tasks</strong><span>{deletedTasks.length}</span></div>{deletedTasks.length ? deletedTasks.map((record, index) => <div className="recycle-row" key={String(record.id || index)}><div><b>{deletedLabel(record, 'Deleted task')}</b><small>{deletedDate(record) || 'Deleted record'}</small></div><code>{record.id || '—'}</code></div>) : <p className="recycle-empty">No deleted tasks found.</p>}</div></article></div>}
  </section>
}
