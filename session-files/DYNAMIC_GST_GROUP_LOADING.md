# Dynamic GST Group Loading on Account Switch

## Overview
When the connected account is switched in the Dealer setup form, the GST Groups and other details are now dynamically loaded from the newly selected account's Zoho Books configuration.

## Implementation Details

### Frontend Changes: `DealerManagement.tsx`

**What Changed:**
- Enhanced the useEffect hook that loads GST groups (lines 44-57)
- When a user selects a different account, the component now:
  1. Fetches GST groups from the selected account
  2. Loads the tax details for display
  3. **Clears the previously selected GST Group** to avoid showing an invalid selection from the old account

**Key Feature:** Auto-clear GST selection when switching accounts
```typescript
// Clear GST selection when switching accounts
setGstId('')
```

This ensures:
- Users see only GST groups that are available in the newly selected account
- No invalid GST groups are pre-selected
- Form is in a clean state ready for new GST group selection

### Backend Implementation (Already Existing)

The backend endpoint `/api/dealer/accounts/{account_member_id}/gst-groups` was already implemented and handles:

1. **Permission Validation:**
   - Only Owner and Sales Rep roles can access
   - Sales Rep can only view their own account's GST groups
   
2. **Account Status Check:**
   - Verifies the connected Zoho Books account
   - Retrieves organization ID from the account status
   
3. **Dynamic Loading:**
   - Fetches the latest GST groups from Zoho Books using the selected account's connection
   - Returns available tax groups with their percentages

**Endpoint Details:**
```
GET /api/dealer/accounts/{account_member_id}/gst-groups
Response: { "items": [ { tax_id, tax_name, tax_display_name, tax_percentage } ] }
```

## User Flow

```
1. Owner opens Dealer Setup page
   ↓
2. Selects a Dealer
   ↓
3. Loads existing mapping (if configured)
   ↓
4. User changes "Connected account" dropdown
   ↓
5. Component detects accountId change
   ↓
6. Calls /api/dealer/accounts/{accountId}/gst-groups
   ↓
7. Loads GST groups from new account's Zoho Books
   ↓
8. Clears previous GST selection
   ↓
9. Displays new GST group options
   ↓
10. User can select GST from the new account
   ↓
11. User updates discount (if needed)
   ↓
12. User clicks "Save Dealer setup"
```

## Example Scenario

**Scenario:** Switch from Owner to Sales Rep account

1. **Initial State:**
   - Connected Account: Owner (John)
   - Discount: 5%
   - GST Group: IGST 18%

2. **User switches to:** Sales Rep (Alice)

3. **What Happens:**
   - GST Groups loaded from Alice's Zoho Books account
   - Previous selection (IGST 18%) is cleared
   - User sees: CGST 9%, SGST 9%, IGST 0% (Alice's available groups)
   - User can select a new GST from Alice's options
   - Discount remains at 5% (can be changed if needed)

4. **After Save:**
   - Dealer mapping updated with Alice's account
   - New GST group applied to future invoices
   - Existing invoices unchanged

## Benefits

✅ **Always Fresh Data:** GST groups are fetched fresh each time the account changes  
✅ **Account-Specific:** Shows only the tax configurations available for the selected account  
✅ **User-Friendly:** Auto-clears selection to prevent invalid configurations  
✅ **Dynamic:** Works with any Zoho Books account, automatically adapts to their tax setup  
✅ **No Manual Refresh:** Automatic loading - user just selects and form updates

## Technical Architecture

```
Frontend Component (DealerManagement.tsx)
  │
  ├─ useEffect: Listen for accountId changes
  │   ├─ Trigger: User changes "Connected account" dropdown
  │   └─ Action: Fetch GST groups from selected account
  │
  └─ Request to Backend
      └─ GET /api/dealer/accounts/{accountId}/gst-groups
          └─ Backend (dealer_routes.py)
              ├─ Validate permissions
              ├─ Get Zoho Books status for account
              └─ Fetch taxes from Zoho using account's connection
                  └─ Return { items: [ {...tax objects...} ] }
```

## Compatibility

- Works with both Owner and Sales Rep accounts
- Each account has its own Zoho Books configuration
- GST groups are unique to each Zoho Books organization
- Multiple dealers can use different accounts with different GST configs

## Files Modified

1. `frontend/src/pages/DealerManagement.tsx` - Updated GST group loading logic
2. Backend endpoints (no changes needed) - Already implemented correctly

## Related Features

- **Auto-mapping:** Dealers auto-mapped to Owner on invitation (previous implementation)
- **Account Selection:** Dynamic account selector showing connection status
- **Invoice Generation:** Uses the mapped account's Zoho Books connection and GST configuration
- **Multi-Account Support:** Each Sales Rep can have different GST groups configured in their Zoho Books
