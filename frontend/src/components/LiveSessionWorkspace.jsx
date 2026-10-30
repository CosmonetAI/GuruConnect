import { useEffect, useRef, useState } from 'react';
import {
  ArrowRight, Check, ChevronDown, ChevronUp, Download, FileText, MessageSquare,
  Minus, Package, Plus, Search, ShoppingCart, Sparkles, Trash2, X,
} from 'lucide-react';
import '../styles/live-session-workspace.css';

const number = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
const productName = (item) => item.name || item.item_name || item.product || 'Catalogue item';
const itemKey = (item) => item.item_id || item.id || item.sku;
const initials = (name) => String(name || '?').split(/\s+/).map((part) => part[0]).slice(0, 2).join('').toUpperCase();
const intentLabel = (intent) => typeof intent === 'string' ? intent : intent.label || intent.keyword || intent.term || intent.product || intent.name || intent.query || '';
const detailText = (value) => typeof value === 'string' || typeof value === 'number' ? String(value) : value?.label || value?.text || value?.message || value?.summary || value?.field || value?.name || '';
const detailList = (value) => Array.isArray(value) ? value.map(detailText).filter(Boolean) : value && typeof value === 'object' ? Object.entries(value).map(([field, entry]) => `${field.replaceAll('_', ' ')}: ${detailText(entry)}`).filter((entry) => !entry.endsWith(': ')) : [];
const specLabels = (value) => detailList(value).map((field) => field.replaceAll('_', ' '));

export const productPriceAvailable = (item) => {
  const value = item.rate ?? item.price;
  return (!item.price_status || item.price_status === 'known') && value !== undefined && value !== null && value !== '' && Number.isFinite(Number(value)) && Number(value) > 0;
};
export const productNeedsConfirmation = (item) => (Boolean(item.match_status) && item.match_status !== 'verified') || detailList(item.missing_specs).length > 0 || detailList(item.conflicts).length > 0;
export const productReviewFingerprint = (item) => JSON.stringify([itemKey(item), item.match_status, item.matched_specs, item.missing_specs, item.conflicts]);
export const productStockNotTracked = (item) => {
  const tracking = String(item.track_inventory ?? '').toLowerCase();
  const itemType = String(item.item_type || '').toLowerCase();
  if (['true', 'yes', '1'].includes(tracking) || itemType === 'inventory') return false;
  return ['not_tracked', 'not_applicable'].includes(item.availability_status) || item.stock_tracking_status === 'not_tracked'
    || ['false', 'no', '0'].includes(tracking) || ['sales', 'sales_and_purchases'].includes(itemType)
    || ['service', 'digital_service', 'capital_service'].includes(String(item.product_type || '').toLowerCase());
};
export const productOutOfStock = (item) => {
  if (productStockNotTracked(item)) return false;
  const stock = item.available_stock ?? item.stock_on_hand ?? item.actual_available_stock;
  return item.availability_status === 'out_of_stock' || (!['needs_review', 'unknown'].includes(item.availability_status) && stock !== undefined && stock !== null && stock !== '' && Number.isFinite(Number(stock)) && Number(stock) <= 0);
};

function speaker(turn) {
  const role = String(turn.speaker_role || '').toLowerCase();
  const name = String(turn.speaker_name || '').trim();
  if (['agent', 'salesperson', 'sales_agent', 'sales executive'].includes(role)) return { label: name || 'Sales agent', agent: true };
  if (role === 'customer') return { label: name || 'Customer', agent: false };
  if (name) return { label: name, agent: false };
  const ref = String(turn.speaker_ref || turn.speaker || '').trim();
  const numeric = ref.match(/(?:^|:)speaker[_ -]?(\d+)$/i);
  return { label: numeric ? `Speaker ${Number(numeric[1]) + 1}` : ref.includes(':') ? 'Conversation' : ref || 'Conversation', agent: false };
}

// Progressive publication rebuilds database turn IDs. Keep unchanged utterances
// mounted across versions so they retain their position and do not animate again.
const turnKey = (turn, index) => [turn.start_seconds ?? '', turn.end_seconds ?? '', speaker(turn).label,
  turn.source_text || turn.english_text || '', turn.ordinal ?? index].join('|');

function turnTime(turn, sessionStartedAt) {
  const recorded = turn.recorded_at || (sessionStartedAt && number(turn.start_seconds) >= 0
    ? new Date(new Date(sessionStartedAt).getTime() + number(turn.start_seconds) * 1000) : null);
  if (recorded && !Number.isNaN(new Date(recorded).getTime())) {
    return new Date(recorded).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
  const seconds = Math.max(0, Math.floor(number(turn.start_seconds)));
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
}

function ProductImage({ item, className = '' }) {
  const [failed, setFailed] = useState(false);
  const url = item.image_url || item.image?.url || item.image;
  useEffect(() => setFailed(false), [url]);
  return <div className={`lws-product-image ${className}`}>
    {typeof url === 'string' && url && !failed
      ? <img src={url} alt={productName(item)} loading="lazy" onError={() => setFailed(true)} />
      : <Package size={30} strokeWidth={1.35} aria-hidden="true" />}
  </div>;
}

export function Stock({ item }) {
  if (productStockNotTracked(item)) return <span className="lws-stock not-tracked"><i />Stock not tracked</span>;
  const stock = item.available_stock ?? item.stock_on_hand ?? item.actual_available_stock;
  const known = stock !== undefined && stock !== null && stock !== '' && Number.isFinite(Number(stock));
  const review = ['needs_review', 'unknown'].includes(item.availability_status) || !known;
  const unavailable = productOutOfStock(item);
  return <span className={`lws-stock${unavailable ? ' is-unavailable' : review ? ' needs-review' : ''}`}><i />{unavailable ? 'Out of stock' : review ? 'Confirm stock' : `${number(stock)} in stock`}</span>;
}

export function ProductMatchDetails({ item, showEvidence = false }) {
  const needsConfirmation = productNeedsConfirmation(item);
  const matched = detailList(item.matched_specs);
  const missing = specLabels(item.missing_specs);
  const conflicts = detailList(item.conflicts);
  const evidence = Array.isArray(item.match_evidence) ? item.match_evidence.filter((source) => source?.text) : [];
  return <div className="lws-match-details">
    <span className={`lws-match-badge${item.match_status !== 'verified' || needsConfirmation ? ' needs-review' : ''}`}>{needsConfirmation ? 'Needs confirmation' : item.match_status === 'verified' ? 'Verified match' : 'Match not assessed'}</span>
    {matched.length > 0 && <p className="lws-matched-specs"><strong>Matched:</strong> {matched.join(' · ')}</p>}
    {missing.length > 0 && <p className="lws-missing-specs"><strong>Confirm:</strong> {missing.join(' · ')}</p>}
    {conflicts.length > 0 && <p className="lws-missing-specs"><strong>Conflict:</strong> {conflicts.join(' · ')}</p>}
    {item.match_reason && <p>{detailText(item.match_reason)}</p>}
    {showEvidence && evidence.length > 0 && <details className="lws-match-evidence"><summary>Catalogue evidence</summary><ul>{evidence.map((source, index) => <li key={index}><strong>{String(source.field || 'Catalogue').replaceAll('_', ' ')}:</strong> {source.text}</li>)}</ul></details>}
  </div>;
}

export function ProductCartAction({ item, onAddToCart, className = 'lws-button lws-button-primary' }) {
  const [acknowledged, setAcknowledged] = useState(false);
  const reviewKey = productReviewFingerprint(item);
  useEffect(() => setAcknowledged(false), [reviewKey]);
  const priceAvailable = productPriceAvailable(item);
  const unavailable = productOutOfStock(item);
  const needsConfirmation = productNeedsConfirmation(item);
  const disabled = !itemKey(item) || !onAddToCart || !priceAvailable || unavailable || (needsConfirmation && !acknowledged);
  const label = !priceAvailable ? 'Price unavailable' : unavailable ? 'Out of stock' : 'Add to cart';
  return <div className="lws-product-action">
    {needsConfirmation && <label className="lws-match-acknowledgement"><input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} aria-label={`Review unverified match ${productName(item)}`} /><span>I understand this match needs confirmation</span></label>}
    <button type="button" className={className} aria-label={!priceAvailable ? `Price unavailable for ${productName(item)}` : unavailable ? `Out of stock ${productName(item)}` : `Add to cart ${productName(item)}`} disabled={disabled} onClick={() => { if (!disabled) onAddToCart({ ...item, ...(needsConfirmation ? { match_confirmation_acknowledged: true } : {}) }); }}><ShoppingCart size={14} />{label}</button>
  </div>;
}

function AgentGuidance({ clarificationSuggestions, requirements, currentIntent, agentGuidance }) {
  const suggestions = clarificationSuggestions.filter((suggestion) => suggestion?.question);
  const guidance = (Array.isArray(agentGuidance) ? agentGuidance : agentGuidance ? [agentGuidance] : []).map(detailText).filter(Boolean);
  const assessments = requirements.filter((requirement) => ['no_match', 'lookup_deferred'].includes(requirement.status) || detailList(requirement.missing_specs).length || detailList(requirement.conflicts).length);
  const deferred = assessments.filter((requirement) => requirement.status === 'lookup_deferred').length;
  const intent = detailText(currentIntent);
  if (!suggestions.length && !guidance.length && !assessments.length && !intent) return null;
  return <section className="lws-agent-guidance" aria-label="Agent guidance">
    <div className="lws-guidance-heading"><strong><Sparkles size={13} />Agent guidance</strong><span>For the sales agent</span></div>
    {intent && <p className="lws-current-intent"><strong>Customer intent:</strong> {intent}</p>}
    {guidance.map((message, index) => <p key={`guidance-${index}`}>{message}</p>)}
    {suggestions.length > 0 && <><p className="lws-guidance-caption">Suggested questions to ask the customer</p><ul>{suggestions.map((suggestion, index) => {
      const evidence = Array.isArray(suggestion.evidence) ? detailList(suggestion.evidence).join(' · ') : detailText(suggestion.evidence);
      return <li key={suggestion.id || `${suggestion.requirement_id}-${index}`}><span>{suggestion.question}</span>{evidence && <small>From conversation: {evidence}</small>}</li>;
    })}</ul></>}
    {deferred > 0 && <p className="lws-guidance-caption">{deferred} {deferred === 1 ? 'requirement is' : 'requirements are'} awaiting catalogue lookup.</p>}
    {assessments.length > 0 && <div className="lws-requirement-assessments" aria-label="Requirement assessments">{assessments.map((requirement, index) => <p className="lws-requirement-review" data-assessment-status={requirement.status || 'needs_confirmation'} key={requirement.requirement_id || requirement.id || index}><strong>{intentLabel(requirement) || 'Requirement'}:</strong> {requirement.status === 'lookup_deferred' ? 'Lookup deferred. This requirement has not been checked against the catalogue.' : requirement.status === 'no_match' ? 'No compatible match found in the checked catalogue results.' : detailList(requirement.conflicts).length > 0 ? `Resolve ${detailList(requirement.conflicts).join(' · ')}` : `Confirm ${specLabels(requirement.missing_specs).join(' · ')}`}</p>)}</div>}
  </section>;
}

function Conversation({ transcript, messages, sessionStartedAt, recordingState }) {
  const list = useRef(null);
  const stick = useRef(true);
  const turns = transcript.length ? transcript : messages.map((message, index) => ({
    ...message, id: message.id || `message-${index}`, english_text: message.content,
    speaker_ref: message.role === 'assistant' ? 'Assistant' : message.role === 'user' ? 'User' : 'Conversation',
    recorded_at: message.created_at,
  }));
  useEffect(() => {
    if (stick.current && list.current) list.current.scrollTop = list.current.scrollHeight;
  }, [turns.length, turns.at(-1)?.english_text, turns.at(-1)?.source_text]);
  const active = recordingState?.capture === 'recording';
  return <section className="lws-conversation lws-panel" aria-label="Live translated conversation">
    <div className="lws-panel-heading"><h3><MessageSquare size={17} />Conversation</h3><span className="lws-subtle">{active ? <><i className="lws-live-dot" />Listening</> : 'Customer conversation'}</span></div>
    <div className="lws-bubbles" ref={list} onScroll={() => { if (list.current) stick.current = list.current.scrollHeight - list.current.scrollTop - list.current.clientHeight < 70; }} aria-live="polite" aria-relevant="additions text">
      {turns.length ? turns.map((turn, index) => {
        const voice = speaker(turn);
        return <article className={`lws-message${voice.agent ? ' is-agent' : ''}`} key={turnKey(turn, index)}>
          <span className="lws-avatar" aria-hidden="true">{voice.agent ? 'SA' : initials(voice.label)}</span>
          <div className="lws-message-body"><div className="lws-message-meta"><strong>{voice.label}</strong><time>{turnTime(turn, sessionStartedAt)}</time></div><p>{turn.english_text || turn.source_text}</p>{!turn.english_text && turn.source_text && <span className="lws-translation-pending">Translation pending</span>}</div>
        </article>;
      }) : <div className="lws-empty lws-conversation-empty"><span className="lws-empty-icon"><MessageSquare size={25} strokeWidth={1.5} /></span><strong>{active ? 'Listening to your conversation' : 'Your conversation, as it happens'}</strong><p>{active ? 'Translated text will appear as each short audio segment is processed.' : 'Start recording below. The conversation and English translation will update here as you speak.'}</p></div>}
    </div>
  </section>;
}

function Transcript({ transcript, onDownload, sessionStartedAt }) {
  const [collapsed, setCollapsed] = useState(true);
  return <section className={`lws-transcript lws-panel${collapsed ? ' is-collapsed' : ''}`} aria-label="Latest transcript">
    <div className="lws-panel-heading"><h3><FileText size={18} />Latest transcript <span className="lws-subtle">· Live updates</span></h3><div className="lws-heading-actions"><button type="button" className="lws-button lws-button-small" onClick={onDownload} disabled={!transcript.length || !onDownload}><Download size={14} /><span>Download transcript</span></button><button type="button" className="lws-icon-button" aria-expanded={!collapsed} aria-label={collapsed ? 'Expand latest transcript' : 'Collapse latest transcript'} onClick={() => setCollapsed(!collapsed)}>{collapsed ? <ChevronDown size={16} /> : <ChevronUp size={16} />}</button></div></div>
    {!collapsed && <div className="lws-transcript-lines">
      {transcript.length ? transcript.map((turn, index) => <p key={turnKey(turn, index)}><time>{turnTime(turn, sessionStartedAt)}</time><span><strong>{speaker(turn).label}:</strong> {turn.english_text || turn.source_text}</span></p>) : <p className="lws-transcript-empty">Completed speech segments and their English translation will appear here.</p>}
    </div>}
  </section>;
}

function Suggestions({ recommendations, intents, onAddToCart, onRemoveIntent, onAddIntent, onViewProduct, onSearchProducts, money, catalogueNotice, catalogueStatus, insightsStatus, clarificationSuggestions, requirements, currentIntent, agentGuidance, compact }) {
  const catalogueState = { syncing: 'Catalogue sync in progress. Product details may still be incomplete.', searching: 'Searching the connected catalogue.', stale: 'Catalogue data needs a refresh. Confirm prices and stock before completing the order.', unavailable: 'Catalogue unavailable. Reconnect the catalogue to verify products, prices and stock.', semantic_unavailable: 'Product similarity search is temporarily unavailable. Confirm catalogue matches before completing the order.' }[catalogueStatus];
  return <section className={`lws-suggestions lws-panel${compact ? ' is-compact' : ''}`} aria-label="Suggested products">
    <div className="lws-panel-heading"><h3><ShoppingCart size={18} />{compact ? 'Recommended products' : 'Suggested products'}</h3>{onSearchProducts && <button className="lws-text-button" type="button" onClick={onSearchProducts}>{compact ? 'View all' : 'Search catalogue'}<ArrowRight size={13} /></button>}</div>
    {(intents.length > 0 || onAddIntent) && <div className="lws-intents" aria-label="Detected product requirements">{intents.map((intent, index) => <span className="lws-intent" key={`${intentLabel(intent)}-${index}`}><Sparkles size={10} />{intentLabel(intent)}{onRemoveIntent && <button type="button" aria-label={`Remove intent ${intentLabel(intent)}`} onClick={() => onRemoveIntent(intent, index)}><X size={11} /></button>}</span>)}{onAddIntent && <button className="lws-add-intent" type="button" onClick={onAddIntent}><Plus size={12} />Add intent</button>}</div>}
    {catalogueNotice && <p className="lws-catalogue-notice" role="status">{catalogueNotice}</p>}
    {catalogueState && <p className="lws-catalogue-notice" role="status">{catalogueState}</p>}
    {(insightsStatus?.detail || insightsStatus?.label) && ['running', 'pending', 'translating', 'failed', 'unavailable'].includes(insightsStatus.key) && <p className={`lws-analysis-state${['failed', 'unavailable'].includes(insightsStatus.key) ? ' is-error' : ''}`} role="status"><Sparkles size={12} />{insightsStatus.detail || insightsStatus.label}</p>}
    <AgentGuidance {...{ clarificationSuggestions, requirements, currentIntent, agentGuidance }} />
    <div className="lws-product-list">{recommendations.length ? recommendations.map((item, index) => {
      const price = item.rate ?? item.price;
      const priceAvailable = productPriceAvailable(item);
      const features = (Array.isArray(item.features) ? item.features : typeof item.description === 'string' ? item.description.split(/[\n•]/).filter(Boolean) : []).slice(0, 3);
      return <article className="lws-product-card" key={itemKey(item) || `${productName(item)}-${index}`}>
        <ProductImage item={item} /><div className="lws-product-copy"><strong>{productName(item)}</strong><small>{item.sku ? `SKU: ${item.sku}` : item.unit || 'Zoho catalogue'}</small>{compact && features.length > 0 && <ul>{features.map((feature, i) => <li key={i}>{feature}</li>)}</ul>}<ProductMatchDetails item={item} /><Stock item={item} /><b className={priceAvailable ? '' : 'lws-price-review'}>{priceAvailable ? money(price) : 'Confirm price'}</b>{onViewProduct && <button className="lws-text-button lws-product-details" type="button" onClick={() => onViewProduct(item)}>{priceAvailable ? 'View details' : 'Review price details'}</button>}<ProductCartAction item={item} onAddToCart={onAddToCart} /></div>
      </article>;
    }) : <div className="lws-empty lws-products-empty"><span className="lws-empty-icon"><Sparkles size={22} strokeWidth={1.5} /></span><strong>{intents.length ? 'Finding catalogue matches' : 'Suggestions that follow the conversation'}</strong><p>{intents.length ? 'Matching the products and requirements mentioned with your connected Zoho inventory.' : 'Mention a product, specification, or budget. Relevant items from your Zoho catalogue will appear here.'}</p>{onSearchProducts && <button type="button" className="lws-text-button" onClick={onSearchProducts}><Search size={13} />Browse product catalogue</button>}</div>}</div>
  </section>;
}

function Cart({ cart, onUpdateCart, onRemoveCart, onSearchProducts, onCompleteCart, onCartOpenChange, money, taxTotal }) {
  const pricesKnown = cart.every(productPriceAvailable);
  const rows = cart.map((item) => {
    const subtotal = Math.max(0, number(item.rate ?? item.price)) * Math.max(1, number(item.quantity, 1));
    const discount = Math.min(subtotal, Math.max(0, item.discount_type === 'fixed' ? number(item.discount) : subtotal * number(item.discount) / 100));
    return { item, subtotal, discount, total: subtotal - discount };
  });
  const subtotal = rows.reduce((sum, row) => sum + row.subtotal, 0);
  const discount = rows.reduce((sum, row) => sum + row.discount, 0);
  const taxKnown = taxTotal !== undefined && taxTotal !== null;
  const tax = taxKnown ? Math.max(0, number(taxTotal)) : 0;
  return <section className="lws-cart lws-panel" aria-label="Customer cart" tabIndex={0}>
    <div className="lws-panel-heading"><h3><ShoppingCart size={25} />Cart <span>· {cart.length} {cart.length === 1 ? 'item' : 'items'}</span></h3><div className="lws-heading-actions">{onSearchProducts && <button className="lws-button" type="button" onClick={onSearchProducts}><Plus size={16} />Add another product</button>}{!cart.length && onCartOpenChange && <button type="button" className="lws-icon-button" onClick={() => onCartOpenChange(false)} aria-label="Close empty cart"><X size={17} /></button>}</div></div>
    {rows.length ? <><div className="lws-cart-table" role="table" aria-label="Cart items"><div className="lws-cart-table-header" role="row"><span role="columnheader">Product</span><span role="columnheader">Unit price</span><span role="columnheader">Quantity</span><span role="columnheader">Discount</span><span role="columnheader">Line total</span><span className="lws-sr-only" role="columnheader">Remove</span></div>{rows.map(({ item, discount: saving, total }, index) => <div className="lws-cart-row" role="row" key={`${itemKey(item) || productName(item)}-${index}`}>
      <div className="lws-cart-product" role="cell"><ProductImage item={item} /><div><strong>{productName(item)}</strong><small>{item.sku ? `SKU: ${item.sku}` : item.unit || 'Catalogue item'}</small><ProductMatchDetails item={item} /><Stock item={item} /></div></div>
      <strong className="lws-cart-unit" role="cell">{productPriceAvailable(item) ? money(item.rate ?? item.price) : 'Confirm price'}</strong>
      <div className="lws-quantity" role="cell"><button type="button" aria-label={`Decrease quantity of ${productName(item)}`} onClick={() => onUpdateCart?.(index, 'quantity', Math.max(1, number(item.quantity, 1) - 1))} disabled={number(item.quantity, 1) <= 1}><Minus size={12} /></button><input type="number" min="1" step="1" inputMode="numeric" aria-label={`Quantity of ${productName(item)}`} value={item.quantity ?? 1} onChange={(event) => onUpdateCart?.(index, 'quantity', Math.max(1, Math.floor(number(event.target.value, 1))))} /><button type="button" aria-label={`Increase quantity of ${productName(item)}`} onClick={() => onUpdateCart?.(index, 'quantity', number(item.quantity, 1) + 1)}><Plus size={12} /></button></div>
      <div className="lws-discount" role="cell"><span className="lws-discount-caption">Line discount</span><div><input type="number" min="0" max={item.discount_type === 'fixed' ? undefined : '100'} step="any" aria-label={`Discount for ${productName(item)}`} value={item.discount ?? 0} onChange={(event) => onUpdateCart?.(index, 'discount', Math.max(0, Math.min(item.discount_type === 'fixed' ? Infinity : 100, number(event.target.value))))} /><select aria-label={`Discount type for ${productName(item)}`} value={item.discount_type || 'percentage'} onChange={(event) => onUpdateCart?.(index, 'discount_type', event.target.value)}><option value="percentage">%</option><option value="fixed">₹</option></select></div>{saving > 0 && <small>− {money(saving)}</small>}</div>
      <strong className="lws-line-total" role="cell">{productPriceAvailable(item) ? money(total) : 'Confirm price'}</strong><button className="lws-icon-button lws-remove-item" type="button" aria-label={`Remove ${productName(item)} from cart`} onClick={() => onRemoveCart?.(index)}><Trash2 size={16} /></button>
    </div>)}</div><div className="lws-cart-bottom">{onSearchProducts && <button type="button" className="lws-button lws-another-product" onClick={onSearchProducts}><Plus size={17} />Add another product</button>}<div className="lws-order-totals"><div><span>Subtotal ({cart.length} {cart.length === 1 ? 'item' : 'items'})</span><span>{pricesKnown ? money(subtotal) : 'Prices incomplete'}</span></div><div className="lws-order-discount"><span>Discount</span><span>{pricesKnown ? `− ${money(discount)}` : 'Pending prices'}</span></div>{taxKnown ? <div><span>Tax</span><span>{money(tax)}</span></div> : <p className="lws-tax-note">Applicable tax is calculated in Zoho Books.</p>}<div className="lws-order-grand-total"><strong>{taxKnown ? 'Total amount' : 'Total before tax'}</strong><strong>{pricesKnown ? money(subtotal - discount + tax) : 'Confirm prices'}</strong></div>{!pricesKnown && <p className="lws-cart-review-note">Confirm catalogue prices before completing this cart.</p>}<button type="button" className="lws-button lws-button-primary lws-complete-cart" onClick={onCompleteCart} disabled={!onCompleteCart || !pricesKnown}>Complete cart<ArrowRight size={17} /></button></div></div></> : <div className="lws-empty"><ShoppingCart size={32} strokeWidth={1.25} /><strong>Your cart is ready</strong><p>Add a suggested item or search the product catalogue.</p></div>}
  </section>;
}

function Notes({ notes, noteText, onNoteTextChange, onAddNote, noteBusy, noteError }) {
  const [collapsed, setCollapsed] = useState(false);
  return <aside className={`lws-notes lws-panel${collapsed ? ' is-collapsed' : ''}`} aria-label="Internal notes">
    <button className="lws-panel-heading lws-notes-toggle" type="button" aria-expanded={!collapsed} onClick={() => setCollapsed(!collapsed)}><h3>Internal notes <span className="lws-note-count">{notes.length}</span></h3>{collapsed ? <ChevronDown size={16} /> : <ChevronUp size={16} />}</button>
    {!collapsed && <><div className="lws-notes-list">{notes.length ? notes.map((note, index) => {
      const author = note.author_name || note.author?.name || note.author?.display_name || note.member_name || 'Team member';
      const date = note.created_at && new Date(note.created_at);
      return <article className="lws-note" key={note.id || index}><span className="lws-avatar" aria-hidden="true">{initials(author)}</span><div><div className="lws-note-meta"><strong>{author}</strong>{date && !Number.isNaN(date.getTime()) && <time>{date.toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}</time>}</div><p>{note.content || note.text}</p></div></article>;
    }) : <div className="lws-notes-empty"><MessageSquare size={21} strokeWidth={1.4} /><p>Keep customer preferences and next steps together with this session.</p></div>}</div><form className="lws-note-editor" onSubmit={(event) => { event.preventDefault(); if (noteText.trim() && !noteBusy) onAddNote?.(); }}><label htmlFor="live-session-note">Add internal note</label><textarea id="live-session-note" placeholder="Write a note about this customer, next steps, or follow-up…" value={noteText} onChange={(event) => onNoteTextChange?.(event.target.value)} disabled={noteBusy} />{noteError && <p role="alert" className="lws-note-error">{noteError}</p>}<div><span>Internal session notes</span><button type="submit" className="lws-button lws-button-primary" disabled={noteBusy || !noteText.trim() || !onAddNote}>{noteBusy ? 'Saving…' : 'Add note'}</button></div></form></>}
  </aside>;
}

/**
 * @typedef {Object} WorkspaceProps
 * @property {Record<string, any>} [identity]
 * @property {Array<Record<string, any>>} [transcript]
 * @property {Array<Record<string, any>>} [messages]
 * @property {Array<Record<string, any>>} [recommendations]
 * @property {Array<string | Record<string, any>>} [intents]
 * @property {Array<Record<string, any>>} [cart]
 * @property {boolean} [cartOpen]
 * @property {(open: boolean) => void} [onCartOpenChange]
 * @property {(item: any) => void} [onAddToCart]
 * @property {(index: number, key: string, value: any) => void} [onUpdateCart]
 * @property {(index: number) => void} [onRemoveCart]
 * @property {() => void} [onSearchProducts]
 * @property {() => void} [onCompleteCart]
 * @property {Array<Record<string, any>>} [notes]
 * @property {string} [noteText]
 * @property {(text: string) => void} [onNoteTextChange]
 * @property {() => void} [onAddNote]
 * @property {boolean} [noteBusy]
 * @property {string} [noteError]
 * @property {Record<string, any>} [recordingState]
 * @property {Record<string, any>} [recordingStatus]
 * @property {Record<string, any>} [insightsStatus]
 * @property {string} [catalogueNotice]
 * @property {string} [catalogueStatus]
 * @property {Array<Record<string, any>>} [clarificationSuggestions]
 * @property {Array<Record<string, any>>} [requirements]
 * @property {string | Record<string, any>} [currentIntent]
 * @property {string | Record<string, any> | Array<string | Record<string, any>>} [agentGuidance]
 * @property {string} [sessionStartedAt]
 * @property {() => void} [onDownloadTranscript]
 * @property {(intent: any, index: number) => void} [onRemoveIntent]
 * @property {() => void} [onAddIntent]
 * @property {(item: any) => void} [onViewProduct]
 * @property {number} [taxTotal]
 * @property {string} [currencyCode]
 * @property {import('react').ReactNode} [children]
 */

/** @param {WorkspaceProps} props */
export default function LiveSessionWorkspace({
  identity = {}, transcript = [], messages = [], recommendations = [], intents = [], cart = [], cartOpen = false,
  onCartOpenChange, onAddToCart, onUpdateCart, onRemoveCart, onSearchProducts, onCompleteCart,
  notes = [], noteText = '', onNoteTextChange, onAddNote, noteBusy = false, noteError = '',
  recordingState = {}, recordingStatus, insightsStatus, catalogueNotice = '', sessionStartedAt,
  catalogueStatus = '', clarificationSuggestions = [], requirements = [], currentIntent = '', agentGuidance = [],
  onDownloadTranscript, onRemoveIntent, onAddIntent, onViewProduct, taxTotal, currencyCode = 'INR', children,
}) {
  const cartView = cart.length > 0 || cartOpen;
  const money = (value) => { try { return new Intl.NumberFormat('en-IN', { style: 'currency', currency: currencyCode || 'INR', maximumFractionDigits: 2 }).format(number(value)); } catch { return `₹${number(value).toLocaleString('en-IN')}`; } };
  const suggestions = <Suggestions {...{ recommendations, intents, onAddToCart, onRemoveIntent, onAddIntent, onViewProduct, onSearchProducts, money, catalogueNotice, catalogueStatus, insightsStatus, clarificationSuggestions, requirements, currentIntent, agentGuidance }} compact={cartView} />;
  return <div className={`live-session-workspace${cartView ? ' is-cart-view' : ''}`} data-layout={cartView ? 'cart' : 'conversation'} aria-label={`${identity.name || 'Customer'} conversation workspace`}>
    <div className="lws-work-grid">
      <div className="lws-conversation-lane"><Conversation {...{ transcript, messages, sessionStartedAt, recordingState }} />{!cartView && <Transcript transcript={transcript} onDownload={onDownloadTranscript} sessionStartedAt={sessionStartedAt} />}{suggestions}</div>
      <div className="lws-cart-lane" inert={!cartView ? true : undefined} aria-hidden={!cartView}>
        <Cart {...{ cart, onUpdateCart, onRemoveCart, onSearchProducts, onCompleteCart, onCartOpenChange, money, taxTotal }} />
        {cartView && <Transcript transcript={transcript} onDownload={onDownloadTranscript} sessionStartedAt={sessionStartedAt} />}
      </div>
      <Notes {...{ notes, noteText, onNoteTextChange, onAddNote, noteBusy, noteError }} />
    </div>
    {children && <div className="lws-recording-footer">{children}</div>}
    {recordingStatus?.key === 'complete' && <div className="lws-sr-only" role="status"><Check size={12} />{recordingStatus.label || 'Processing complete'}</div>}
  </div>;
}
