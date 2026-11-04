import asyncio

from app.crm_lead import normalize_phone, resolve_crm_lead

CONTACT = {'id': 'c1', 'Full_Name': 'Asha Rao', 'Phone': '+91 78408-83484', 'Email': 'Asha@Example.com', '$layout_id': {'id': 'L-contact'}, 'Owner': {'name': 'Owner One'}}
DEAL = {'id': 'd1', 'Deal_Name': 'Fans', 'Stage': 'Needs Analysis', 'Pipeline': {'id': 'P1', 'name': 'Sales Pipeline'}, 'Amount': 10, 'Contact_Name': {'id': 'c1', 'name': 'Asha Rao'}}


class FakeZoho:
    def __init__(self, contacts=None, deals=None, pipelines=None):
        self.contacts = contacts or {}
        self.deals = deals or []
        self.pipelines = pipelines or {}
        self.calls = []

    async def lookup_bigin_contacts(self, search_by, value):
        self.calls.append(('contacts', search_by, value))
        return {'data': self.contacts.get((search_by, value), [])} if (search_by, value) in self.contacts else {'ok': True}

    async def contact_pipelines(self, contact_id, layout_id=None):
        self.calls.append(('deals', contact_id, layout_id))
        return {'data': self.deals}

    async def get_bigin_contact(self, contact_id):
        self.calls.append(('contact', contact_id))
        return {'data': [self.pipelines['contact'][contact_id]]} if contact_id in self.pipelines.get('contact', {}) else {'ok': True}

    async def search_pipeline_records(self, word):
        self.calls.append(('search', word))
        return {'data': self.pipelines.get(word, [])} if word in self.pipelines else {'ok': True}


def run(coro):
    return asyncio.run(coro)


def test_phone_match_uses_contact_layout_and_returns_ids():
    zoho = FakeZoho({('phone', '7840883484'): [CONTACT]}, [DEAL])
    result = run(resolve_crm_lead(zoho, 'Asha Rao', '917840883484', ''))
    assert result['status'] == 'matched' and result['matched_by'] == 'phone'
    assert ('deals', 'c1', 'L-contact') in zoho.calls
    deal = result['deals'][0]
    assert (deal['deal_id'], deal['pipeline_id'], deal['contact_id']) == ('d1', 'P1', 'c1')
    assert not any(call[0] == 'search' for call in zoho.calls)


def test_phone_search_result_without_exact_phone_is_rejected():
    other = {**CONTACT, 'Phone': '9999999999'}
    zoho = FakeZoho({('phone', '7840883484'): [other]}, [DEAL])
    assert run(resolve_crm_lead(zoho, '', '7840883484', ''))['status'] == 'not_found'


def test_email_match_is_case_insensitive_when_phone_misses():
    zoho = FakeZoho({('email', 'asha@example.com'): [CONTACT]}, [DEAL])
    result = run(resolve_crm_lead(zoho, 'Asha Rao', '1112223334', 'ASHA@example.COM'))
    assert result['matched_by'] == 'email' and result['status'] == 'matched'


def test_phone_and_email_prefers_contact_matching_both():
    other = {**CONTACT, 'id': 'c2', 'Email': 'other@example.com'}
    zoho = FakeZoho({('phone', '7840883484'): [other, CONTACT]}, [DEAL])
    result = run(resolve_crm_lead(zoho, '', '7840883484', 'asha@example.com'))
    assert result['matched_by'] == 'phone+email' and [c['id'] for c in result['contacts']] == ['c1']


def test_partial_matches_are_listed_together_when_no_contact_matches_both():
    by_phone = {**CONTACT, 'id': 'c2', 'Email': 'other@example.com'}
    by_email = {**CONTACT, 'id': 'c3', 'Phone': '1'}
    zoho = FakeZoho({('phone', '7840883484'): [by_phone], ('email', 'asha@example.com'): [by_email]}, [DEAL])
    result = run(resolve_crm_lead(zoho, '', '7840883484', 'asha@example.com'))
    assert result['matched_by'] == 'phone or email' and {c['id'] for c in result['contacts']} == {'c2', 'c3'}


def test_contact_without_deals_does_not_fall_back():
    zoho = FakeZoho({('phone', '7840883484'): [CONTACT]}, [])
    result = run(resolve_crm_lead(zoho, 'Asha Rao', '7840883484', ''))
    assert result['status'] == 'contact_without_deals'
    assert not any(call[0] == 'search' for call in zoho.calls)


def test_multiple_deals_are_all_returned():
    zoho = FakeZoho({('phone', '7840883484'): [CONTACT]}, [DEAL, {**DEAL, 'id': 'd2'}])
    assert [d['deal_id'] for d in run(resolve_crm_lead(zoho, '', '7840883484', ''))['deals']] == ['d1', 'd2']


def test_fallback_full_name_ranks_email_first_and_dedupes():
    deal_other = {**DEAL, 'id': 'd9', 'Contact_Name': {'id': 'c9', 'name': 'Asha Rao'}}
    zoho = FakeZoho(pipelines={'Asha Rao': [deal_other, DEAL, DEAL], 'contact': {'c1': CONTACT, 'c9': {**CONTACT, 'id': 'c9', 'Email': 'x@y.z', 'Phone': '1'}}})
    result = run(resolve_crm_lead(zoho, 'Asha Rao', '0000000000', 'asha@example.com'))
    assert result['status'] == 'candidates' and result['source'] == 'name_fallback'
    assert [d['deal_id'] for d in result['deals']] == ['d1', 'd9']
    assert 'email' in result['deals'][0]['match']['reasons']


def test_fallback_uses_surname_only_when_full_name_is_empty():
    zoho = FakeZoho(pipelines={'Rao': [DEAL], 'contact': {'c1': {**CONTACT, 'Email': ''}}})
    result = run(resolve_crm_lead(zoho, 'Asha Rao', '', ''))
    assert ('search', 'Asha Rao') in zoho.calls and ('search', 'Rao') in zoho.calls
    assert result['deals'][0]['match']['searched_by'] == 'surname'
    assert 'email' not in result['deals'][0]['match']['reasons']


def test_nothing_found():
    assert run(resolve_crm_lead(FakeZoho(), 'Nobody Here', '1234567890', 'n@x.com'))['status'] == 'not_found'


def test_fallback_pipeline_id_from_layout_when_pipeline_is_string():
    deal = {'id': 'd3', 'Pipeline': 'Sales Pipeline Standard', '$layout_id': {'id': 'P7', 'name': 'Sales Pipeline'}}
    zoho = FakeZoho(pipelines={'Zed Q': [deal]})
    found = run(resolve_crm_lead(zoho, 'Zed Q', '', ''))['deals'][0]
    assert (found['pipeline_id'], found['pipeline_name']) == ('P7', 'Sales Pipeline')


def test_phone_normalisation():
    assert normalize_phone('+91 (784) 088-3484') == '7840883484'
