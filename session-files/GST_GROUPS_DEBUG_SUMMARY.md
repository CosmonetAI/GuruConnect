# GST Groups Loading Issue - Debug Summary

## Problem Statement
When switching the connected account for a dealer in the Owner's Dealer Setup page, the GST groups dropdown becomes empty instead of loading groups from the newly selected account.

## Symptoms
1. ✅ Initial account (Salesperson) - GST groups load correctly  
2. ❌ Switched account - GST dropdown becomes empty
3. ✅ GST tax settings in Zoho show the groups exist

## Root Cause Investigation

### Frontend Issues (Fixed)
1. **Race Condition**: State updates are async, so accountId might not update before GST load effect runs
   - **Solution**: Added immediate GST loading when dealer mapping is loaded (before accountId state update)

2. **Missing Error Display**: Errors were caught but not shown to user
   - **Solution**: Added error messages to UI and console logging

### Potential Backend Issues (Under Investigation)
1. **Zoho Connection**: The switched account might not be connected to Zoho Books
   - Backend checks: `status.get("connected") or not status.get("organization_id")`
   - Throws HTTP 409 if not connected

2. **Tax Cache**: GST groups use cached data from account connection
   - Function: `zoho.taxes_for_documents()` → `cached_taxes()`
   - Cached data: `connection.get('zoho_taxes')`, `connection.get('zoho_taxes_updated_at')`
   - Cache might not exist for newly added/switched accounts

## Changes Made

### Frontend (DealerManagement.tsx)
1. Added immediate GST loading when dealer mapping is loaded
2. Added detailed console logging for debugging
3. Added error display to UI when GST load fails
4. Added status messages for empty GST responses

### Backend (dealer_routes.py)
1. Added logging to `dealer_account_gst_groups` endpoint:
   - Logs dealer ID and account ID
   - Logs account access check
   - Logs Zoho status retrieval
   - Logs number of tax groups fetched
   - Logs any errors encountered

## Testing Instructions
1. Build frontend: `npm run build`
2. Reload browser
3. Navigate to Dealer Setup page
4. Select a dealer
5. Select an account (should load GST groups - check browser console)
6. **Switch to a different account** - note the error message that appears
7. Check browser console for detailed logs
8. Report the error message shown

## Expected Error Messages
- "No GST groups found for the selected account" - Tax data exists but is empty
- "Failed to load GST groups: Connect Zoho Books..." - Account not connected to Zoho
- "Failed to load GST groups: Select an active Owner..." - Invalid account
- Other network or permission errors

## Next Steps
1. Run test and get error message
2. Check backend logs for more details
3. Verify Zoho connection status for all accounts
4. Check if tax cache exists in database for all accounts
5. If needed, add cache refresh logic
