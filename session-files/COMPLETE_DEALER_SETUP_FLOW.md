# Complete Dealer Account Setup Flow

## End-to-End Implementation Summary

### Phase 1: Dealer Invitation ✅
When Owner invites a Dealer:
```
Invite Dealer → Database Trigger Fires → Auto-mapped to Owner's Account
                                       ├─ Discount: 0%
                                       ├─ GST Group: NULL (can be set later)
                                       └─ Account: Owner
```

### Phase 2: Dealer Setup Page ✅
Owner configures the dealer mapping:

```
Dealer Setup Form
├─ [Dealer Selector] → Loads existing mapping
├─ [Connected Account Dropdown]
│  ├─ Shows Owner and all Sales Reps
│  ├─ Marks non-connected accounts
│  └─ **On Change Trigger:**
│     ├─ Calls: GET /api/dealer/accounts/{accountId}/gst-groups
│     ├─ Loads: GST groups from selected account's Zoho Books
│     ├─ Updates: GST Group dropdown with new options
│     └─ Clears: Previous GST selection
├─ [Discount Input]
├─ [GST Group Selector] ← Dynamically populated from connected account
└─ [Save Button]
```

### Phase 3: Invoice Generation ✅
When dealer creates an invoice:
```
Dealer Submission
├─ Uses: Mapped account's Zoho Books connection
├─ Applies: Configured discount percentage
├─ Applies: Selected GST group (if set)
└─ Creates: Invoice in owner/sales rep's Zoho Books
```

## Key Features Implemented

### ✅ Auto-Mapping (202611270001_dealer_auto_mapping.sql)
- Trigger function automatically creates dealer_account_mappings
- Maps to organization's owner by default
- Prevents manual setup errors

### ✅ Dynamic GST Loading (DealerManagement.tsx)
- Loads GST groups when account changes
- Clears invalid selections automatically
- Shows account-specific tax configurations

### ✅ Flexible Configuration
- Owner can switch account to any Sales Rep
- GST groups update automatically
- Discount percentage customizable
- Changes apply only to future invoices

## Database Schema

```
dealer_account_mappings
├─ organisation_id (UUID) → Links to organization
├─ dealer_id (UUID) → Dealer member ID
├─ account_member_id (UUID) → Owner or Sales Rep ID
├─ books_contact_id (TEXT) → Empty string (for future use)
├─ discount_percent (NUMERIC) → 0-100%
├─ gst_group_id (TEXT) → Optional Zoho Books tax ID
└─ updated_at (TIMESTAMP) → Track changes
```

## API Endpoints

### 1. Load Available Accounts
```
GET /api/dealer/accounts
Response: { items: [ { member_id, name, role, connected } ] }
Purpose: Show all accounts owner/sales rep can assign
```

### 2. Load Dealer's Current Mapping
```
GET /api/dealer/{dealer_id}/mapping
Response: { 
  configured: boolean,
  account_member_id: string,
  discount_percent: number,
  gst_group_id: string | null
}
Purpose: Pre-fill form when editing existing dealer
```

### 3. Load GST Groups for Account
```
GET /api/dealer/accounts/{account_member_id}/gst-groups
Response: { 
  items: [ { 
    tax_id, 
    tax_name, 
    tax_display_name, 
    tax_percentage 
  } ]
}
Purpose: Dynamically load GST options when account changes
```

### 4. Save Dealer Mapping
```
PUT /api/dealer/{dealer_id}/mapping
Body: { 
  account_member_id: UUID,
  discount_percent: number,
  gst_group_id: string | null
}
Response: { saved: true }
Purpose: Persist configuration changes
```

## User Interactions

### Scenario 1: Initial Dealer Setup
1. Owner opens "Dealer setup" page
2. Dealer appears with auto-mapped Owner account
3. Owner reviews auto-populated discount (0%) and GST (NULL)
4. Owner can optionally:
   - Switch to a Sales Rep account
   - Adjust discount percentage
   - Select a GST group
5. Owner clicks "Save Dealer setup"
6. Configuration saved and ready to use

### Scenario 2: Modify Existing Dealer
1. Owner opens "Dealer setup" page
2. Selects a dealer with existing mapping
3. Form loads current account, discount, GST
4. Owner changes account to different Sales Rep
5. **GST groups automatically reload from new account**
6. **Previous GST selection automatically clears**
7. Owner selects new GST group from updated options
8. Owner adjusts discount if needed
9. Owner clicks "Save Dealer setup"
10. Changes applied to future invoices

### Scenario 3: Dealer Creates Invoice
1. Dealer (or assigned staff) adds products to cart
2. Submits order
3. System uses:
   - Mapped account's Zoho Books connection
   - Configured discount percentage
   - Selected GST group
4. Invoice created in mapped account's Zoho Books
5. PDF automatically downloaded

## State Management Flow

```
Component State
├─ dealerId: Track selected dealer
├─ accountId: Track connected account
│  └─ onChange: Trigger GST reload
├─ taxes: GST groups from selected account
│  └─ Populated by accountId effect
├─ gstId: Selected GST group
│  └─ Cleared when accountId changes
├─ discount: Discount percentage
└─ busy/error/notice: UI feedback

Data Flow:
dealerId change → Load dealer mapping
accountId change → Load GST groups from account
                → Clear previous GST selection
gstId/discount change → User input, no API call
Save → Send all to backend
```

## Validation & Safety

✅ **Backend Validations:**
- Owner/Sales Rep permission checks
- Sales Rep can only use their own account
- Zoho Books connection verification
- GST group existence in selected account
- Database constraints and triggers

✅ **Frontend Validations:**
- Auto-clear invalid selections
- Disable save without account
- Show connection status of accounts
- Display errors to user

✅ **Data Integrity:**
- Existing invoices never modified
- Both old and new invoices tracked
- Previous GST/account always preserved per invoice
- Trigger ensures no orphaned dealers

## Testing Checklist

- [ ] Dealer invited → auto-mapped to owner
- [ ] Load dealer setup page → account pre-filled
- [ ] Switch account → GST groups update
- [ ] Switch account → previous GST clears
- [ ] Select new GST → save successful
- [ ] Create invoice → uses new account/GST
- [ ] Non-connected account → disabled in dropdown
- [ ] Sales rep → can only use their own account
- [ ] Multiple dealers → each has independent config

## Performance Considerations

- GST groups loaded on-demand (not all at once)
- Proper React dependency arrays to avoid infinite loops
- Abort mechanism for cancelled requests
- Minimal database queries (indexed lookups)
- API requests only when needed

## Future Enhancements

- Remember GST preference per account
- Batch operations for multiple dealers
- GST/Discount templates
- Audit trail of mapping changes
- Invoice preview with actual GST/discount applied
