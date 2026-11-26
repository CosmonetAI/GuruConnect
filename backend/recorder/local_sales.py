"""Persistent customer Sessions for the loopback development workspace.

These routes use the same private SQLite store and recording IDs as local
recording. Sample inventory is explicitly marked and never sent to Zoho.
"""
from __future__ import annotations

import json
import re
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from .local_store import LOCAL_USER_ID, LOCAL_WORKSPACE_ID, LocalStore, encoded, now


class SessionInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    phone: str = Field(min_length=7, max_length=40)
    email: str = Field(default='', max_length=320)

    @field_validator('name', 'phone', 'email', mode='before')
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator('phone')
    @classmethod
    def phone_number(cls, value):
        digits = phone_key(value)
        if not 7 <= len(digits) <= 15:
            raise ValueError('Enter a valid phone number')
        return digits

    @field_validator('email')
    @classmethod
    def email_address(cls, value):
        if not value:
            return ''
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
            raise ValueError('Enter a valid email address')
        return value.lower()


class SessionLink(BaseModel):
    conversation_id: UUID | None = None


class SessionNote(BaseModel):
    content: str = Field(min_length=1, max_length=5000)


class SessionContext(BaseModel):
    product_requirements: list[dict] = Field(default_factory=list, max_length=25)
    cart: list[dict] = Field(default_factory=list, max_length=50)


def phone_key(value) -> str:
    digits = re.sub(r'\D', '', str(value or ''))
    if len(digits) == 11 and digits.startswith('0'):
        return digits[1:]
    if len(digits) == 12 and digits.startswith('91'):
        return digits[2:]
    return digits


def recording_matches(session: dict, recording: dict) -> bool:
    # A supplied phone identifies the customer; a conflicting phone cannot be
    # bypassed by submitting a matching email or name.
    phone = phone_key(recording.get('phone'))
    if phone:
        return phone == session['phone']
    email = str(recording.get('email') or '').strip().casefold()
    return bool(email and email == session['email'].casefold())


def public_recording(row: dict) -> dict:
    keys = ('id', 'title', 'created_at', 'updated_at', 'capture_state', 'upload_state',
            'transcription_state', 'english_state', 'speaker_state', 'final_state',
            'total_samples', 'duration_seconds', 'active_version_id', 'processing_error_code')
    return {key: row.get(key) for key in keys if key in row}


def install_local_sales_routes(app: FastAPI, store: LocalStore, settings) -> None:
    from .local_voice_analysis import LocalVoiceAnalysis, _processing_status, local_runtime_credentials
    import asyncio
    analysis = LocalVoiceAnalysis(settings, store)
    app.state.local_voice_analysis = analysis

    def session_row(identity: UUID | str) -> dict:
        row = store.get('sales_sessions', str(identity))
        if not row or row.get('organisation_id') != LOCAL_WORKSPACE_ID or row.get('member_id') != LOCAL_USER_ID:
            raise HTTPException(404, 'Session not found')
        return row

    def selected_recording(session: dict) -> dict | None:
        if not session.get('conversation_id'):
            return None
        row = store.get('conversations', session['conversation_id'])
        if not row or row.get('capture_state') == 'deleted' or row.get('owner_id') != LOCAL_USER_ID or row.get('organisation_id') != LOCAL_WORKSPACE_ID:
            return None
        return row if recording_matches(session, row) else None

    def transcript_preview(recording: dict | None) -> tuple[list[dict], str]:
        from app.voice_ai.live import analysis_turns
        if not recording:
            return [], ''
        transcript = store.get_transcript(recording['id'])
        version = transcript.get('version') or {}
        speakers = {item.get('speaker_ref'): item for item in transcript.get('speakers') or []}
        turns = []
        for item in transcript.get('turns') or []:
            turn = dict(item)
            assignment = speakers.get(turn.get('speaker_ref')) or {}
            if not assignment.get('is_provisional', True):
                turn['speaker_role'] = assignment.get('role')
                turn['speaker_name'] = assignment.get('display_name')
            turn['translation_state'] = 'complete' if str(turn.get('english_text') or '').strip() else 'pending'
            turn.setdefault('created_at', recording.get('created_at'))
            turns.append(turn)
        turns = analysis_turns(turns, version.get('coverage_samples'))
        turns.sort(key=lambda item: (item.get('start_seconds') or 0, item.get('ordinal') or 0))
        return turns, str(version.get('id') or '')

    def history(session: dict) -> dict:
        recordings = [public_recording(row) for row in store.list('conversations')
                      if row.get('capture_state') != 'deleted' and row.get('upload_state') == 'saved'
                      and row.get('organisation_id') == LOCAL_WORKSPACE_ID and row.get('owner_id') == LOCAL_USER_ID
                      and recording_matches(session, row)]
        recordings.sort(key=lambda item: item.get('created_at') or '', reverse=True)
        selected = selected_recording(session)
        turns, _ = transcript_preview(selected)
        return {'session': session, 'conversation_id': selected['id'] if selected else None,
                'messages': [], 'recordings': [], 'recorder_sessions': recordings,
                'insights': [], 'transcript_preview': turns}

    @app.get('/api/chats')
    def chats():
        rows = [row for row in store.list('sales_sessions')
                if row.get('organisation_id') == LOCAL_WORKSPACE_ID and row.get('member_id') == LOCAL_USER_ID]
        return sorted(rows, key=lambda item: item.get('updated_at') or '', reverse=True)[:50]

    @app.get('/api/session-contacts')
    def contacts(search: str = Query('', max_length=200)):
        query = search.strip().casefold()
        result = []
        for row in chats():
            if query and query not in ' '.join(str(row.get(key) or '') for key in ('name', 'phone', 'email')).casefold():
                continue
            recording = selected_recording(row)
            status = 'Recording' if recording and recording.get('capture_state') == 'recording' else 'Session'
            result.append({'id': f"session:{row['id']}", 'session_id': row['id'],
                           'name': row['name'], 'phone': row['phone'], 'email': row['email'],
                           'sources': ['Session'], 'records': {}, 'recording_status': status})
        return {'contacts': result, 'errors': []}

    @app.post('/api/voice-sessions')
    def open_session(body: SessionInput):
        with store.transaction() as db:
            rows = [json.loads(item['data']) for item in db.execute("select data from records where table_name='sales_sessions'")]
            existing = next((row for row in rows if row.get('phone') == body.phone
                             and row.get('organisation_id') == LOCAL_WORKSPACE_ID and row.get('member_id') == LOCAL_USER_ID), None)
            row = {**(existing or {}), **body.model_dump(),
                   'id': existing['id'] if existing else str(uuid4()), 'title': f'{body.name} voice session',
                   'organisation_id': LOCAL_WORKSPACE_ID, 'member_id': LOCAL_USER_ID,
                   'status': 'active', 'created_at': existing.get('created_at') if existing else now(),
                   'updated_at': now(), 'conversation_id': existing.get('conversation_id') if existing else None,
                   'context': existing.get('context', {}) if existing else {}}
            db.execute("insert into records values('sales_sessions',?,?) on conflict(table_name,id) do update set data=excluded.data", (row['id'], encoded(row)))
        return {**history(row), 'customer': {'id': row['id'], **body.model_dump()}}

    @app.get('/api/voice-sessions/{session_id}/history')
    def session_history(session_id: UUID):
        return history(session_row(session_id))

    @app.get('/api/voice-sessions/{session_id}/live')
    async def live(session_id: UUID):
        def snapshot():
            session = session_row(session_id)
            recording = selected_recording(session)
            turns, revision = transcript_preview(recording)
            jobs = store.list('jobs', {'conversation_id': recording['id']}) if recording else []
            return session, recording, turns, revision, jobs
        session, recording, turns, revision, jobs = await asyncio.to_thread(snapshot)
        insights = await analysis.snapshot(str(session_id), recording, turns)
        notices = [
            {'code': 'local_demo_catalogue', 'message': 'Suggestions use the local demo catalogue. Products, prices, and stock are sample data; Zoho Books is not connected.'},
        ]
        if recording and recording.get('processing_error_code'):
            notices.insert(0, {'code': recording['processing_error_code'], 'message': 'Speech processing needs attention. Your captured audio is preserved; check the managed speech connections.'})
        return {'session_id': str(session_id), 'conversation_id': recording['id'] if recording else None,
                'revision': revision, 'recording': public_recording(recording) if recording else None,
                'transcript_preview': turns, **insights, 'processing_mode': 'progressive',
                'processing_status': _processing_status(recording, turns, jobs),
                'provider_readiness': {
                    'speech_configured': bool(local_runtime_credentials(settings, store, 'elevenlabs').get('api_key')),
                    'translation_configured': bool(local_runtime_credentials(settings, store, 'openai-translator').get('api_key')),
                    'analysis_configured': bool(local_runtime_credentials(settings, store, 'openai-translator').get('api_key')),
                },
                'catalogue_source': 'local-demo', 'demo': True, 'notices': [*notices, *insights['notices']]}

    @app.post('/api/voice-sessions/{session_id}/conversation')
    def link_recording(session_id: UUID, body: SessionLink):
        session = session_row(session_id)
        identity = str(body.conversation_id) if body.conversation_id else None
        if identity:
            recording = store.get('conversations', identity)
            if not recording or recording.get('capture_state') == 'deleted' or recording.get('organisation_id') != LOCAL_WORKSPACE_ID or recording.get('owner_id') != LOCAL_USER_ID or not recording_matches(session, recording):
                raise HTTPException(404, 'Conversation not found for this customer')
            # The local progressive worker uses this marker to choose the live
            # Sessions cadence without changing unrelated enquiry recordings.
            store.patch('conversations', identity, {'live_session': True, 'live_session_id': str(session_id)})
            # The recording callback and first uploads can race. Linking must
            # also plan already confirmed audio, even if capture is now paused.
            store.plan_live_windows(identity, settings.live_window_seconds, settings.context_seconds)
        store.patch('sales_sessions', str(session_id), {'conversation_id': identity})
        return {'linked': True, 'session_id': str(session_id), 'conversation_id': identity}

    @app.get('/api/voice-sessions/{session_id}/notes')
    def notes(session_id: UUID):
        session_row(session_id)
        values = store.list('sales_notes', {'session_id': str(session_id)})
        return {'notes': sorted(values, key=lambda item: item['created_at'])}

    @app.post('/api/voice-sessions/{session_id}/notes')
    def add_note(session_id: UUID, body: SessionNote):
        session_row(session_id)
        content = body.content.strip()
        if not content:
            raise HTTPException(422, 'Note cannot be empty')
        note = {'id': str(uuid4()), 'session_id': str(session_id), 'content': content,
                'created_at': now(), 'author_id': LOCAL_USER_ID, 'author_name': 'Local user'}
        store.put('sales_notes', note['id'], note)
        return {'note': note}

    @app.get('/api/voice-sessions/{session_id}/context')
    def context(session_id: UUID):
        value = session_row(session_id).get('context') or {}
        return {'product_requirements': value.get('product_requirements') or [], 'cart': value.get('cart') or []}

    @app.put('/api/voice-sessions/{session_id}/context')
    def save_context(session_id: UUID, body: SessionContext):
        session_row(session_id)
        store.patch('sales_sessions', str(session_id), {'context': body.model_dump()})
        return body.model_dump()

    @app.get('/api/zoho/status')
    def zoho_status():
        return {'connected': False, 'organization_id': None, 'mode': 'local'}

    @app.get('/api/products')
    async def products(search: str = Query('', max_length=200), page: int = Query(1, ge=1)):
        from .demo_catalogue import LocalDemoCatalogueClient
        result = await LocalDemoCatalogueClient().search_books_items('local-demo', search, None, page)
        return {**result, 'mode': 'local', 'notice': 'Demo inventory for local testing. These are sample products, prices and stock.'}

    @app.get('/api/document-contacts')
    def document_contacts():
        return {'contacts': [], 'mode': 'local'}

    from app.sales_documents import DocumentRepository, SalesDocumentService, install_sales_document_routes

    def document_service(session_id):
        session = session_row(session_id)
        scope = {'organisation_id': LOCAL_WORKSPACE_ID, 'member_id': LOCAL_USER_ID, 'session_id': str(session_id)}
        return SalesDocumentService(DocumentRepository(scope, local_store=store),
                                    {key: session.get(key) or '' for key in ('name', 'phone', 'email')}, demo=True)

    install_sales_document_routes(app, document_service)
