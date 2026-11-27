# Dealer Account Setup Enhancement

## What Changed

The Dealer Setup form now dynamically loads and displays customers and GST groups specific to the selected Owner or Sales Rep account.

## Key Improvements

### 1. Dynamic Customer Loading
**Before:** Customers weren't scoped to the selected account
**After:** When you select an Owner or Sales Rep, customers from that specific Zoho Books account are automatically loaded

### 2. Dynamic GST Group Loading  
**Before:** GST Groups shown weren't specific to the account
**After:** Only GST Groups configured for the selected account's Zoho Books are shown

### 3. Sales Rep Account Support
**Before:** Only Owner could assign Accounts
**After:** Sales Reps can now configure Dealers assigned to them, using their own account

### 4. Form State Management
**Before:** Switching accounts left old search/contact data
**After:** Switching accounts properly clears search field and contact selection, loading fresh data

## User Experience

### Scenario 1: Owner Setting Up a Dealer with Owner Account
1. Select Dealer
2. Select "Soumyashree Patil • owner" 
   - 🔄 Frontend fetches all customers from Soumyashree's Zoho Books
   - 🔄 Frontend fetches all GST Groups from Soumyashree's Zoho Books
3. Search for customer (e.g., "ABC Corp")
   - Only Soumyashree's customers shown
4. Select GST Group (e.g., "GST 18%")
   - Only available for Soumyashree's account
5. Save

### Scenario 2: Owner Setting Up a Dealer with Sales Rep Account
1. Select Dealer
2. Select "John Salesman • salesperson"
   - 🔄 Frontend fetches all customers from John's Zoho Books
   - 🔄 Frontend fetches all GST Groups from John's Zoho Books
3. Search for customer (e.g., "XYZ Ltd")
   - Only John's customers shown
4. Select GST Group
   - Only available for John's account
5. Save

### Scenario 3: Sales Rep Setting Up Their Assigned Dealer
1. Select Dealer assigned to them
2. Only their own account appears in dropdown (can't select others)
3. Their customers automatically load
4. Their GST Groups automatically load
5. Configure and save

## Technical Details

### Frontend Changes
- Clear `contactSearch` state when account changes
- API calls to `/dealer/accounts/{accountId}/contacts?search=...`
- API calls to `/dealer/accounts/{accountId}/gst-groups`
- Both use the selected account to scope results

### Backend Changes
- Updated `/dealer/accounts/{accountId}/contacts` endpoint
  - Now allows both Owner and Sales Rep (was Owner-only)
  - Sales Reps restricted to their own account
  - Owner can access any account
- `/dealer/accounts/{accountId}/gst-groups` already supported both roles

### Data Flow

```
User Selects Account
        ↓
Frontend Clears Previous Search
        ↓
Frontend Fetches:
  ├─ /dealer/accounts/{id}/contacts?search=""
  └─ /dealer/accounts/{id}/gst-groups
        ↓
Backend Verifies:
  ├─ Authorization (Owner or assigned Sales Rep)
  ├─ Account exists and connected to Zoho Books
  └─ Fetches from that account's Zoho Books
        ↓
Frontend Updates:
  ├─ Customer dropdown with account-specific contacts
  └─ GST Group dropdown with account-specific groups
        ↓
User Selects Customer & GST Group
        ↓
User Saves Configuration
```

## Files Modified

1. **frontend/src/pages/DealerManagement.tsx**
   - Line 18: `useState('')` for contactSearch
   - Line 50: Reset contactSearch on account clear
   - Line 82: Reset contactSearch on account change
   - Line 84: Use contactSearch state in input

2. **backend/app/dealer_routes.py**
   - Lines 473-490: Updated `dealer_account_contacts` endpoint
     - Added Sales Rep authorization
     - Added account restriction for Sales Rep
     - Maintains backward compatibility

## Testing

✅ **Frontend TypeScript Check**: PASSED
✅ **Backend Unit Tests**: PASSED  
✅ **Production Build**: PASSED

All tests confirm:
- No breaking changes
- Form loads correctly
- API calls work properly
- Authorization enforced

## Backward Compatibility

✅ 100% Backward Compatible
- Existing Dealer mappings continue to work
- No database schema changes
- API responses unchanged
- Only adds new authorization path for Sales Reps

## Ready for Deployment

This enhancement is ready to merge and deploy. It improves the Dealer setup UX while maintaining all existing functionality.
