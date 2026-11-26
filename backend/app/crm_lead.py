"""Resolve a session customer to Zoho Bigin contacts and pipeline deals.

Primary: Contacts search (phone, then email) -> Contacts/{id}/Pipelines.
Fallback: Pipelines word search (full name, then surname), ranked by evidence.
"""
import asyncio
import re

MAX_CONTACTS = 5
MAX_FALLBACK_CONTACT_LOOKUPS = 15
PHONE_FIELDS = ('Phone', 'Mobile', 'Home_Phone', 'Other_Phone', 'Assistant_Phone')
EMAIL_FIELDS = ('Email', 'Secondary_Email')


def normalize_phone(value):
    digits = re.sub(r'\D', '', str(value or ''))
    return digits[-10:] if len(digits) > 10 else digits


def normalize_email(value):
    return str(value or '').strip().lower()


def normalize_name(value):
    return re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', ' ', str(value or ''))).strip().lower()


def split_name(full_name):
    parts = [part for part in normalize_name(full_name).split(' ') if part]
    return (parts[0] if parts else ''), (parts[-1] if len(parts) > 1 else '')


def _rows(payload):
    data = payload.get('data') if isinstance(payload, dict) else None
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def contact_phones(contact):
    return {normalize_phone(contact.get(field)) for field in PHONE_FIELDS if normalize_phone(contact.get(field))}


def contact_emails(contact):
    return {normalize_email(contact.get(field)) for field in EMAIL_FIELDS if normalize_email(contact.get(field))}


def contact_summary(contact):
    layout = contact.get('$layout_id') if isinstance(contact.get('$layout_id'), dict) else {}
    owner = contact.get('Owner') if isinstance(contact.get('Owner'), dict) else {}
    return {
        'id': str(contact.get('id') or ''),
        'name': contact.get('Full_Name') or ' '.join(part for part in (contact.get('First_Name'), contact.get('Last_Name')) if part) or '',
        'email': contact.get('Email') or '',
        'phone': contact.get('Phone') or contact.get('Mobile') or contact.get('Home_Phone') or '',
        'owner': owner.get('name') or '',
        'layout_id': str(layout.get('id') or ''),
    }


def deal_summary(deal, contact=None, match=None):
    """Normalize a Bigin pipeline record; deal id and pipeline id stay separate."""
    pipeline = deal.get('Pipeline')
    layout = deal.get('$layout_id') if isinstance(deal.get('$layout_id'), dict) else {}
    pipeline_id = str(pipeline.get('id') or '') if isinstance(pipeline, dict) else ''
    pipeline_name = (pipeline.get('name') if isinstance(pipeline, dict) else None) or layout.get('name') or (pipeline if isinstance(pipeline, str) else '') or ''
    pipeline_id = pipeline_id or str(layout.get('id') or '')
    deal_contact = deal.get('Contact_Name') if isinstance(deal.get('Contact_Name'), dict) else {}
    owner = deal.get('Owner') if isinstance(deal.get('Owner'), dict) else {}
    contact = contact or {}
    return {
        'deal_id': str(deal.get('id') or ''),
        'deal_name': deal.get('Deal_Name') or '',
        'stage': deal.get('Stage') or '',
        'sub_pipeline': deal.get('Sub_Pipeline') or '',
        'pipeline_id': pipeline_id,
        'pipeline_name': pipeline_name,
        'amount': deal.get('Amount'),
        'closing_date': deal.get('Closing_Date') or '',
        'description': deal.get('Description') or '',
        'owner': owner.get('name') or contact.get('owner') or '',
        'contact_id': str(deal_contact.get('id') or contact.get('id') or ''),
        'contact_name': deal_contact.get('name') or contact.get('name') or '',
        'contact_email': contact.get('email') or '',
        'contact_phone': contact.get('phone') or '',
        'modified_time': deal.get('Modified_Time') or '',
        'created_time': deal.get('Created_Time') or '',
        'last_activity_time': deal.get('Last_Activity_Time') or '',
        'match': match or {'score': 0, 'reasons': []},
    }


async def _search_contacts(zoho, search_by, value):
    return _rows(await zoho.lookup_bigin_contacts(search_by, value))


async def find_contacts(zoho, phone, email):
    """Return (matched_by, contacts) using exact validated matches only."""
    wanted_phone = normalize_phone(phone)
    wanted_email = normalize_email(email)
    by_phone, by_email = [], []
    if wanted_phone:
        raw_digits = re.sub(r'\D', '', str(phone))
        found = await _search_contacts(zoho, 'phone', wanted_phone)
        if not any(wanted_phone in contact_phones(row) for row in found) and raw_digits != wanted_phone:
            found += await _search_contacts(zoho, 'phone', raw_digits)
        by_phone = [row for row in found if wanted_phone in contact_phones(row)]
    if wanted_email:
        found = await _search_contacts(zoho, 'email', wanted_email)
        by_email = [row for row in found if wanted_email in contact_emails(row)]
    email_ids = {str(row.get('id')) for row in by_email}
    both = [row for row in by_phone if str(row.get('id')) in email_ids or (wanted_email and wanted_email in contact_emails(row))]
    if both:
        return 'phone+email', both[:MAX_CONTACTS]
    merged = {}
    for row in by_phone + by_email:
        merged.setdefault(str(row.get('id')), row)
    if not merged:
        return None, []
    label = 'phone' if by_phone and not by_email else 'email' if by_email and not by_phone else 'phone or email'
    return label, list(merged.values())[:MAX_CONTACTS]


async def contact_deals(zoho, contact):
    summary = contact_summary(contact)
    if not summary['layout_id']:
        detail = _rows(await zoho.get_bigin_contact(summary['id']))
        layout = (detail[0].get('$layout_id') if detail else None) or {}
        summary['layout_id'] = str(layout.get('id') or '')
    return summary, _rows(await zoho.contact_pipelines(summary['id'], summary['layout_id']))


async def fallback_candidates(zoho, name, phone, email):
    first, surname = split_name(name)
    full = normalize_name(name)
    if not full:
        return []
    searched = 'full_name'
    display_name = re.sub(r'\s+', ' ', str(name)).strip()
    rows = _rows(await zoho.search_pipeline_records(display_name))
    if not rows and surname:
        searched = 'surname'
        rows = _rows(await zoho.search_pipeline_records(display_name.split(' ')[-1]))
    unique = {}
    for row in rows:
        if row.get('id'):
            unique.setdefault(str(row['id']), row)
    rows = list(unique.values())
    contact_ids = []
    for row in rows:
        ref = row.get('Contact_Name')
        if isinstance(ref, dict) and ref.get('id') and str(ref['id']) not in contact_ids:
            contact_ids.append(str(ref['id']))
    contact_ids = contact_ids[:MAX_FALLBACK_CONTACT_LOOKUPS]
    fetched = await asyncio.gather(*(zoho.get_bigin_contact(cid) for cid in contact_ids), return_exceptions=True)
    contacts = {}
    for cid, result in zip(contact_ids, fetched):
        data = [] if isinstance(result, BaseException) else _rows(result)
        if data:
            contacts[cid] = data[0]
    wanted_phone, wanted_email = normalize_phone(phone), normalize_email(email)
    results = []
    for row in rows:
        ref = row.get('Contact_Name') if isinstance(row.get('Contact_Name'), dict) else {}
        contact = contacts.get(str(ref.get('id') or ''), {})
        candidate_name = normalize_name(ref.get('name') or contact.get('Full_Name'))
        score, reasons = 0, []
        if wanted_email and wanted_email in contact_emails(contact):
            score += 100; reasons.append('email')
        if wanted_phone and wanted_phone in contact_phones(contact):
            score += 50; reasons.append('phone')
        if candidate_name and candidate_name == full:
            score += 20; reasons.append('full name')
        elif surname and candidate_name and candidate_name.split(' ')[-1] == surname:
            score += 5; reasons.append('surname')
        results.append(deal_summary(row, contact_summary(contact) if contact else None, {'score': score, 'reasons': reasons, 'searched_by': searched}))
    results.sort(key=lambda item: item['modified_time'], reverse=True)
    results.sort(key=lambda item: item['match']['score'], reverse=True)
    return results


async def resolve_crm_lead(zoho, name='', phone='', email=''):
    matched_by, contacts = await find_contacts(zoho, phone, email)
    if contacts:
        deals, summaries, seen = [], [], set()
        for contact in contacts:
            summary, rows = await contact_deals(zoho, contact)
            summaries.append(summary)
            for row in rows:
                deal = deal_summary(row, summary, {'score': 100, 'reasons': [matched_by], 'searched_by': 'contact'})
                if deal['deal_id'] and deal['deal_id'] not in seen:
                    seen.add(deal['deal_id']); deals.append(deal)
        return {'status': 'matched' if deals else 'contact_without_deals', 'source': 'contacts', 'matched_by': matched_by, 'contacts': summaries, 'deals': deals}
    deals = await fallback_candidates(zoho, name, phone, email)
    if not deals:
        return {'status': 'not_found', 'source': 'none', 'matched_by': None, 'contacts': [], 'deals': []}
    return {'status': 'candidates', 'source': 'name_fallback', 'matched_by': None, 'contacts': [], 'deals': deals}
