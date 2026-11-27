# Implementation Architecture Diagram

## Complete System Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                     DEALER SETUP SYSTEM                              │
└─────────────────────────────────────────────────────────────────────┘

PHASE 1: DEALER INVITATION & AUTO-MAPPING
═════════════════════════════════════════════════════════════════════════

   Owner Invites Dealer
   │
   ├─→ Sends invitation via Email
   │
   └─→ Dealer Accepts & Creates Account
       │
       └─→ System Creates Member Record (role='dealer')
           │
           └─→ ⚡ DATABASE TRIGGER FIRES ⚡
               │
               ├─ Trigger: trigger_auto_map_dealer_on_create
               ├─ Function: auto_map_dealer_to_owner()
               │
               └─→ Query Members Table
                   ├─ Find: WHERE role='owner' AND status='active'
                   └─ Get: owner_member_id
                   
                       └─→ INSERT into dealer_account_mappings
                           ├─ dealer_id: Dealer's user_id
                           ├─ account_member_id: Owner's user_id  ← MAPPED!
                           ├─ discount_percent: 0
                           ├─ gst_group_id: NULL
                           └─ books_contact_id: ''

   ✅ RESULT: Dealer auto-mapped to Owner, ready to use!


PHASE 2: OWNER CONFIGURES DEALER
═════════════════════════════════════════════════════════════════════════

   Owner Opens Dealer Setup Page
   │
   ├─→ Calls: GET /api/dealer/accounts
   │   └─ Returns: [ { member_id, name, role, connected } ]
   │
   ├─→ Calls: GET /api/dealer/{dealer_id}/mapping
   │   └─ Returns: { account_member_id, discount_percent, gst_group_id }
   │
   └─→ Form Loaded with Current Settings
       ├─ Connected Account: [Owner ▼]
       ├─ Discount: [0 %]
       └─ GST Group: [No GST Group ▼]


PHASE 3: OWNER SWITCHES ACCOUNT
═════════════════════════════════════════════════════════════════════════

   User clicks "Connected account" dropdown
   │
   └─→ Selects: Sales Rep (Alice)
       │
       └─→ onChange Event Triggered
           │
           ├─→ State Update: setAccountId('alice-id')
           │
           └─→ ⚡ useEffect DETECTS CHANGE ⚡
               │
               ├─ Dependency: [accountId, workspace]
               ├─ Condition: accountId changed
               │
               └─→ CALL API: GET /api/dealer/accounts/alice-id/gst-groups
                   │
                   └─→ Backend Processing:
                       ├─ Check Permission: User can access alice's account
                       ├─ Get Alice's Zoho Connection: zoho_connection_id
                       ├─ Call Zoho API: taxes_for_documents(org_id)
                       └─ Return: [ { tax_id, tax_name, tax_percentage } ]

                           └─→ Frontend Receives Response
                               │
                               ├─ Update State: setTaxes(response.items)
                               │
                               ├─→ ⚡ CLEAR PREVIOUS GST ⚡
                               │   └─ setGstId('')  ← NEW FEATURE!
                               │
                               └─→ Re-render GST Dropdown
                                   ├─ Show: Alice's available GST groups
                                   ├─ Clear: Previous selection
                                   └─ Ready: User to select new GST


PHASE 4: OWNER SAVES CONFIGURATION
═════════════════════════════════════════════════════════════════════════

   Owner Selects New GST Group
   │
   └─→ Owner Clicks "Save Dealer setup"
       │
       └─→ CALL API: PUT /api/dealer/{dealer_id}/mapping
           │
           ├─ Body: {
           │   account_member_id: 'alice-id',
           │   discount_percent: 0,
           │   gst_group_id: 'cgst-9-sgst-9'
           │ }
           │
           └─→ Backend Processing:
               ├─ Validate Permission: Alice can be assigned
               ├─ Validate GST: Exists in Alice's account
               ├─ Update Record: INSERT ... ON CONFLICT DO UPDATE
               └─ Return: { saved: true }

                   └─→ Frontend Shows: "Dealer account mapping saved."
                       │
                       └─→ Configuration Active!
                           ├─ Future invoices use Alice's account
                           ├─ Future invoices use Alice's Zoho connection
                           ├─ Future invoices apply CGST 9% + SGST 9%
                           └─ Future invoices apply 0% discount


PHASE 5: DEALER CREATES INVOICE
═════════════════════════════════════════════════════════════════════════

   Dealer (or assigned staff) adds products to cart
   │
   └─→ Clicks "Submit" / "Generate Invoice"
       │
       └─→ System Retrieves Mapping:
           ├─ dealer_id: Dealer's user_id
           ├─ account_member_id: 'alice-id'
           ├─ gst_group_id: 'cgst-9-sgst-9'
           └─ discount_percent: 0
           
               └─→ Create Invoice Payload:
                   ├─ Use: Alice's Zoho Books Connection
                   ├─ Apply: 0% discount on each line
                   ├─ Apply: CGST 9% + SGST 9% taxes
                   └─ Call: zoho.create_invoice(alice_org_id, payload)

                       └─→ Zoho Creates Invoice
                           │
                           └─→ System Stores:
                               ├─ dealer_invoice_references record
                               ├─ zoho_invoice_id
                               ├─ account_member_id: alice-id
                               └─ state: 'active'

                                   └─→ Download PDF to Dealer


═════════════════════════════════════════════════════════════════════════
STATE & DATA FLOW DIAGRAM
═════════════════════════════════════════════════════════════════════════

Frontend Component State:
┌──────────────────────────────────────────────────────────────┐
│ DealerManagement Component                                   │
├──────────────────────────────────────────────────────────────┤
│                                                               │
│ State Variables:                                              │
│ ├─ dealerId: string              [Dealer selector]           │
│ ├─ accountId: string ◄─── WATCHED BY useEffect              │
│ ├─ taxes: Tax[]                  [Loaded from API]           │
│ ├─ gstId: string                 [Cleared on account change] │
│ ├─ discount: string              [User input]                │
│ └─ busy/error/notice: string     [UI feedback]               │
│                                                               │
│ Effects:                                                      │
│ ├─ [dealerId] → Fetch dealer mapping                         │
│ ├─ [accountId] → Fetch GST groups (CLEARS gstId)  ⚡        │
│ └─ [form submit] → Save to backend                           │
│                                                               │
└──────────────────────────────────────────────────────────────┘


Database Trigger Flow:
┌──────────────────────────────────────────────────────────────┐
│ PostgreSQL Trigger                                           │
├──────────────────────────────────────────────────────────────┤
│                                                               │
│ Event: INSERT INTO members                                   │
│ ├─ Trigger: ON INSERT ... FOR EACH ROW                      │
│ ├─ Condition: new.role = 'dealer'                           │
│ │                                                             │
│ └─ Function: auto_map_dealer_to_owner()                     │
│    ├─ Query: Find owner_member_id                           │
│    ├─ Check: active owner exists                            │
│    └─ Insert: dealer_account_mappings record                │
│       └─ Defaults: discount=0, gst=NULL                     │
│                                                               │
└──────────────────────────────────────────────────────────────┘


API Endpoints Used:
┌──────────────────────────────────────────────────────────────┐
│ Backend API Endpoints                                        │
├──────────────────────────────────────────────────────────────┤
│                                                               │
│ 1. GET /api/dealer/accounts                                 │
│    └─→ Load all accounts (Owner & Sales Reps)              │
│                                                               │
│ 2. GET /api/dealer/{id}/mapping                             │
│    └─→ Load current dealer configuration                    │
│                                                               │
│ 3. GET /api/dealer/accounts/{id}/gst-groups ⚡ DYNAMIC      │
│    ├─→ Called when account changes                          │
│    ├─→ Fetches GST from selected account's Zoho            │
│    └─→ Returns fresh tax group options                      │
│                                                               │
│ 4. PUT /api/dealer/{id}/mapping                             │
│    └─→ Save dealer configuration                            │
│                                                               │
└──────────────────────────────────────────────────────────────┘


Zoho Integration Points:
┌──────────────────────────────────────────────────────────────┐
│ Zoho Books Integration                                       │
├──────────────────────────────────────────────────────────────┤
│                                                               │
│ When Setup Account:                                          │
│ ├─→ Call: zoho.taxes_for_documents(org_id)                 │
│ └─→ Get: Available tax groups from Zoho                     │
│                                                               │
│ When Creating Invoice:                                       │
│ ├─→ Switch Context: set_member_id(alice_id)               │
│ ├─→ Call: zoho.create_invoice(payload)                    │
│ └─→ Use: Alice's Zoho connection                           │
│                                                               │
│ Each Account's Zoho Connection:                             │
│ ├─→ Has own Organization ID                                 │
│ ├─→ Has own Tax Configuration                               │
│ ├─→ Has own Products List                                   │
│ └─→ Has own Customers Database                              │
│                                                               │
└──────────────────────────────────────────────────────────────┘


═════════════════════════════════════════════════════════════════════════
KEY FEATURES HIGHLIGHTED
═════════════════════════════════════════════════════════════════════════

🎯 Auto-Mapping (Database Trigger)
   ├─ Automatic on dealer creation
   ├─ Maps to organization owner
   ├─ No manual configuration needed
   └─ Prevents missing mappings

🎯 Dynamic GST Loading (Frontend useEffect)
   ├─ Loads when account changes
   ├─ Account-specific tax groups
   ├─ Clears invalid selections
   └─ Real-time dropdown updates

🎯 Flexible Reconfiguration
   ├─ Switch accounts anytime
   ├─ Different GST per account
   ├─ Customizable discount
   └─ Changes apply to future invoices only

🎯 Data Integrity
   ├─ Trigger ensures no orphans
   ├─ Each invoice tracks mapping
   ├─ Previous settings preserved
   └─ Backward compatible


═════════════════════════════════════════════════════════════════════════
DEPLOYMENT ORDER
═════════════════════════════════════════════════════════════════════════

1. Database Migrations (in order)
   ├─→ 202611270003_dealer_auto_mapping.sql
   │   └─ Creates: auto_map_dealer_to_owner() trigger
   │
   └─→ 202611270002_remove_books_contact_requirement.sql
       └─ Updates: DealerMapping model in backend

2. Backend Deployment
   └─→ backend/app/dealer_routes.py
       ├─ Updated: DealerMapping model
       ├─ Updated: save_dealer_mapping endpoint
       └─ Already have: GST groups loading endpoint

3. Frontend Deployment
   └─→ frontend/src/pages/DealerManagement.tsx
       ├─ Updated: useEffect to clear GST on account change
       └─ Already have: API call to load GST groups

═════════════════════════════════════════════════════════════════════════
