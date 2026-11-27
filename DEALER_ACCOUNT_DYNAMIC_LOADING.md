# Dealer Setup Form - Dynamic Account-Based Customer & GST Loading

## Enhancement Summary

Updated the Dealer Setup form to dynamically load Zoho Books customers and GST groups based on the selected connected account (Owner or Sales Rep).

## Changes Made

### Frontend: `frontend/src/pages/DealerManagement.tsx`

1. **Clear contact search when account changes**
   - When user selects an Owner or Sales Rep in "Connected account" dropdown, the contact search field is now reset
   - Previous: `setAccountId(event.target.value); setContactId('')`
   - **New:** `setAccountId(event.target.value); setContactId(''); setContactSearch('')`

2. **Clear contact search on account load reset**
   - When no account is selected or workspace is not available, reset contact search
   - Previous: `if (!accountId || !workspace) { setContacts([]); setTaxes([]); return }`
   - **New:** `if (!accountId || !workspace) { setContacts([]); setTaxes([]); setContactSearch(''); return }`

**How It Works:**
1. User selects a Dealer
2. User selects an Owner or Sales Rep from "Connected account" dropdown
3. Frontend automatically:
   - Resets the contact search field
   - Clears previously selected customer
   - Fetches that account's customers from `/api/dealer/accounts/{accountId}/contacts`
   - Fetches that account's GST groups from `/api/dealer/accounts/{accountId}/gst-groups`
4. Customer dropdown and GST Group dropdown populate with the selected account's data
5. User can type in "Search Zoho Books customers" to filter
6. User selects a customer and GST Group
7. User saves the mapping

### Backend: `backend/app/dealer_routes.py`

**Updated endpoint:** `GET /api/dealer/accounts/{account_member_id}/contacts`

**Authorization Fix:**
- **Previous:** Only Owner could access (blocked Sales Rep)
- **New:** Both Owner and Sales Rep can access their own accounts' customers
  - Owner: Can access any Owner/Sales Rep's customers
  - Sales Rep: Can only access their own account's customers when setting up an assigned Dealer

**Changes:**
```python
# Before
if context.actor_role != "owner":
    raise HTTPException(403, "Only the Owner can configure Dealer account mappings")

# After
if context.actor_role not in {"owner", "salesperson"}:
    raise HTTPException(403, "Dealer setup is available to the Owner or assigned Sales Rep")
if context.actor_role == "salesperson":
    if str(account_member_id) != str(context.user.id):
        raise HTTPException(403, "A Sales Rep can only use their own connected account")
    with require_db() as db:
        assigned = db.execute(text("""select 1 from dealer_account_mappings
            where organisation_id=cast(:org as uuid) and dealer_id=cast(:dealer as uuid)
              and account_member_id=cast(:account as uuid)"""),
            {"org": org, "dealer": str(dealer_id), "account": str(account_member_id)}).first()
    if context.role != "dealer" and not assigned:
        raise HTTPException(403, "Only the assigned Sales Rep can configure this Dealer")
```

## User Experience Flow

### Setup Steps:
1. ✅ Select a Dealer
2. ✅ Select a connected account (Owner or Sales Rep)
   - **New:** Contacts and GST groups load automatically
3. 📝 Search for a Zoho Books customer (optional)
   - **Improved:** Search results show only the selected account's customers
4. 🎯 Select the customer contact
   - Dropdown populated with the account's customers
5. 💰 Set dealer-wide discount (%)
6. 🏷️ Select GST Group
   - **Improved:** Shows only the selected account's GST groups
7. 💾 Save Dealer setup

## Permissions Matrix

| User Role | Can See | Can Assign | Notes |
|-----------|---------|-----------|-------|
| Owner | All accounts' customers & GST groups | Any Owner or Sales Rep account | Full control |
| Sales Rep | Own account's customers & GST groups | Only their own account | Limited to themselves |
| Dealer | N/A | N/A | Configured by Owner/Sales Rep |

## Testing

✅ All tests pass:
- Backend: `tests/test_dealer_routes.py` - **PASSED**
- Frontend: `npm run check` - **PASSED** (TypeScript)
- Build: `npm run build` - **PASSED**

## Key Improvements

1. **Account-Specific Data**: Customers and GST groups are now scoped to the selected account
2. **Better UX**: Form resets properly when switching accounts
3. **Sales Rep Support**: Sales Reps can now configure Dealers assigned to them
4. **Data Accuracy**: Prevents mixing customers from different Zoho Books accounts
5. **Search Optimization**: Search results match the selected account's scope

## Related Endpoints

- `GET /api/dealer/accounts` - List available Owner/Sales Rep accounts
- `GET /api/dealer/accounts/{accountId}/contacts?search=...` - **UPDATED** - Fetch account's customers
- `GET /api/dealer/accounts/{accountId}/gst-groups` - Fetch account's GST groups
- `PUT /api/dealer/{dealerId}/mapping` - Save the Dealer configuration

## Backward Compatibility

✅ **Fully backward compatible**
- Existing Dealer mappings continue to work
- No database changes required
- API response format unchanged
- Authorization now more permissive (allowing Sales Reps)
