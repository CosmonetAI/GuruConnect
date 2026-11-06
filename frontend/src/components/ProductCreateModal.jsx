import React, { useMemo, useState } from 'react';
import { call } from '../zoho/shared/api';

const blank = { name: '', sku: '', rate: '', purchase_rate: '', unit: 'pcs', description: '', item_type: 'sales' };
const template = 'name,sku,rate,purchase_rate,unit,description,item_type\nAxial Cooling Fan,DEMO-SKU-001,1000,800,pcs,Demo item,sales\n';

function parseCsvLine(line) {
  const values = []; let value = ''; let quoted = false;
  for (let index = 0; index < line.length; index += 1) {
    const character = line[index];
    if (character === '"') {
      if (quoted && line[index + 1] === '"') { value += '"'; index += 1; }
      else quoted = !quoted;
    } else if (character === ',' && !quoted) { values.push(value.trim()); value = ''; }
    else value += character;
  }
  values.push(value.trim());
  return values;
}

function parseCsv(text) {
  const lines = text.trim().split(/\r?\n/).filter(Boolean);
  if (lines.length < 2) throw new Error('CSV must include a header row and at least one item.');
  const headers = parseCsvLine(lines[0]).map(value => value.toLowerCase());
  if (!headers.includes('name') || !headers.includes('rate')) throw new Error('CSV must include name and rate columns.');
  return lines.slice(1).map(line => {
    const values = parseCsvLine(line);
    return headers.reduce((row, header, index) => { row[header] = (values[index] || '').trim(); return row; }, {});
  });
}

export default function ProductCreateModal({ onClose, onCreated, setError }) {
  const [mode, setMode] = useState('single'); const [form, setForm] = useState(blank); const [file, setFile] = useState(null); const [busy, setBusy] = useState(false); const [result, setResult] = useState(null);
  const canSubmit = mode === 'single' ? form.name.trim() && form.rate !== '' : file;
  const title = useMemo(() => mode === 'single' ? 'Add single item' : 'Bulk upload items', [mode]);
  const submit = async event => {
    event.preventDefault(); if (!canSubmit || busy) return; setBusy(true); setError(''); setResult(null);
    try {
      if (mode === 'single') {
        const response = await call('/products', { method: 'POST', body: JSON.stringify(form) });
        setResult({ message: `Item ${response.item?.name || form.name} created in Zoho Books.` });
      } else {
        const response = await call('/products/bulk', { method: 'POST', timeoutMs: 120000, body: JSON.stringify({ items: parseCsv(await file.text()) }) });
        setResult({ message: `${response.created_count} item${response.created_count === 1 ? '' : 's'} created. ${response.failed_count} failed.`, failed: response.failed });
      }
      onCreated?.();
    } catch (error) { setError(error.message); } finally { setBusy(false); }
  };
  return <div className="modal-backdrop" onClick={onClose}><article className="contact-modal product-create-modal" onClick={event => event.stopPropagation()}>
    <div className="modal-heading"><div><small>ZOHO BOOKS ITEMS</small><h3>{title}</h3></div><button type="button" onClick={onClose} aria-label="Close">×</button></div>
    <p>Bulk upload sends one Zoho Books item request per CSV row and reports partial failures.</p>
    <div className="item-create-tabs"><button type="button" className={mode === 'single' ? 'active' : ''} onClick={() => setMode('single')}>Add single item</button><button type="button" className={mode === 'bulk' ? 'active' : ''} onClick={() => setMode('bulk')}>Upload bulk CSV</button></div>
    <form onSubmit={submit}>{mode === 'single' ? <div className="contact-form-grid"><input required placeholder="Item name *" value={form.name} onChange={event => setForm({ ...form, name: event.target.value })} /><input placeholder="SKU" value={form.sku} onChange={event => setForm({ ...form, sku: event.target.value })} /><input required type="number" min="0" step="0.01" placeholder="Selling rate *" value={form.rate} onChange={event => setForm({ ...form, rate: event.target.value })} /><input type="number" min="0" step="0.01" placeholder="Purchase rate" value={form.purchase_rate} onChange={event => setForm({ ...form, purchase_rate: event.target.value })} /><input placeholder="Unit" value={form.unit} onChange={event => setForm({ ...form, unit: event.target.value })} /><select value={form.item_type} onChange={event => setForm({ ...form, item_type: event.target.value })}><option value="sales">Sales item</option><option value="purchases">Purchase item</option><option value="sales_and_purchases">Sales and purchase item</option></select><textarea className="product-create-description" placeholder="Description" value={form.description} onChange={event => setForm({ ...form, description: event.target.value })} /></div> : <div className="bulk-upload-box"><a download="zoho-books-items-template.csv" href={`data:text/csv;charset=utf-8,${encodeURIComponent(template)}`}>Download CSV template</a><input required type="file" accept=".csv,text/csv" onChange={event => setFile(event.target.files?.[0] || null)} /><small>Required columns: name, rate. Optional: sku, purchase_rate, unit, description, item_type.</small></div>}{result && <div className="product-create-result" role="status">{result.message}{result.failed?.length ? <ul>{result.failed.map(item => <li key={item.row}>Row {item.row}: {item.error}</li>)}</ul> : null}</div>}<div className="modal-actions"><button type="button" onClick={onClose}>Close</button><button className="primary" type="submit" disabled={!canSubmit || busy}>{busy ? 'Creating in Zoho…' : 'Create item'}</button></div></form>
  </article></div>;
}
