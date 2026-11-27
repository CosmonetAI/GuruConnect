import hashlib
import json
import logging
import re
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from .db import current_workspace, require_db, row_dict
from .zoho import get_member_id, set_member_id, zoho

router = APIRouter()
log = logging.getLogger("sales.dealers")


class CartLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str = Field(min_length=1, max_length=100)
    quantity: Decimal = Field(gt=0, le=100000)


class DealerCart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_key: UUID
    lines: list[CartLine] = Field(min_length=1, max_length=50)


class DealerMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_member_id: UUID
    discount_percent: Decimal = Field(ge=0, le=100)
    gst_group_id: str | None = Field(default=None, max_length=100)


def _scope():
    context = current_workspace()
    return context, str(context.id), str(context.member_id)


def _set_account(member_id):
    previous = get_member_id()
    set_member_id(str(member_id))
    return previous


def _mapping(dealer_id):
    context, org, _ = _scope()
    with require_db() as db:
        row = db.execute(text("""select d.dealer_id,d.account_member_id,d.books_contact_id,
                d.discount_percent,d.gst_group_id,m.role as account_role,
                coalesce(p.display_name, u.email, 'Account') as account_name
            from dealer_account_mappings d
            join members m on m.organisation_id=d.organisation_id and m.user_id=d.account_member_id and m.status='active'
            join auth.users u on u.id=m.user_id
            left join profiles p on p.id=m.user_id
            where d.organisation_id=cast(:org as uuid) and d.dealer_id=cast(:dealer as uuid)"""),
            {"org": org, "dealer": str(dealer_id)}).mappings().first()
    if not row:
        raise HTTPException(409, "Dealer account mapping is not configured")
    return dict(row)


def _dealer_access():
    context, org, dealer_id = _scope()
    if context.role != "dealer":
        raise HTTPException(403, "Open a Dealer workspace to use this action")
    mapping = _mapping(dealer_id)
    return context, org, dealer_id, mapping


def _account_access(dealer_id):
    context, org, _ = _scope()
    if context.actor_role not in {"owner", "salesperson"}:
        raise HTTPException(403, "Dealer setup is available to the Owner or assigned Sales Rep")
    with require_db() as db:
        dealer = db.execute(text("""select 1 from members
            where organisation_id=cast(:org as uuid) and user_id=cast(:dealer as uuid)
              and role='dealer' and status='active'"""),
            {"org": org, "dealer": str(dealer_id)}).first()
        existing = db.execute(text("""select account_member_id from dealer_account_mappings
            where organisation_id=cast(:org as uuid) and dealer_id=cast(:dealer as uuid)"""),
            {"org": org, "dealer": str(dealer_id)}).first()
    if not dealer:
        raise HTTPException(404, "Dealer not found")
    if context.actor_role == "salesperson" and (not existing or str(existing[0]) != str(context.user.id)):
        raise HTTPException(403, "Only the assigned Sales Rep can configure this Dealer")
    return context, org


def _account_status(member_id):
    _, org, _ = _scope()
    with require_db() as db:
        member = db.execute(text("""select role from members
            where organisation_id=cast(:org as uuid) and user_id=cast(:member as uuid) and status='active'"""),
            {"org": org, "member": str(member_id)}).first()
    if not member or member[0] not in {"owner", "salesperson"}:
        raise HTTPException(422, "Select an active Owner or Sales Rep in this workspace")
    previous = _set_account(member_id)
    try:
        status = zoho.status()
    finally:
        set_member_id(previous)
    if not status.get("connected") or not status.get("organization_id"):
        raise HTTPException(409, "Connect Zoho Books and select an organization for the assigned account")
    return status


async def _catalogue_item(org, item_id):
    if not re.fullmatch(r"[0-9]{1,64}", item_id):
        raise HTTPException(422, "Select a valid Zoho Books product")
    response = await zoho.books_item(org, item_id)
    item = response.get("item") or response.get("data") or {}
    if str(item.get("item_id") or "") != item_id or str(item.get("status") or "active").lower() not in {"active", "enabled"}:
        raise HTTPException(409, "A selected product is no longer available")
    try:
        rate = Decimal(str(item.get("rate")))
    except Exception:
        raise HTTPException(409, "A selected product has no valid Zoho Books price") from None
    if not rate.is_finite() or rate <= 0:
        raise HTTPException(409, "A selected product has no valid Zoho Books price")
    return item, rate


def _tax_percent(group):
    try:
        return Decimal(str(group.get("tax_percentage")))
    except Exception:
        return Decimal(0)


async def _invoice_payload(org, mapping, cart):
    rows = []
    preview_lines = []
    tax_group = None
    if mapping.get("gst_group_id"):
        taxes = await zoho.taxes_for_documents(org)
        tax_group = next((tax for tax in taxes.get("taxes", [])
                          if str(tax.get("tax_id")) == str(mapping["gst_group_id"])), None)
        if not tax_group:
            raise HTTPException(409, "The configured GST Group is no longer available in Zoho Books")
    for line in cart.lines:
        item, rate = await _catalogue_item(org, line.item_id)
        rows.append({
            "item_id": line.item_id,
            "quantity": float(line.quantity),
            "rate": float(rate),
            "discount": f'{Decimal(str(mapping["discount_percent"]))}%',
            **({"tax_id": str(mapping["gst_group_id"])} if mapping.get("gst_group_id") else {}),
        })
        line_subtotal = rate * line.quantity
        line_discount = (line_subtotal * Decimal(str(mapping["discount_percent"])) / Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        line_taxable = line_subtotal - line_discount
        line_gst = (line_taxable * _tax_percent(tax_group) / Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if tax_group else Decimal(0)
        preview_lines.append({"item_id": line.item_id, "name": item.get("name") or item.get("item_name") or "Product",
                              "quantity": float(line.quantity), "rate": float(rate),
                              "discount_percent": float(mapping["discount_percent"]),
                              "subtotal": float(line_subtotal), "discount": float(line_discount), "gst": float(line_gst),
                              "total": float(line_taxable + line_gst)})
    payload = {"customer_id": str(mapping["books_contact_id"]), "line_items": rows}
    subtotal = sum((Decimal(str(row["rate"])) * Decimal(str(row["quantity"])) for row in rows), Decimal(0))
    discount = (subtotal * Decimal(str(mapping["discount_percent"])) / Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    taxable = subtotal - discount
    tax = (taxable * _tax_percent(tax_group) / Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if tax_group else Decimal(0)
    return payload, {"lines": preview_lines, "subtotal": float(subtotal), "discount": float(discount), "gst": float(tax),
                     "total": float(taxable + tax), "gst_group": tax_group}


def _action_hash(cart):
    content = json.dumps([{"item_id": line.item_id, "quantity": str(line.quantity)} for line in cart.lines],
                         sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode()).hexdigest()


def _existing_action(org, dealer, cart, previous_invoice_id=None):
    with require_db() as db:
        row = db.execute(text("""select * from dealer_invoice_references
            where organisation_id=cast(:org as uuid) and dealer_id=cast(:dealer as uuid)
              and action_key=cast(:action as uuid)"""),
            {"org": org, "dealer": dealer, "action": str(cart.action_key)}).mappings().first()
    if not row:
        return None
    if row["request_hash"] != _action_hash(cart) or str(row.get("previous_zoho_invoice_id") or "") != str(previous_invoice_id or ""):
        raise HTTPException(409, "This order action key was already used for a different order")
    if row["state"] == "active":
        return dict(row)
    raise HTTPException(409, "This invoice action is still being reconciled. Do not submit it again.")


def _claim_action(org, dealer, account, cart, previous_invoice_id=None):
    digest = _action_hash(cart)
    with require_db() as db:
        db.execute(text("select pg_advisory_xact_lock(hashtext(:key))"),
                   {"key": f"dealer-invoice:{org}:{dealer}"})
        existing = db.execute(text("""select * from dealer_invoice_references
            where organisation_id=cast(:org as uuid) and dealer_id=cast(:dealer as uuid)
              and action_key=cast(:action as uuid) for update"""),
            {"org": org, "dealer": dealer, "action": str(cart.action_key)}).mappings().first()
        if existing:
            db.commit()
            if existing["request_hash"] != digest or str(existing.get("previous_zoho_invoice_id") or "") != str(previous_invoice_id or ""):
                raise HTTPException(409, "This order action key was already used for a different order")
            if existing["state"] == "active":
                return dict(existing), False
            raise HTTPException(409, "This invoice action is still being reconciled. Do not submit it again.")
        if not previous_invoice_id:
            unresolved = db.execute(text("""select 1 from dealer_invoice_references
                where organisation_id=cast(:org as uuid) and dealer_id=cast(:dealer as uuid)
                  and previous_zoho_invoice_id is null and state in ('creating','uncertain','cleanup_pending')
                limit 1"""), {"org": org, "dealer": dealer}).first()
            if unresolved:
                raise HTTPException(409, "A previous invoice action is unresolved. Reconcile it before creating another invoice.")
        else:
            replacement = db.execute(text("""select 1 from dealer_invoice_references
                where organisation_id=cast(:org as uuid) and dealer_id=cast(:dealer as uuid)
                  and previous_zoho_invoice_id=:previous
                  and state in ('creating','replacing','void_pending','uncertain','cleanup_pending')
                limit 1"""), {"org": org, "dealer": dealer, "previous": previous_invoice_id}).first()
            if replacement:
                raise HTTPException(409, "This invoice already has an update in progress.")
        row = db.execute(text("""insert into dealer_invoice_references
            (organisation_id,dealer_id,account_member_id,action_key,request_hash,previous_zoho_invoice_id,state)
            values(cast(:org as uuid),cast(:dealer as uuid),cast(:account as uuid),cast(:action as uuid),
                   :hash,:previous,:state) returning *"""),
            {"org": org, "dealer": dealer, "account": str(account), "action": str(cart.action_key),
             "hash": digest, "previous": previous_invoice_id,
             "state": "replacing" if previous_invoice_id else "creating"}).mappings().first()
        db.commit()
    return dict(row), True


async def _create_and_record(org, dealer, mapping, cart, previous_invoice_id=None):
    existing = _existing_action(org, dealer, cart, previous_invoice_id)
    if existing:
        return {"invoice_id": existing["zoho_invoice_id"], "duplicate_request": True}
    previous_account = _set_account(mapping["account_member_id"])
    try:
        connection = _account_status(mapping["account_member_id"])
        payload, preview = await _invoice_payload(connection["organization_id"], mapping, cart)
        action, fresh = _claim_action(org, dealer, mapping["account_member_id"], cart, previous_invoice_id)
        if not fresh:
            return {"invoice_id": action["zoho_invoice_id"], "duplicate_request": True}
        try:
            response = await zoho.create_invoice(connection["organization_id"], payload)
        except Exception as error:
            with require_db() as db:
                db.execute(text("""update dealer_invoice_references set state='uncertain',updated_at=now()
                    where id=cast(:id as uuid) and state in ('creating','replacing')"""), {"id": str(action["id"])})
                db.commit()
            log.error("dealer_invoice_action_uncertain action_id=%s exception_type=%s", action["id"], type(error).__name__)
            raise HTTPException(502, "Zoho invoice creation could not be confirmed. The action is locked to prevent a duplicate invoice.") from error
        invoice = response.get("invoice") or {}
        invoice_id = str(invoice.get("invoice_id") or "")
        if not invoice_id:
            with require_db() as db:
                db.execute(text("update dealer_invoice_references set state='uncertain',updated_at=now() where id=cast(:id as uuid)"),
                           {"id": str(action["id"])})
                db.commit()
            raise HTTPException(502, "Zoho created an invoice but did not return its ID. Further actions are blocked until reconciliation.")
        with require_db() as db:
            db.execute(text("""update dealer_invoice_references set zoho_invoice_id=:invoice,state=:state,updated_at=now()
                where id=cast(:id as uuid)"""),
                {"invoice": invoice_id, "state": "void_pending" if previous_invoice_id else "active", "id": str(action["id"])})
            db.commit()
        if previous_invoice_id:
            try:
                await zoho.void_invoice(connection["organization_id"], previous_invoice_id)
            except Exception:
                try:
                    await zoho.delete_invoice(connection["organization_id"], invoice_id)
                except Exception:
                    try:
                        await zoho.void_invoice(connection["organization_id"], invoice_id)
                    except Exception:
                        with require_db() as db:
                            db.execute(text("update dealer_invoice_references set state='cleanup_pending',updated_at=now() where id=cast(:id as uuid)"),
                                       {"id": str(action["id"])})
                            db.commit()
                        raise HTTPException(502, "Zoho could not void the previous or replacement invoice. Further changes are blocked until reconciliation.")
                with require_db() as db:
                    db.execute(text("update dealer_invoice_references set state='failed',updated_at=now() where id=cast(:id as uuid)"),
                               {"id": str(action["id"])})
                    db.commit()
                raise HTTPException(502, "Zoho could not void the previous invoice. The replacement was removed; the previous invoice remains active.")
            with require_db() as db:
                db.execute(text("""update dealer_invoice_references set state='voided',updated_at=now()
                    where organisation_id=cast(:org as uuid) and dealer_id=cast(:dealer as uuid)
                      and zoho_invoice_id=:previous"""), {"org": org, "dealer": dealer, "previous": previous_invoice_id})
                db.execute(text("update dealer_invoice_references set state='active',updated_at=now() where id=cast(:id as uuid)"),
                           {"id": str(action["id"])})
                db.commit()
        return {"invoice_id": invoice_id, "preview": preview}
    finally:
        set_member_id(previous_account)


@router.get("/api/dealer/config")
def dealer_configuration():
    _, org, dealer, mapping = _dealer_access()
    return {"account_member_id": str(mapping["account_member_id"]), "account_name": mapping["account_name"],
            "books_contact_id": mapping["books_contact_id"], "discount_percent": float(mapping["discount_percent"]),
            "gst_group_id": mapping["gst_group_id"]}


@router.get("/api/dealer/{dealer_id}/mapping")
def read_dealer_mapping(dealer_id: UUID):
    context, org = _account_access(dealer_id)
    with require_db() as db:
        row = db.execute(text("""select dealer_id,account_member_id,books_contact_id,discount_percent,gst_group_id
            from dealer_account_mappings where organisation_id=cast(:org as uuid) and dealer_id=cast(:dealer as uuid)"""),
            {"org": org, "dealer": str(dealer_id)}).mappings().first()
    if not row:
        return {"configured": False}
    return {"configured": True, **dict(row)}


@router.get("/api/dealer/products")
async def dealer_products(search: str = ""):
    _, _, _, mapping = _dealer_access()
    status = _account_status(mapping["account_member_id"])
    result = await zoho.search_books_items(status["organization_id"], search.strip()[:100], page=1)
    return {"items": result.get("items", result.get("data", []))}


@router.get("/api/dealer/gst-groups")
async def dealer_gst_groups():
    _, _, _, mapping = _dealer_access()
    status = _account_status(mapping["account_member_id"])
    result = await zoho.taxes_for_documents(status["organization_id"])
    return {"taxes": result.get("taxes", [])}


@router.get("/api/dealer/invoices")
async def dealer_invoices():
    context, org, dealer, _ = _dealer_access()
    with require_db() as db:
        rows = [row_dict(row) for row in db.execute(text("""select zoho_invoice_id,account_member_id,
              previous_zoho_invoice_id,state,created_at from dealer_invoice_references
            where organisation_id=cast(:org as uuid) and dealer_id=cast(:dealer as uuid)
              and zoho_invoice_id is not null order by created_at desc"""),
            {"org": org, "dealer": dealer}).all()]
    blocked_updates = {row["previous_zoho_invoice_id"] for row in rows
                       if row["previous_zoho_invoice_id"] and row["state"] in {"creating", "replacing", "void_pending", "uncertain", "cleanup_pending"}}
    invoices = []
    for row in rows:
        previous = _set_account(row["account_member_id"])
        try:
            connection = _account_status(row["account_member_id"])
            result = await zoho.books_invoice(connection["organization_id"], row["zoho_invoice_id"])
            invoice = result.get("invoice") or {}
            invoices.append({"invoice_id": row["zoho_invoice_id"], "invoice_number": invoice.get("invoice_number"),
                             "status": invoice.get("status"), "date": invoice.get("date"),
                             "total": invoice.get("total"), "balance": invoice.get("balance"),
                             "state": row["state"], "previous_invoice_id": row["previous_zoho_invoice_id"],
                             "can_update": str(invoice.get("status") or "").lower() == "due"
                             and invoice.get("is_editable", True) is not False and row["state"] == "active"
                             and row["zoho_invoice_id"] not in blocked_updates})
        except Exception as error:
            log.warning("dealer_invoice_refresh_failed invoice_id=%s exception_type=%s",
                        row["zoho_invoice_id"], type(error).__name__)
            invoices.append({"invoice_id": row["zoho_invoice_id"], "state": row["state"],
                             "status": "Unavailable", "can_update": False})
        finally:
            set_member_id(previous)
    return {"items": invoices}


@router.get("/api/dealer/invoices/{invoice_id}")
async def dealer_invoice_detail(invoice_id: str):
    _, org, dealer, _ = _dealer_access()
    with require_db() as db:
        row = db.execute(text("""select account_member_id from dealer_invoice_references
            where organisation_id=cast(:org as uuid) and dealer_id=cast(:dealer as uuid)
              and zoho_invoice_id=:invoice"""),
            {"org": org, "dealer": dealer, "invoice": invoice_id}).first()
    if not row:
        raise HTTPException(404, "Invoice not found")
    previous = _set_account(row[0])
    try:
        status = _account_status(row[0])
        result = await zoho.books_invoice(status["organization_id"], invoice_id)
        return {"invoice": result.get("invoice") or {}}
    finally:
        set_member_id(previous)


@router.post("/api/dealer/invoices")
async def create_dealer_invoice(cart: DealerCart):
    _, org, dealer, mapping = _dealer_access()
    return await _create_and_record(org, dealer, mapping, cart)


@router.post("/api/dealer/preview")
async def preview_dealer_order(cart: DealerCart):
    _, _, _, mapping = _dealer_access()
    status = _account_status(mapping["account_member_id"])
    _, preview = await _invoice_payload(status["organization_id"], mapping, cart)
    return preview


@router.post("/api/dealer/invoices/{invoice_id}/replace")
async def replace_dealer_invoice(invoice_id: str, cart: DealerCart):
    _, org, dealer, mapping = _dealer_access()
    existing = _existing_action(org, dealer, cart, invoice_id)
    if existing:
        return {"invoice_id": existing["zoho_invoice_id"], "duplicate_request": True}
    with require_db() as db:
        previous = db.execute(text("""select account_member_id,state from dealer_invoice_references
            where organisation_id=cast(:org as uuid) and dealer_id=cast(:dealer as uuid)
              and zoho_invoice_id=:invoice"""),
            {"org": org, "dealer": dealer, "invoice": invoice_id}).mappings().first()
    if not previous or previous["state"] != "active":
        raise HTTPException(404, "Eligible invoice not found")
    if str(previous["account_member_id"]) != str(mapping["account_member_id"]):
        raise HTTPException(409, "This invoice belongs to a previous Dealer account mapping")
    account_previous = _set_account(mapping["account_member_id"])
    try:
        status = _account_status(mapping["account_member_id"])
        detail = await zoho.books_invoice(status["organization_id"], invoice_id)
        invoice = detail.get("invoice") or {}
        if str(invoice.get("status") or "").lower() != "due":
            raise HTTPException(409, "Only a Due invoice can be updated")
        return await _create_and_record(org, dealer, mapping, cart, invoice_id)
    finally:
        set_member_id(account_previous)


@router.get("/api/dealer/invoices/{invoice_id}/pdf")
async def dealer_invoice_pdf(invoice_id: str):
    _, org, dealer, _ = _dealer_access()
    with require_db() as db:
        row = db.execute(text("""select account_member_id from dealer_invoice_references
            where organisation_id=cast(:org as uuid) and dealer_id=cast(:dealer as uuid)
              and zoho_invoice_id=:invoice"""),
            {"org": org, "dealer": dealer, "invoice": invoice_id}).first()
    if not row:
        raise HTTPException(404, "Invoice not found")
    previous = _set_account(row[0])
    try:
        status = _account_status(row[0])
        content = await zoho.download_sales_document(status["organization_id"], "invoice", invoice_id)
        return Response(content, media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="invoice-{invoice_id}.pdf"'})
    finally:
        set_member_id(previous)


@router.get("/api/dealer/accounts")
def dealer_accounts():
    context, org, _ = _scope()
    if context.actor_role != "owner":
        raise HTTPException(403, "Only the Owner can assign Dealer accounts")
    with require_db() as db:
        rows = [row_dict(row) for row in db.execute(text("""select m.user_id as member_id,m.role,
                coalesce(p.display_name,u.email,'Account') as name
            from members m join auth.users u on u.id=m.user_id left join profiles p on p.id=m.user_id
            where m.organisation_id=cast(:org as uuid) and m.role in ('owner','salesperson') and m.status='active'
            order by m.role,p.display_name"""), {"org": org}).all()]
    accounts = []
    for row in rows:
        try:
            status = _account_status(row["member_id"])
            accounts.append({**row, "connected": True, "organization_id": status.get("organization_id")})
        except Exception:
            accounts.append({**row, "connected": False})
    return {"items": accounts}


@router.get("/api/dealer/accounts/{account_member_id}/contacts")
async def dealer_account_contacts(account_member_id: UUID, search: str = ""):
    context, org, dealer_id = _scope()
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
    status = _account_status(account_member_id)
    result = await zoho.search_books_customers(status["organization_id"], search.strip()[:100], page=1)
    return {"items": result.get("contacts", [])}


@router.get("/api/dealer/accounts/{account_member_id}/gst-groups")
async def dealer_account_gst_groups(account_member_id: UUID):
    context, org, dealer_id = _scope()
    if context.actor_role not in {"owner", "salesperson"}:
        raise HTTPException(403, "Dealer setup is available to the Owner or assigned Sales Rep")
    if context.actor_role == "salesperson":
        if str(account_member_id) != str(context.user.id):
            raise HTTPException(403, "A Sales Rep can only use their own connected account")
        with require_db() as db:
            assigned = db.execute(text("""select 1 from dealer_account_mappings
                where organisation_id=cast(:org as uuid) and dealer_id=cast(:dealer as uuid)
                  and account_member_id=cast(:account as uuid)"""),
                {"org": org, "dealer": str(context.member_id), "account": str(account_member_id)}).first()
        if context.role != "dealer" or not assigned:
            raise HTTPException(403, "Only the assigned Sales Rep can configure this Dealer")
    status = _account_status(account_member_id)
    result = await zoho.taxes_for_documents(status["organization_id"])
    return {"items": result.get("taxes", [])}


@router.put("/api/dealer/{dealer_id}/mapping")
async def save_dealer_mapping(dealer_id: UUID, body: DealerMapping):
    context, org = _account_access(dealer_id)
    if context.actor_role != "owner" and str(body.account_member_id) != str(context.user.id):
        raise HTTPException(403, "A Sales Rep can only assign their own connected account")
    status = _account_status(body.account_member_id)
    if body.gst_group_id:
        previous = _set_account(body.account_member_id)
        try:
            taxes = await zoho.taxes_for_documents(status["organization_id"])
            if not any(str(tax.get("tax_id")) == body.gst_group_id for tax in taxes.get("taxes", [])):
                raise HTTPException(422, "Select an active GST Group from the connected Zoho Books account")
        finally:
            set_member_id(previous)
    with require_db() as db:
        db.execute(text("""insert into dealer_account_mappings
            (organisation_id,dealer_id,account_member_id,books_contact_id,discount_percent,gst_group_id,updated_at)
            values(cast(:org as uuid),cast(:dealer as uuid),cast(:account as uuid),'',
                   :discount,:gst,now())
            on conflict (organisation_id,dealer_id) do update set
              account_member_id=excluded.account_member_id,
              discount_percent=excluded.discount_percent,gst_group_id=excluded.gst_group_id,updated_at=now()"""),
            {"org": org, "dealer": str(dealer_id), "account": str(body.account_member_id),
             "discount": body.discount_percent, "gst": body.gst_group_id})
        db.commit()
    return {"saved": True}


