# Bug Fix: GST Groups Not Loading When Switching to Salesperson Account

## Problem
When an Owner switched the connected account from their own to a Salesperson's account in the Dealer setup form, the GST groups were not loading/updating in the dropdown.

## Root Cause
The backend endpoints for loading GST groups and customer contacts were missing the `dealer_id` parameter in the URL path. This caused permission validation issues:

**Original Endpoint:**
```
GET /api/dealer/accounts/{account_member_id}/gst-groups
```

When the Owner (not a dealer) called this endpoint:
- The backend used `context.member_id` to get the dealer_id
- But `context.member_id` was the Owner's ID, not the dealer being configured
- This caused incorrect permission checks and GST group loading failures

## Solution

### 1. Backend: Updated Endpoint Paths
**Before:**
```python
@router.get("/api/dealer/accounts/{account_member_id}/gst-groups")
async def dealer_account_gst_groups(account_member_id: UUID):
    context, org, dealer_id = _scope()  # ❌ dealer_id is wrong when Owner accesses it
    # ... complex permission logic ...
```

**After:**
```python
@router.get("/api/dealer/{dealer_id}/accounts/{account_member_id}/gst-groups")
async def dealer_account_gst_groups(dealer_id: UUID, account_member_id: UUID):
    context, org = _account_access(dealer_id)  # ✅ Correct dealer_id from URL
    status = _account_status(account_member_id)
    result = await zoho.taxes_for_documents(status["organization_id"])
    return {"items": result.get("taxes", [])}
```

**Changes:**
- ✅ Added `{dealer_id}` to the route path
- ✅ Simplified permission checking using `_account_access(dealer_id)`
- ✅ Removed redundant permission logic
- ✅ Now correctly handles Owner and Salesperson roles

### 2. Frontend: Updated API Call
**Before:**
```typescript
request<{items: Tax[]}>(`/dealer/accounts/${accountId}/gst-groups`, ...)
```

**After:**
```typescript
request<{items: Tax[]}>(`/dealer/${dealerId}/accounts/${accountId}/gst-groups`, ...)
```

**Changes:**
- ✅ Added `dealerId` to the URL path
- ✅ Added `dealerId` to useEffect dependency array
- ✅ Added `!dealerId` check before making the request

## Endpoints Updated
Both GST groups and customer contacts endpoints were updated:

1. **GST Groups Loading (Fixed)**
   - Old: `GET /api/dealer/accounts/{account_member_id}/gst-groups`
   - New: `GET /api/dealer/{dealer_id}/accounts/{account_member_id}/gst-groups`

2. **Customer Contacts Lookup**
   - Old: `GET /api/dealer/accounts/{account_member_id}/contacts`
   - New: `GET /api/dealer/{dealer_id}/accounts/{account_member_id}/contacts`

## User Flow After Fix

```
1. Owner opens Dealer Setup page
   ↓
2. Selects dealer (dealerId state updated)
   ↓
3. Loads existing mapping
   ↓
4. Owner changes "Connected account" dropdown
   ↓
5. accountId state changes
   ↓
6. useEffect detects change (now has dealerId)
   ↓
7. Calls: GET /api/dealer/{dealerId}/accounts/{accountId}/gst-groups
   ↓
8. Backend validates: Can owner/salesperson access this dealer?
   ↓
9. Backend fetches GST from selected account's Zoho Books
   ↓
10. ✅ GST dropdown updates with new groups
   ↓
11. Owner selects GST and saves
```

## Permission Flow
Now correctly handles:

**When Owner accesses:**
```
GET /api/dealer/{dealerId}/accounts/{salespersonId}/gst-groups
↓
_account_access(dealerId) validates:
├─ Is user the owner? YES → Allow
└─ Fetch GST from salesperson's Zoho account
```

**When Salesperson accesses (their own dealer):**
```
GET /api/dealer/{dealerId}/accounts/{salesPersonId}/gst-groups
↓
_account_access(dealerId) validates:
├─ Is user owner? NO
├─ Is user a salesperson assigned to this dealer? Must check
└─ Fetch GST from their Zoho account
```

## Files Modified

1. **backend/app/dealer_routes.py**
   - Updated line 469: `dealer_account_contacts` endpoint path
   - Updated line 479: `dealer_account_gst_groups` endpoint path
   - Simplified both endpoints using `_account_access(dealer_id)`

2. **frontend/src/pages/DealerManagement.tsx**
   - Updated line 44: useEffect dependency array (added `dealerId`)
   - Updated line 44: API URL path (added `dealerId`)
   - Updated line 45: Condition check (added `!dealerId` check)

## Testing

**Test Case 1: Owner Switches Account**
1. ✅ Open Dealer Setup as Owner
2. ✅ Select a dealer
3. ✅ Change "Connected account" to a Salesperson
4. ✅ GST Group dropdown updates (loads salesperson's GST groups)
5. ✅ Select a GST group
6. ✅ Save successfully

**Test Case 2: Different Dealers**
1. ✅ Select first dealer
2. ✅ Switch account → GST loads
3. ✅ Select second dealer
4. ✅ Switch account → Different GST loads
5. ✅ Both configurations independent

**Test Case 3: Salesperson Access**
1. ✅ Salesperson can view their dealers
2. ✅ Salesperson can only use their own account
3. ✅ GST loads from their account

## Verification

After deployment, verify:
- [ ] Owner can switch dealer account to salesperson
- [ ] GST groups load from salesperson's Zoho Books
- [ ] GST dropdown shows available groups
- [ ] Can select and save GST group
- [ ] Invoices use correct GST
- [ ] Error messages shown if Zoho not connected

## Related Issue
This fix resolves the issue where:
- GST groups appeared empty when switching accounts
- Form showed "No GST Group" even though groups were available
- Dropdown didn't update when account was changed
