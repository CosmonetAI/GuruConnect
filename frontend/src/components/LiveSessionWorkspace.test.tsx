import { useState } from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import LiveSessionWorkspace from './LiveSessionWorkspace';

const fan = { item_id: 'fan', name: 'Quiet ceiling fan', sku: 'FAN-1', rate: 1000, available_stock: 8 };
const motor = { item_id: 'motor', name: 'Water pump motor', sku: 'MOTOR-1', rate: 2500 };

describe('live session workspace', () => {
  it('shows speech once by default and opens the timestamped transcript on demand', () => {
    const download = vi.fn();
    render(<LiveSessionWorkspace transcript={[{ id: 'turn', english_text: 'I need a fan.', start_seconds: 0 }]} onDownloadTranscript={download} />);
    expect(screen.getAllByText('I need a fan.')).toHaveLength(1);
    const transcript = screen.getByRole('region', { name: 'Latest transcript' });
    expect(within(transcript).queryByText('I need a fan.')).not.toBeInTheDocument();
    fireEvent.click(within(transcript).getByRole('button', { name: 'Expand latest transcript' }));
    expect(within(transcript).getByText('I need a fan.', { exact: false })).toBeInTheDocument();
    fireEvent.click(within(transcript).getByRole('button', { name: 'Download transcript' }));
    expect(download).toHaveBeenCalledOnce();
  });
  it('moves to the cart as a real item is added and returns after its removal', () => {
    function Session() {
      const [cart, setCart] = useState<object[]>([]);
      return <LiveSessionWorkspace recommendations={[fan]} cart={cart} onAddToCart={(item: object) => setCart([{ ...item, quantity: 1 }])} onRemoveCart={() => setCart([])} />;
    }
    const { container } = render(<Session />);
    expect(container.querySelector('.live-session-workspace')).toHaveAttribute('data-layout', 'conversation');
    fireEvent.click(screen.getByRole('button', { name: 'Add to cart Quiet ceiling fan' }));
    expect(container.querySelector('.live-session-workspace')).toHaveAttribute('data-layout', 'cart');
    expect(screen.getByRole('region', { name: 'Customer cart' })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Remove Quiet ceiling fan from cart' }));
    expect(container.querySelector('.live-session-workspace')).toHaveAttribute('data-layout', 'conversation');
  });

  it('preserves neutral speaker labels until a role is explicitly supplied', () => {
    render(<LiveSessionWorkspace transcript={[
      { id: 'one', speaker_ref: 'speaker_0', source_text: 'Original words', english_text: 'I need a fan.', start_seconds: 5 },
      { id: 'two', speaker_ref: 'speaker_1', speaker_role: 'agent', english_text: 'Here are the available fans.', start_seconds: 12 },
    ]} />);
    const conversation = screen.getByRole('region', { name: 'Live translated conversation' });
    expect(within(conversation).getByText('Speaker 1')).toBeInTheDocument();
    expect(within(conversation).getByText('Sales agent')).toBeInTheDocument();
    expect(within(conversation).queryByText('Customer')).not.toBeInTheDocument();
    expect(within(conversation).getByText('I need a fan.')).toBeInTheDocument();
    expect(within(conversation).queryByText('Original words')).not.toBeInTheDocument();
  });

  it('uses supplied speaker names and keeps scoped provider references out of labels', () => {
    render(<LiveSessionWorkspace transcript={[
      { id: 'one', speaker_ref: 'run-uuid:window-uuid:speaker_0', speaker_name: 'Akshat', english_text: 'I need a quiet fan.', start_seconds: 2 },
      { id: 'two', speaker_ref: 'run-uuid:window-uuid:speaker_1', english_text: 'There are several options.', start_seconds: 8 },
    ]} />);
    const conversation = screen.getByRole('region', { name: 'Live translated conversation' });
    expect(within(conversation).getByText('Akshat')).toBeInTheDocument();
    expect(within(conversation).getByText('Speaker 2')).toBeInTheDocument();
    expect(conversation).not.toHaveTextContent('run-uuid');
    expect(conversation.querySelector('.is-agent')).not.toBeInTheDocument();
  });

  it('keeps existing utterances mounted when a new transcript version changes database IDs', () => {
    const turn = { id: 'old-version-turn', speaker_ref: 'speaker_0', source_text: 'Original words', english_text: 'I need a fan.', start_seconds: 0, end_seconds: 3 };
    const { rerender } = render(<LiveSessionWorkspace transcript={[turn]} />);
    const message = screen.getByRole('region', { name: 'Live translated conversation' }).querySelector('.lws-message');
    rerender(<LiveSessionWorkspace transcript={[{ ...turn, id: 'new-version-turn' }]} />);
    expect(screen.getByRole('region', { name: 'Live translated conversation' }).querySelector('.lws-message')).toBe(message);
  });

  it('calculates percentage and fixed discounts per line without inventing tax', () => {
    render(<LiveSessionWorkspace cart={[
      { ...fan, quantity: 2, discount: 10, discount_type: 'percentage' },
      { ...motor, quantity: 1, discount: 500, discount_type: 'fixed' },
    ]} />);
    const cart = screen.getByRole('region', { name: 'Customer cart' });
    expect(within(cart).getByText('₹4,500.00')).toBeInTheDocument();
    expect(within(cart).getByText('− ₹700.00')).toBeInTheDocument();
    expect(within(cart).getByText('₹3,800.00')).toBeInTheDocument();
    expect(within(cart).getByText('Total before tax')).toBeInTheDocument();
    expect(within(cart).getByText('Applicable tax is calculated in Zoho Books.')).toBeInTheDocument();
  });

  it('caps discounts and routes quantity changes to the selected cart line', () => {
    const update = vi.fn();
    render(<LiveSessionWorkspace cart={[{ ...fan, quantity: 2, discount: 10, discount_type: 'percentage' }]} onUpdateCart={update} />);
    fireEvent.click(screen.getByRole('button', { name: 'Increase quantity of Quiet ceiling fan' }));
    expect(update).toHaveBeenCalledWith(0, 'quantity', 3);
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Discount for Quiet ceiling fan' }), { target: { value: '300' } });
    expect(update).toHaveBeenCalledWith(0, 'discount', 100);
  });

  it('submits internal notes only when content is present', () => {
    const addNote = vi.fn();
    const { rerender } = render(<LiveSessionWorkspace onAddNote={addNote} />);
    expect(screen.getByRole('button', { name: 'Add note' })).toBeDisabled();
    rerender(<LiveSessionWorkspace noteText="Customer needs two motors." onAddNote={addNote} />);
    fireEvent.click(screen.getByRole('button', { name: 'Add note' }));
    expect(addNote).toHaveBeenCalledTimes(1);
  });

  it('keeps items without usable prices out of the cart and shows analysis notices', () => {
    const add = vi.fn();
    render(<LiveSessionWorkspace recommendations={[
      { item_id: 'unpriced', name: 'Unpriced motor' },
      { item_id: 'invalid', name: 'Invalid fan', rate: -50 },
      { item_id: 'zero', name: 'Zero price pump', rate: 0 },
      { item_id: 'pending', name: 'Pending price motor', rate: 4200, price_status: 'confirmation_required' },
    ]} onAddToCart={add} insightsStatus={{ key: 'pending', label: 'Finding product requirements…' }} />);
    expect(screen.getByRole('button', { name: 'Price unavailable for Unpriced motor' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Price unavailable for Invalid fan' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Price unavailable for Zero price pump' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Price unavailable for Pending price motor' })).toBeDisabled();
    expect(screen.queryByText('₹0.00')).not.toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Finding product requirements…');
    expect(add).not.toHaveBeenCalled();
  });

  it('keeps targeted clarification questions in agent guidance and outside customer conversation', () => {
    const question = 'What power rating and electrical phase does the pump need?';
    render(<LiveSessionWorkspace transcript={[{ english_text: 'I need a pump for the farm.' }]}
      currentIntent={{ summary: 'Find a motor compatible with the farm pump' }}
      clarificationSuggestions={[{ id: 'power', requirement_id: 'pump', question, missing_fields: ['power_hp', 'phase'], evidence: 'I need a pump for the farm.' }]}
      agentGuidance={['Confirm the installation requirements before recommending a motor.']} />);
    const guidance = screen.getByRole('region', { name: 'Agent guidance' });
    expect(within(guidance).getByText(question)).toBeVisible();
    expect(within(guidance).getByText('Find a motor compatible with the farm pump', { exact: false })).toBeVisible();
    expect(within(guidance).getByText('For the sales agent')).toBeVisible();
    const conversation = screen.getByRole('region', { name: 'Live translated conversation' });
    expect(within(conversation).queryByText(question)).not.toBeInTheDocument();
    expect(within(conversation).getAllByText('I need a pump for the farm.')).toHaveLength(1);
    expect(screen.queryByRole('textbox', { name: /customer answer/i })).not.toBeInTheDocument();
  });

  it('requires review for a technical match and resets acknowledgement when missing specs change', () => {
    const add = vi.fn();
    const item = { ...motor, match_status: 'needs_confirmation', matched_specs: ['1 HP'], missing_specs: ['Electrical phase'], availability_status: 'needs_review' };
    const { rerender } = render(<LiveSessionWorkspace recommendations={[item]} onAddToCart={add} />);
    expect(screen.getByText('Needs confirmation')).toBeVisible();
    expect(screen.getByText('1 HP', { exact: false })).toBeVisible();
    expect(screen.getByText('Electrical phase', { exact: false })).toBeVisible();
    expect(screen.getByText('Confirm stock')).toBeVisible();
    const button = screen.getByRole('button', { name: 'Add to cart Water pump motor' });
    expect(button).toBeDisabled();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Review unverified match Water pump motor' }));
    expect(button).toBeEnabled();
    fireEvent.click(button);
    expect(add).toHaveBeenCalledWith(expect.objectContaining({ match_status: 'needs_confirmation', match_confirmation_acknowledged: true }));
    rerender(<LiveSessionWorkspace recommendations={[{ ...item, missing_specs: ['Voltage'] }]} onAddToCart={add} />);
    expect(screen.getByRole('checkbox', { name: 'Review unverified match Water pump motor' })).not.toBeChecked();
    expect(button).toBeDisabled();
  });

  it('distinguishes verified matches, unknown stock and out of stock', () => {
    render(<LiveSessionWorkspace recommendations={[
      { ...fan, match_status: 'verified', matched_specs: { sweep_mm: 1200 } },
      { ...motor, availability_status: 'needs_review', available_stock: 50 },
      { ...motor, item_id: 'sold', name: 'Sold out motor', availability_status: 'out_of_stock', available_stock: 5 },
    ]} onAddToCart={vi.fn()} />);
    expect(screen.getByText('Verified match')).toBeVisible();
    expect(screen.getByText('sweep mm: 1200', { exact: false })).toBeVisible();
    expect(screen.getByText('8 in stock')).toBeVisible();
    expect(screen.getByText('Confirm stock')).toBeVisible();
    expect(screen.queryByText('50 in stock')).not.toBeInTheDocument();
    expect(screen.getAllByText('Match not assessed')).toHaveLength(2);
    expect(screen.getByRole('button', { name: 'Out of stock Sold out motor' })).toBeDisabled();
  });

  it('shows incomplete prices and prevents checkout for saved unpriced cart lines', () => {
    render(<LiveSessionWorkspace cart={[{ ...motor, rate: 0, quantity: 2 }]} onCompleteCart={vi.fn()} />);
    const cart = screen.getByRole('region', { name: 'Customer cart' });
    expect(within(cart).getByText('Prices incomplete')).toBeVisible();
    expect(within(cart).getByRole('button', { name: 'Complete cart' })).toBeDisabled();
    expect(within(cart).queryByText('₹0.00')).not.toBeInTheDocument();
  });

  it('allows explicit untracked services and goods without claiming stock availability', () => {
    const add = vi.fn();
    render(<LiveSessionWorkspace recommendations={[
      { item_id: 'service', name: 'Installation service', rate: 800, product_type: 'service', track_inventory: false, stock_on_hand: 0, match_status: 'verified', availability_status: 'not_tracked' },
      { item_id: 'untracked', name: 'Made to order part', rate: 250, item_type: 'sales', product_type: 'goods', stock_on_hand: -1, match_status: 'verified' },
      { item_id: 'inventory', name: 'Tracked service kit', rate: 1000, product_type: 'service', track_inventory: true, stock_on_hand: 0, match_status: 'verified' },
    ]} onAddToCart={add} />);
    expect(screen.getAllByText('Stock not tracked')).toHaveLength(2);
    const button = screen.getByRole('button', { name: 'Add to cart Installation service' });
    expect(button).toBeEnabled();
    fireEvent.click(button);
    expect(add).toHaveBeenCalledWith(expect.objectContaining({ item_id: 'service', stock_on_hand: 0, availability_status: 'not_tracked' }));
    expect(screen.getByRole('button', { name: 'Add to cart Made to order part' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Out of stock Tracked service kit' })).toBeDisabled();
    expect(screen.queryByText(/\d+ in stock/)).not.toBeInTheDocument();
  });

  it('removes resolved guidance and shows catalogue sync state without generating questions', () => {
    const { rerender } = render(<LiveSessionWorkspace catalogueStatus="syncing" clarificationSuggestions={[{ id: 'phase', question: 'Which electrical phase is required?' }]} />);
    expect(screen.getByRole('status')).toHaveTextContent('Catalogue sync in progress');
    expect(screen.getByRole('region', { name: 'Agent guidance' })).toBeVisible();
    rerender(<LiveSessionWorkspace catalogueStatus="ready" clarificationSuggestions={[]} />);
    expect(screen.queryByRole('region', { name: 'Agent guidance' })).not.toBeInTheDocument();
    expect(screen.queryByText('Which electrical phase is required?')).not.toBeInTheDocument();
  });

  it('renders unavailable analysis and semantic search separately without removing the transcript', () => {
    render(<LiveSessionWorkspace transcript={[{ english_text: 'Need two fans.' }]} catalogueStatus="semantic_unavailable" insightsStatus={{ key: 'unavailable', label: 'Product analysis temporarily unavailable' }} />);
    expect(screen.getByText('Product analysis temporarily unavailable')).toBeVisible();
    expect(screen.getByText('Product similarity search is temporarily unavailable.', { exact: false })).toBeVisible();
    expect(screen.getByRole('region', { name: 'Live translated conversation' })).toHaveTextContent('Need two fans.');
  });

  it('keeps bulk deferred lookups visible without claiming that unchecked requirements have no match', () => {
    const requirements = Array.from({ length: 70 }, (_, index) => ({
      requirement_id: `requirement-${index}`, product: `Requested item ${index + 1}`,
      status: index < 64 ? 'matched' : 'lookup_deferred', missing_specs: [],
    }));
    const { rerender } = render(<LiveSessionWorkspace requirements={requirements} />);
    const guidance = screen.getByRole('region', { name: 'Agent guidance' });
    expect(within(guidance).getByText('6 requirements are awaiting catalogue lookup.')).toBeVisible();
    expect(guidance.querySelectorAll('[data-assessment-status="lookup_deferred"]')).toHaveLength(6);
    expect(within(guidance).getByText('Requested item 70:', { exact: true })).toBeVisible();
    expect(within(guidance).queryByText(/No compatible match found/)).not.toBeInTheDocument();
    expect(screen.queryByText('Suggested questions to ask the customer')).not.toBeInTheDocument();
    rerender(<LiveSessionWorkspace requirements={requirements.map((requirement) => ({ ...requirement }))} />);
    expect(guidance.querySelectorAll('[data-assessment-status="lookup_deferred"]')).toHaveLength(6);
  });
});
