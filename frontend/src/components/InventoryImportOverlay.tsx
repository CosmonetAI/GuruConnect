import { useEffect, useMemo, useRef, useState, type ChangeEvent, type PointerEvent } from 'react'
import { Check, ChevronLeft, ChevronRight, Copy, Download, FileSpreadsheet, Maximize2, Minus, Search, UploadCloud, X } from 'lucide-react'
import * as XLSX from 'xlsx'

export type InventorySheet = {
  name: string
  rows: string[][]
  searchIndex: string[]
  columnCount: number
}

export type InventoryImportData = {
  fileName: string
  fileType: string
  fileBytes: ArrayBuffer | null
  sheets: InventorySheet[]
}

type Position = { x: number; y: number }
type Size = { width: number; height: number }
type ResizeDirection = 'n' | 'e' | 's' | 'w' | 'ne' | 'nw' | 'se' | 'sw'

const RESIZE_DIRECTIONS: ResizeDirection[] = ['n', 'e', 's', 'w', 'ne', 'nw', 'se', 'sw']

const PAGE_SIZE = 250

function columnLabel(index: number) {
  let value = index + 1
  let label = ''
  while (value > 0) {
    const remainder = (value - 1) % 26
    label = String.fromCharCode(65 + remainder) + label
    value = Math.floor((value - 1) / 26)
  }
  return label
}

function toCellText(value: unknown) {
  if (value === null || value === undefined) return ''
  return String(value)
}

function parseWorkbook(fileBytes: ArrayBuffer): InventorySheet[] {
  const workbook = XLSX.read(fileBytes, {
    type: 'array',
    cellDates: true,
    cellNF: true,
    cellText: true,
  })

  return workbook.SheetNames.map(name => {
    const sheet = workbook.Sheets[name]
    const rawRows = XLSX.utils.sheet_to_json(sheet, {
      header: 1,
      raw: false,
      defval: '',
      blankrows: true,
    }) as unknown[][]
    const rows = rawRows.map(row => row.map(toCellText))
    const columnCount = rows.reduce((highest, row) => Math.max(highest, row.length), 0)
    const normalizedRows = rows.map(row => Array.from({ length: columnCount }, (_, index) => row[index] ?? ''))

    return {
      name,
      rows: normalizedRows,
      searchIndex: normalizedRows.map(row => row.join('\u0001').toLocaleLowerCase()),
      columnCount,
    }
  })
}

function copyToClipboard(value: string) {
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(value)
  const helper = document.createElement('textarea')
  helper.value = value
  helper.style.position = 'fixed'
  helper.style.opacity = '0'
  document.body.appendChild(helper)
  helper.select()
  document.execCommand('copy')
  helper.remove()
  return Promise.resolve()
}

export default function InventoryImportOverlay({ data, onDataChange, onClose }: { data: InventoryImportData; onDataChange: (next: InventoryImportData) => void; onClose: () => void }) {
  const { fileName, sheets } = data
  const [activeSheet, setActiveSheet] = useState(0)
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [copiedKey, setCopiedKey] = useState('')
  const [minimized, setMinimized] = useState(false)
  const [size, setSize] = useState<Partial<Size>>({})
  const [position, setPosition] = useState<Position>(() => ({ x: Math.max(10, (window.innerWidth - Math.min(1180, window.innerWidth - 36)) / 2), y: Math.max(10, (window.innerHeight - 720) / 2) }))
  const panelRef = useRef<HTMLElement | null>(null)
  const searchInputRef = useRef<HTMLInputElement | null>(null)
  const dragRef = useRef<{ offsetX: number; offsetY: number } | null>(null)
  const resizeRef = useRef<{ direction: ResizeDirection; startX: number; startY: number; startPosition: Position; startSize: Size } | null>(null)

  const currentSheet = sheets[activeSheet]
  const normalizedQuery = query.trim().toLocaleLowerCase()
  const matchingRows = useMemo(() => {
    if (!currentSheet) return []
    if (!normalizedQuery) return currentSheet.rows.map((_, index) => index)
    return currentSheet.searchIndex.reduce<number[]>((matches, text, index) => {
      if (text.includes(normalizedQuery)) matches.push(index)
      return matches
    }, [])
  }, [currentSheet, normalizedQuery])

  const totalMatches = useMemo(() => {
    if (!normalizedQuery) return 0
    return sheets.reduce((total, sheet) => total + sheet.searchIndex.reduce((count, text) => count + (text.includes(normalizedQuery) ? 1 : 0), 0), 0)
  }, [normalizedQuery, sheets])

  const totalPages = Math.max(1, Math.ceil(matchingRows.length / PAGE_SIZE))
  const visibleRows = matchingRows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)

  useEffect(() => {
    setPage(0)
  }, [activeSheet, normalizedQuery])

  useEffect(() => {
    const panel = panelRef.current
    if (!panel) return
    setPosition({
      x: Math.max(10, (window.innerWidth - panel.offsetWidth) / 2),
      y: Math.max(10, (window.innerHeight - panel.offsetHeight) / 2),
    })
  }, [])

  useEffect(() => {
    function resize(event: globalThis.PointerEvent) {
      const active = resizeRef.current
      if (!active) return
      const dx = event.clientX - active.startX
      const dy = event.clientY - active.startY
      const minWidth = Math.min(520, window.innerWidth - 20)
      const minHeight = minimized ? 66 : 360
      let width = active.startSize.width
      let height = active.startSize.height
      let x = active.startPosition.x
      let y = active.startPosition.y
      if (active.direction.includes('e')) width = Math.max(minWidth, active.startSize.width + dx)
      if (active.direction.includes('s')) height = Math.max(minHeight, active.startSize.height + dy)
      if (active.direction.includes('w')) { width = Math.max(minWidth, active.startSize.width - dx); x = active.startPosition.x + active.startSize.width - width }
      if (active.direction.includes('n')) { height = Math.max(minHeight, active.startSize.height - dy); y = active.startPosition.y + active.startSize.height - height }
      width = Math.min(width, window.innerWidth - 20)
      height = Math.min(height, window.innerHeight - 20)
      x = Math.max(10, Math.min(window.innerWidth - width - 10, x))
      y = Math.max(10, Math.min(window.innerHeight - height - 10, y))
      setSize({ width, height })
      setPosition({ x, y })
    }
    function endResize() { resizeRef.current = null }
    window.addEventListener('pointermove', resize)
    window.addEventListener('pointerup', endResize)
    return () => { window.removeEventListener('pointermove', resize); window.removeEventListener('pointerup', endResize) }
  }, [minimized])

  useEffect(() => {
    if (!normalizedQuery || !sheets.length || currentSheet?.searchIndex.some(text => text.includes(normalizedQuery))) return
    const firstMatch = sheets.findIndex(sheet => sheet.searchIndex.some(text => text.includes(normalizedQuery)))
    if (firstMatch >= 0 && firstMatch !== activeSheet) setActiveSheet(firstMatch)
  }, [activeSheet, currentSheet, normalizedQuery, sheets])

  useEffect(() => {
    const handleKeyboard = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
      if ((event.ctrlKey || event.metaKey) && event.key.toLocaleLowerCase() === 'k') {
        event.preventDefault()
        searchInputRef.current?.focus()
      }
    }
    document.addEventListener('keydown', handleKeyboard)
    return () => document.removeEventListener('keydown', handleKeyboard)
  }, [onClose])

  function clampPosition(next: Position) {
    const panel = panelRef.current
    const width = panel?.offsetWidth ?? Math.min(1180, window.innerWidth - 36)
    const height = panel?.offsetHeight ?? (minimized ? 76 : Math.min(760, window.innerHeight - 100))
    return {
      x: Math.max(10, Math.min(window.innerWidth - width - 10, next.x)),
      y: Math.max(10, Math.min(window.innerHeight - height - 10, next.y)),
    }
  }

  function beginDrag(event: PointerEvent<HTMLDivElement>) {
    if ((event.target as HTMLElement).closest('button')) return
    const panel = panelRef.current
    if (!panel) return
    const rect = panel.getBoundingClientRect()
    dragRef.current = { offsetX: event.clientX - rect.left, offsetY: event.clientY - rect.top }
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  function drag(event: PointerEvent<HTMLDivElement>) {
    if (!dragRef.current) return
    setPosition(clampPosition({ x: event.clientX - dragRef.current.offsetX, y: event.clientY - dragRef.current.offsetY }))
  }

  function endDrag() {
    dragRef.current = null
  }

  function beginResize(event: PointerEvent<HTMLDivElement>, direction: ResizeDirection) {
    event.preventDefault()
    event.stopPropagation()
    const panel = panelRef.current
    if (!panel) return
    const rect = panel.getBoundingClientRect()
    resizeRef.current = { direction, startX: event.clientX, startY: event.clientY, startPosition: { x: rect.left, y: rect.top }, startSize: { width: rect.width, height: rect.height } }
  }

  async function importFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.currentTarget.value = ''
    if (!file) return
    setBusy(true)
    setError('')
    try {
      const fileBytes = await file.arrayBuffer()
      const importedSheets = parseWorkbook(fileBytes)
      onDataChange({ sheets: importedSheets, fileName: file.name, fileType: file.type, fileBytes })
      setActiveSheet(0)
      setQuery('')
      setPage(0)
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'This workbook could not be read.')
    } finally {
      setBusy(false)
    }
  }

  async function copyCell(value: string, key: string) {
    try {
      await copyToClipboard(value)
      setCopiedKey(key)
      window.setTimeout(() => setCopiedKey(current => current === key ? '' : current), 1400)
    } catch {
      setError('Copy failed. Your browser did not allow clipboard access.')
    }
  }

  function downloadOriginalFile() {
    if (!data.fileBytes || !data.fileName) return
    const blob = new Blob([data.fileBytes], { type: data.fileType || 'application/octet-stream' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = data.fileName
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  const sheetMatchCount = (sheet: InventorySheet) => normalizedQuery ? sheet.searchIndex.reduce((count, text) => count + (text.includes(normalizedQuery) ? 1 : 0), 0) : sheet.rows.length

  return <div className="inventory-overlay-layer">
    <section
      ref={panelRef}
      className={`inventory-window${minimized ? ' inventory-window-minimized' : ''}`}
      role="dialog"
      aria-modal="false"
      aria-labelledby="inventory-window-title"
      style={{ left: position.x, top: position.y, width: size.width, height: size.height }}
    >
      <div className="inventory-window-header" onPointerDown={beginDrag} onPointerMove={drag} onPointerUp={endDrag} onPointerCancel={endDrag}>
        <div className="inventory-window-title"><FileSpreadsheet size={18} /><div><strong id="inventory-window-title">Quick see Excel</strong><span>{fileName ? `${fileName} · tab session` : 'Attach an Excel or CSV workbook to view it here'}</span></div></div>
        <div className="inventory-window-actions">
          <button type="button" aria-label={minimized ? 'Expand inventory window' : 'Minimize inventory window'} title={minimized ? 'Expand' : 'Minimize'} onClick={() => setMinimized(value => !value)}>{minimized ? <Maximize2 size={16} /> : <Minus size={16} />}</button>
          <button type="button" aria-label="Close inventory window" title="Close window" onClick={onClose}><X size={17} /></button>
        </div>
      </div>
      {!minimized && <div className="inventory-window-body">
        <div className="inventory-toolbar">
          {sheets.length > 0 && <label className="inventory-file-button"><UploadCloud size={16} />{busy ? 'Reading workbook…' : 'Replace workbook'}<input type="file" accept=".xlsx,.xlsm,.xls,.csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel,text/csv" onChange={event => void importFile(event)} disabled={busy} /></label>}
          {sheets.length > 0 && data.fileBytes && <button type="button" className="inventory-download-button" onClick={downloadOriginalFile}><Download size={15} />Download original</button>}
          {sheets.length > 0 && <label className="inventory-search"><Search size={16} /><input ref={searchInputRef} type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Search all imported data" aria-label="Search all imported data" /><kbd>Ctrl K</kbd></label>}
        </div>
        {error && <p role="alert" className="inventory-error">{error}</p>}
        {sheets.length === 0 && !busy && <div className="inventory-empty"><FileSpreadsheet size={32} /><h3>View your offline documents in this window</h3><p>Import .xlsx, .xlsm, .xls, or .csv. The file stays in this browser tab until you refresh or close the tab.</p><label className="inventory-file-button inventory-empty-upload"><UploadCloud size={16} />Choose Excel or CSV<input type="file" accept=".xlsx,.xlsm,.xls,.csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel,text/csv" onChange={event => void importFile(event)} disabled={busy} /></label></div>}
        {sheets.length > 0 && <>
          <div className="inventory-summary"><span><strong>{sheets.length}</strong> sheet{sheets.length === 1 ? '' : 's'} · <strong>{currentSheet?.rows.length.toLocaleString() ?? 0}</strong> rows on {currentSheet?.name}</span>{normalizedQuery ? <span><strong>{totalMatches.toLocaleString()}</strong> matching rows across all sheets</span> : <span>Search scans every imported sheet</span>}</div>
          <div className="inventory-sheet-tabs" role="tablist" aria-label="Imported workbook sheets">{sheets.map((sheet, index) => <button type="button" role="tab" aria-selected={index === activeSheet} className={index === activeSheet ? 'selected' : ''} key={sheet.name} onClick={() => setActiveSheet(index)}>{sheet.name}<span>{sheetMatchCount(sheet).toLocaleString()}</span></button>)}</div>
          {currentSheet?.rows.length === 0 || currentSheet?.columnCount === 0 ? <div className="inventory-empty inventory-empty-small"><h3>This sheet is empty</h3><p>Select another sheet or import a different workbook.</p></div> : <>
            <div className="inventory-grid-wrap">
              <table className="inventory-grid"><thead><tr><th className="inventory-row-number" aria-label="Row number">#</th>{Array.from({ length: currentSheet.columnCount }, (_, index) => <th key={index}>{columnLabel(index)}</th>)}</tr></thead><tbody>{visibleRows.map(rowIndex => <tr key={rowIndex}><th className="inventory-row-number">{rowIndex + 1}</th>{currentSheet.rows[rowIndex].map((value, columnIndex) => { const copyKey = `${activeSheet}-${rowIndex}-${columnIndex}`; return <td key={columnIndex} title={value}><span className="inventory-cell-value">{value}</span><button type="button" className="inventory-copy-cell" aria-label={`Copy ${columnLabel(columnIndex)}${rowIndex + 1}`} title="Copy cell" onClick={() => void copyCell(value, copyKey)}>{copiedKey === copyKey ? <Check size={13} /> : <Copy size={13} />}</button></td> })}</tr>)}</tbody></table>
              {visibleRows.length === 0 && <div className="inventory-no-results">No rows match “{query}” on this sheet. Try another sheet or clear the search.</div>}
            </div>
            <div className="inventory-pagination"><span>{matchingRows.length ? `Showing ${(page * PAGE_SIZE + 1).toLocaleString()}–${Math.min((page + 1) * PAGE_SIZE, matchingRows.length).toLocaleString()} of ${matchingRows.length.toLocaleString()}` : 'No matching rows'}</span><div><button type="button" aria-label="Previous rows" disabled={page === 0} onClick={() => setPage(current => Math.max(0, current - 1))}><ChevronLeft size={15} /></button><span>Page {page + 1} of {totalPages}</span><button type="button" aria-label="Next rows" disabled={page >= totalPages - 1} onClick={() => setPage(current => Math.min(totalPages - 1, current + 1))}><ChevronRight size={15} /></button></div></div>
          </>}
        </>}
      </div>}
      {RESIZE_DIRECTIONS.map(direction => <div key={direction} className={`inventory-resize-handle inventory-resize-${direction}`} aria-hidden="true" onPointerDown={event => beginResize(event, direction)} />)}
    </section>
  </div>
}
