import React, { useEffect, useState } from 'react';
import { call } from '../shared/api';
import { LookupField } from './Records';

export const newDocumentLine = () => ({ item_id: '', item_name: '', description: '', quantity: 1, rate: '', unit: '', discount: '', account_id: '', location_id: '', hsn_or_sac: '', tax_id: '', project_id: '', tag_id: '', tag_option_id: '', custom_value: '', stock_on_hand: null, available_stock: null });
const display = value => value?.name || value?.location_name || value?.project_name || value?.tax_name || value?.tag_name || value?.option_name || value?.account_name || value?.currency_name_formatted || value?.id || 'Unnamed';
function Select({ value, onChange, children, required = false }) { return <select value={value ?? ''} onChange={event => onChange(event.target.value)} required={required}>{children}</select>; }
function Field({ label, required = false, children }) { return <label className="document-field"><span>{label} <small>{required ? 'Required' : 'Optional'}</small></span>{children}</label>; }

export default function DocumentLines({ lines, setLines, items, lookupItems, options, includeAccount = false, purchaseMode = false, itemDiscounts = false }) {
  const [tagOptions, setTagOptions] = useState({});
  const update = (index, key, value) => setLines(current => current.map((line, lineIndex) => lineIndex === index ? { ...line, [key]: value } : line));
  const selectItem = (index, item) => setLines(current => current.map((line, lineIndex) => lineIndex === index ? {
    ...line, item_id: item.item_id, item_name: item.name || item.item_name || '',
    rate: line.rate || (purchaseMode ? item.purchase_rate : item.sales_rate) || item.rate || '',
    unit: line.unit || item.unit || '', account_id: line.account_id || (purchaseMode ? item.purchase_account_id : item.account_id) || '',
    tax_id: line.tax_id || item.tax_id || '', hsn_or_sac: line.hsn_or_sac || item.hsn_or_sac || '',
    stock_on_hand: item.stock_on_hand, available_stock: item.available_stock
  } : line));

  useEffect(() => {
    options.reporting_tags.forEach(async tag => {
      const tagId = tag.tag_id || tag.id;
      if (!tagId || tagOptions[tagId]) return;
      try {
        const result = await call(`/purchase-orders/options/tag-options?tag_id=${encodeURIComponent(tagId)}`);
        setTagOptions(current => ({ ...current, [tagId]: result.options || [] }));
      } catch { /* The tag remains selectable; Zoho will validate an unavailable option. */ }
    });
  }, [options.reporting_tags, tagOptions]);

  return <section className="line-items-editor">
    <div className="form-section-heading"><h4>Products and quantities</h4><span>Product, quantity, and rate are required. Other line fields are optional unless selected.</span></div>
    {lines.map((line, index) => <div className="line-item" key={index}>
      <div className="line-item-heading"><strong>Product {index + 1}</strong>{lines.length > 1 && <button type="button" className="link-button" onClick={() => setLines(current => current.filter((_, lineIndex) => lineIndex !== index))}>Remove</button>}</div>
      <div className="line-item-row">
        <div className="line-item-search"><Field label="Product" required><LookupField required placeholder="Search product name, SKU, or description" value={line.item_name} selected={line.item_id} options={items} onChange={value => { update(index, 'item_name', value); update(index, 'item_id', ''); }} onSearch={() => lookupItems(line.item_name)} renderOption={item => <button type="button" key={item.item_id} onClick={() => selectItem(index, item)}>{item.name || item.item_name}{item.stock_on_hand != null ? ` - Stock: ${item.stock_on_hand}` : ''}</button>} /></Field></div>
        <Field label="Quantity" required><input required type="number" min="1" value={line.quantity} onChange={event => update(index, 'quantity', event.target.value)} /></Field>
        <Field label="Rate" required><input required type="number" min="0" step="0.01" value={line.rate} onChange={event => update(index, 'rate', event.target.value)} /></Field>
      </div>
      <div className="document-form-grid">
        <Field label="Description"><input value={line.description} onChange={event => update(index, 'description', event.target.value)} /></Field>
        <Field label="Unit"><input value={line.unit} onChange={event => update(index, 'unit', event.target.value)} /></Field>
        {itemDiscounts && <Field label="Line discount" required><input required value={line.discount} onChange={event => update(index, 'discount', event.target.value)} placeholder="Amount or %" /></Field>}
        {includeAccount && <Field label="Account" required><Select value={line.account_id} onChange={value => update(index, 'account_id', value)} required><option value="">Select account</option>{options.accounts.map(account => <option key={account.account_id} value={account.account_id}>{display(account)}</option>)}</Select></Field>}
        <Field label="Location"><Select value={line.location_id} onChange={value => update(index, 'location_id', value)}><option value="">No location</option>{options.locations.map(location => <option key={location.location_id} value={location.location_id}>{display(location)}</option>)}</Select></Field>
        <Field label="HSN/SAC"><input value={line.hsn_or_sac} onChange={event => update(index, 'hsn_or_sac', event.target.value)} /></Field>
        <Field label="Tax"><Select value={line.tax_id} onChange={value => update(index, 'tax_id', value)}><option value="">No tax</option>{options.taxes.map(tax => <option key={tax.tax_id || tax.id} value={tax.tax_id || tax.id}>{display(tax)}</option>)}</Select></Field>
        <Field label="Project"><Select value={line.project_id} onChange={value => update(index, 'project_id', value)}><option value="">No project</option>{options.projects.map(project => <option key={project.project_id || project.id} value={project.project_id || project.id}>{display(project)}</option>)}</Select></Field>
        <Field label="Reporting tag"><Select value={line.tag_id} onChange={value => { update(index, 'tag_id', value); update(index, 'tag_option_id', ''); }}><option value="">No tag</option>{options.reporting_tags.map(tag => <option key={tag.tag_id || tag.id} value={tag.tag_id || tag.id}>{display(tag)}</option>)}</Select></Field>
        {line.tag_id && <Field label="Tag option" required><Select value={line.tag_option_id} onChange={value => update(index, 'tag_option_id', value)} required><option value="">Select tag option</option>{(tagOptions[line.tag_id] || []).map(option => <option key={option.option_id || option.tag_option_id || option.id} value={option.option_id || option.tag_option_id || option.id}>{display(option)}</option>)}</Select></Field>}
        <Field label="Custom field value"><input value={line.custom_value} onChange={event => update(index, 'custom_value', event.target.value)} /></Field>
      </div>
      <div className="inventory-hint">{line.item_id ? `Inventory available: ${line.available_stock ?? line.stock_on_hand ?? 'Not provided'}` : 'Search and select a product to view inventory.'}</div>
    </div>)}
    <button type="button" className="add-line-button" onClick={() => setLines(current => [...current, newDocumentLine()])}>+ Add another product</button>
  </section>;
}
