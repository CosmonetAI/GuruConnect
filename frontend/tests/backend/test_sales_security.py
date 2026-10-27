"""Mounted sales routes use synthetic verified identities and in-memory SQL fixtures.

No hosted database, OAuth token, or model/provider calls are used.
"""
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import main as sales
from app import chat as chat_module
from app import zoho as zoho_module
from app import agent
from app import db as sales_db
from app.db import sales_workspace, scope_parameters
from recorder import security


class Row:
    def __init__(self, values):
        self._mapping = values


class Result:
    def __init__(self, rows=()):
        self.rows = [Row(row) for row in rows]

    def first(self):
        return next(iter(self.rows), None)

    def all(self):
        return self.rows


@pytest.fixture
def api(monkeypatch):
    org, other_org, user, peer, owner, outsider, receptionist = (str(uuid4()) for _ in range(7))
    customer, other_customer, session, peer_session = (str(uuid4()) for _ in range(4))
    conversation, wrong_customer, wrong_owner, wrong_tenant = (str(uuid4()) for _ in range(4))
    calls = []
    sessions = {
        session: {'id': session, 'organisation_id': org, 'member_id': user, 'customer_id': customer, 'name': 'Scoped customer', 'context': {}},
        peer_session: {'id': peer_session, 'organisation_id': org, 'member_id': peer, 'customer_id': other_customer, 'name': 'Peer customer', 'context': {}},
    }
    conversations = {
        conversation: (org, user, customer),
        wrong_customer: (org, user, other_customer),
        wrong_owner: (org, peer, customer),
        wrong_tenant: (other_org, user, customer),
    }
    memberships = {(org, user): 'salesperson', (org, peer): 'salesperson',
                   (org, owner): 'owner', (other_org, outsider): 'owner', (org, receptionist): 'receptionist'}

    async def auth_one(connection, query, params=()):
        if 'allow_api_request' in query:
            return {'allowed': True}
        assert 'from members' in query
        role = memberships.get(tuple(str(value) for value in params))
        return {'role': role} if role else None

    monkeypatch.setattr(security, 'one', auth_one)

    class AuthDatabase:
        @asynccontextmanager
        async def connection(self):
            yield object()

    class Verifier:
        async def verify(self, token):
            if token not in memberships_by_token:
                raise HTTPException(401, 'Invalid session')
            return security.User(UUID(memberships_by_token[token]), '', 'Verified member')

    memberships_by_token = {'valid': user, 'peer': peer, 'owner': owner, 'outsider': outsider, 'receptionist': receptionist}

    class Database:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def commit(self):
            pass

        def execute(self, statement, params):
            query = ' '.join(str(statement).lower().split())
            calls.append((query, dict(params)))
            selected = sessions.get(str(params.get('session')))
            allowed = selected and (selected['organisation_id'], selected['member_id']) == (params.get('org'), params.get('member'))
            if query.startswith('select s.*,c.name'):
                assert 'c.owner_id=s.member_id' in query and 's.member_id=cast(:member as uuid)' in query
                return Result([selected] if allowed else [])
            if query.startswith('select id from conversations'):
                linked = conversations.get(str(params['conversation']))
                assert 'customer_id=cast(:customer as uuid)' in query
                return Result([{'id': params['conversation']}] if linked == (params['org'], params['member'], params['customer']) else [])
            if query.startswith('update chat_sessions set conversation_id'):
                assert 'member_id=cast(:member as uuid)' in query and 'customer_id=cast(:customer as uuid)' in query
                if allowed:
                    selected['conversation_id'] = params['conversation']
                return Result([{'id': selected['id']}] if allowed else [])
            if query.startswith('insert into voice_session_notes'):
                assert 's.organisation_id=cast(:org as uuid)' in query and 's.member_id=cast(:member as uuid)' in query
                return Result([{'id': str(uuid4()), 'content': params['content'], 'author_id': params['actor'], 'session_id': params['session']}] if allowed else [])
            if 'from chat_messages m join chat_sessions s' in query:
                assert 's.organisation_id=cast(:org as uuid)' in query and 's.member_id=cast(:member as uuid)' in query
                return Result([{'role': 'user', 'content': 'Only this customer'}] if allowed else [])
            if query.startswith('select s.id,s.title'):
                assert 's.member_id=cast(:member as uuid)' in query and 'c.owner_id=s.member_id' in query
                return Result([row for row in sessions.values() if (row['organisation_id'], row['member_id']) == (params['org'], params['member'])])
            raise AssertionError(f'Unexpected fixture query: {query}')

    monkeypatch.setattr(sales, 'require_db', Database)
    parent = FastAPI()
    parent.state.db = AuthDatabase()
    parent.state.settings = SimpleNamespace(rate_limit_per_minute=120)
    parent.state.verifier = Verifier()
    parent.mount('/', sales.bind_parent(parent))
    with TestClient(parent, raise_server_exceptions=False) as client:
        yield SimpleNamespace(client=client, headers={'Authorization': 'Bearer valid', 'X-Workspace-ID': org},
            calls=calls, org=org, other_org=other_org, user=user, peer=peer, owner=owner,
            session=session, peer_session=peer_session, customer=customer, conversation=conversation,
            bad_conversations=[wrong_customer, wrong_owner, wrong_tenant], parent=parent)


@pytest.mark.parametrize('method,path,body', [
    ('GET', '/api/chats', None), ('GET', '/api/chats/00000000-0000-0000-0000-000000000001/messages', None),
    ('GET', '/api/zoho/status', None), ('POST', '/api/chat', {'message': 'hello'}),
    ('POST', '/api/voice-sessions', {'name': 'Example', 'phone': '9876543210'}),
    ('POST', '/api/voice-sessions/00000000-0000-0000-0000-000000000001/conversation', {}),
    ('GET', '/api/products', None), ('GET', '/api/session-contacts', None),
])
def test_all_sales_routes_require_a_verified_bearer_before_sql(api, method, path, body):
    response = api.client.request(method, path, headers={'X-Workspace-ID': api.org, 'X-User-ID': api.user}, json=body)
    assert response.status_code == 401
    assert not api.calls


def test_standalone_sales_app_cannot_bypass_parent_authentication():
    with TestClient(sales.app, raise_server_exceptions=False) as client:
        assert client.get('/api/chats', headers={'Authorization': 'Bearer forged', 'X-Workspace-ID': str(uuid4())}).status_code == 503
        assert client.get('/api/health').status_code == 200
        assert client.get('/docs').status_code == 404


def test_forged_bearer_and_inactive_or_cross_workspace_membership_fail_closed(api):
    assert api.client.get('/api/chats', headers={**api.headers, 'Authorization': 'Bearer forged'}).status_code == 401
    assert api.client.get('/api/chats', headers={**api.headers, 'X-Workspace-ID': api.other_org}).status_code == 403
    assert not api.calls


@pytest.mark.parametrize('headers', [{'X-Workspace-ID': ''}, {'X-Workspace-ID': 'invalid'}, {'X-Workspace-Member-ID': 'invalid'}])
def test_workspace_headers_are_validated_after_authentication(api, headers):
    assert api.client.get('/api/chats', headers={**api.headers, **headers}).status_code == 400
    assert not api.calls


def test_spoofed_user_header_does_not_grant_peer_session_or_messages(api):
    response = api.client.get(f'/api/chats/{api.peer_session}/messages', headers={**api.headers, 'X-User-ID': api.peer})
    assert response.status_code == 404
    assert all('from chat_messages' not in query for query, _ in api.calls)
    response = api.client.get('/api/chats', headers={**api.headers, 'X-User-ID': api.peer})
    assert response.status_code == 200
    assert [row['id'] for row in response.json()] == [api.session]
    assert api.calls[-1][1]['member'] == api.user


def test_only_verified_owner_can_explicitly_select_another_member(api):
    headers = {**api.headers, 'X-Workspace-Member-ID': api.peer}
    assert api.client.get(f'/api/chats/{api.peer_session}/messages', headers=headers).status_code == 403
    headers['Authorization'] = 'Bearer owner'
    response = api.client.get(f'/api/chats/{api.peer_session}/messages', headers=headers)
    assert response.status_code == 200
    assert response.json()[0]['content'] == 'Only this customer'


@pytest.mark.parametrize('method,suffix,body', [
    ('GET', 'history', None), ('GET', 'notes', None), ('POST', 'notes', {'content': 'Private'}),
    ('GET', 'context', None), ('PUT', 'context', {'product_requirements': []}),
    ('POST', 'conversation', {'conversation_id': None}),
])
def test_voice_session_idor_is_denied_for_every_operation(api, method, suffix, body):
    response = api.client.request(method, f'/api/voice-sessions/{api.peer_session}/{suffix}', headers=api.headers, json=body)
    assert response.status_code == 404
    assert len(api.calls) == 1


def test_chat_cannot_continue_a_peer_session(api, monkeypatch):
    async def no_provider(*args):
        pytest.fail('An unauthorized session must never call a provider')
    monkeypatch.setattr(sales, 'run_graph', no_provider)
    response = api.client.post('/api/chat', headers=api.headers, json={'session_id': api.peer_session, 'message': 'show private history'})
    assert response.status_code == 404
    assert len(api.calls) == 1


def test_conversation_link_requires_same_workspace_customer_and_owner(api):
    path = f'/api/voice-sessions/{api.session}/conversation'
    for conversation in [str(uuid4()), *api.bad_conversations]:
        response = api.client.post(path, headers=api.headers, json={'conversation_id': conversation})
        assert response.status_code == 404
    assert all(not query.startswith('update') for query, _ in api.calls)
    response = api.client.post(path, headers=api.headers, json={'conversation_id': api.conversation})
    assert response.status_code == 200
    assert response.json()['conversation_id'] == api.conversation
    response = api.client.post(path, headers=api.headers, json={'conversation_id': None})
    assert response.status_code == 200


def test_note_author_is_verified_actor_even_with_spoofed_header(api):
    response = api.client.post(f'/api/voice-sessions/{api.session}/notes', headers={**api.headers, 'X-User-ID': api.peer}, json={'content': 'A note'})
    assert response.status_code == 200
    assert response.json()['note']['author_id'] == api.user


@pytest.mark.parametrize('path', ['/api/zoho/status', '/api/products', '/api/chat', '/api/session-contacts'])
def test_receptionists_cannot_access_zoho_or_the_sales_agent(api, path):
    method = 'POST' if path == '/api/chat' else 'GET'
    response = api.client.request(method, path, headers={**api.headers, 'Authorization': 'Bearer receptionist'}, json={'message': 'hello'} if method == 'POST' else None)
    assert response.status_code == 403
    assert not api.calls


def test_raw_database_errors_are_not_returned(api, monkeypatch):
    def failure():
        raise RuntimeError('private-database-password private-customer-text')
    monkeypatch.setattr(sales, 'require_db', failure)
    response = api.client.get('/api/chats', headers=api.headers)
    assert response.status_code == 503
    assert 'private-' not in response.text


def test_provider_errors_and_validation_inputs_are_redacted(api, monkeypatch):
    async def failure():
        raise RuntimeError('private-oauth-token private-customer-text')
    monkeypatch.setattr(sales.zoho, 'smoke_test', failure)
    response = api.client.post('/api/zoho/smoke-test', headers=api.headers)
    assert response.status_code >= 400
    assert 'private-' not in response.text
    response = api.client.post('/api/zoho/connect', headers=api.headers, json={'code': {'secret': 'private-oauth-token'}})
    assert response.status_code == 422
    assert 'private-' not in response.text


def test_cron_refresh_requires_its_own_secret_without_identity_headers(api, monkeypatch):
    monkeypatch.setattr(sales.settings, 'zoho_refresh_cron_secret', 'synthetic-cron-secret')
    async def refreshed():
        return 2
    monkeypatch.setattr(sales, 'refresh_all', refreshed)
    assert api.client.post('/api/internal/zoho/refresh', headers=api.headers).status_code == 401
    response = api.client.post('/api/internal/zoho/refresh', headers={'X-Zoho-Refresh-Secret': 'synthetic-cron-secret'})
    assert response.status_code == 200
    assert response.json()['refreshed'] == 2
    assert not api.calls


def test_sales_workspace_is_cleared_after_request(api):
    assert api.client.get('/api/chats', headers=api.headers).status_code == 200
    assert sales_workspace.get() is None
    with pytest.raises(HTTPException):
        scope_parameters()


def test_parent_binding_is_per_instance_and_cannot_replace_another_verifier(api):
    parent = FastAPI()
    parent.state.db = api.parent.state.db
    parent.state.settings = api.parent.state.settings
    class RejectVerifier:
        async def verify(self, token):
            raise HTTPException(401, 'Invalid session')
    parent.state.verifier = RejectVerifier()
    parent.mount('/', sales.bind_parent(parent))
    with TestClient(parent, raise_server_exceptions=False) as second:
        assert second.get('/api/chats', headers=api.headers).status_code == 401
        assert api.client.get('/api/chats', headers=api.headers).status_code == 200


def scoped_user(org, user):
    identity = security.User(UUID(user), '', 'Verified user')
    return security.Workspace(UUID(org), identity, 'salesperson', UUID(user), 'salesperson')


@pytest.mark.asyncio
async def test_customer_identity_is_partitioned_by_workspace_and_member(monkeypatch):
    customers = {}
    class Database:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def commit(self):
            pass
        def execute(self, statement, params):
            query = str(statement)
            identity = (params['org'], params['member'], params['phone'])
            if query.startswith('select'):
                assert 'organisation_id=cast(:org as uuid)' in query and 'owner_id=cast(:member as uuid)' in query
                return Result([customers[identity]] if identity in customers else [])
            assert query.startswith('insert into customers')
            row = {'id': str(uuid4()), 'name': params['name'], 'organisation_id': params['org'],
                   'owner_id': params['member'], 'normalized_phone': params['phone'], 'normalized_email': params['email']}
            customers[identity] = row
            return Result([row])
    monkeypatch.setattr(chat_module, 'require_db', Database)
    org, second_org, user, peer = (str(uuid4()) for _ in range(4))
    ids = []
    for organisation, member in [(org, user), (org, user), (org, peer), (second_org, user)]:
        token = sales_workspace.set(scoped_user(organisation, member))
        try:
            result = await chat_module.get_or_create_customer('Customer', '+91 9876543210', 'Example@Test.invalid')
            ids.append(result['id'])
            assert result['organisation_id'] == organisation and result['owner_id'] == member
        finally:
            sales_workspace.reset(token)
    assert ids[0] == ids[1]
    assert len(set(ids)) == 3


@pytest.mark.asyncio
async def test_transaction_updates_cannot_target_another_member(monkeypatch):
    recorded = []
    class Database:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def commit(self):
            pass
        def execute(self, statement, params):
            recorded.append((str(statement), params))
    monkeypatch.setattr(agent, 'require_db', Database)
    org, user = str(uuid4()), str(uuid4())
    token = sales_workspace.set(scoped_user(org, user))
    try:
        await agent._persist_transaction({'customer': {'session_id': str(uuid4())}}, {'books_estimate_id': 'synthetic'})
    finally:
        sales_workspace.reset(token)
    query, params = recorded[0]
    assert 's.organisation_id=cast(:org as uuid)' in query and 's.member_id=cast(:member as uuid)' in query
    assert (params['org'], params['member']) == (org, user)


@pytest.mark.asyncio
async def test_refresh_all_restores_the_request_context_on_failure(monkeypatch):
    previous_org, previous_member, worker_org, worker_member = (str(uuid4()) for _ in range(4))
    class Database:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def execute(self, statement):
            return Result([{'organisation_id': worker_org, 'member_id': worker_member}])
    async def failure(force=False):
        assert zoho_module.get_organisation_id() == worker_org
        assert zoho_module.get_member_id() == worker_member
        raise RuntimeError('synthetic-provider-failure')
    monkeypatch.setattr(zoho_module, 'require_db', Database)
    monkeypatch.setattr(zoho_module.zoho, 'refresh', failure)
    zoho_module.set_organisation_id(previous_org)
    zoho_module.set_member_id(previous_member)
    try:
        assert await zoho_module.refresh_all() == 0
        assert zoho_module.get_organisation_id() == previous_org
        assert zoho_module.get_member_id() == previous_member
    finally:
        zoho_module.set_organisation_id(None)
        zoho_module.set_member_id(None)


def test_sales_database_preserves_verify_full_tls(monkeypatch):
    """Inspect actual engine construction while replacing the network factory."""
    import sqlalchemy
    calls = []
    def engine_factory(url, **kwargs):
        calls.append((url, kwargs))
        return object()
    monkeypatch.setattr(sqlalchemy, 'create_engine', engine_factory)
    monkeypatch.setattr(sales_db.settings, 'database_url', 'postgresql://synthetic@database.invalid/app?sslmode=verify-full&sslrootcert=synthetic.pem')
    namespace = {'__name__': 'app._sales_tls_test', '__package__': 'app'}
    exec(compile(Path(sales_db.__file__).read_text(), sales_db.__file__, 'exec'), namespace)
    url, options = calls[0]
    assert 'sslmode=verify-full' in url
    assert options['connect_args'].get('sslmode', 'verify-full') == 'verify-full'
