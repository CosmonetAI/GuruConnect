"""Real, bounded AI requirements analysis for the private loopback workspace."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import logging
from types import SimpleNamespace

from filelock import FileLock, Timeout

from app.voice_ai.agents import ConversationMonitorAgent
from app.voice_ai.live import analysis_turns, batch_fingerprint, intent_keywords, pending_turns, search_catalogue, turn_fingerprint
from app.voice_ai.orchestrator import VoiceConversationOrchestrator
from app.voice_ai.workspace import (analysis_outcome, catalogue_due, failed_analysis_data, retain_catalogue_recommendations,
                                    requirement_signature, retry_failure, row_analysis_error, workspace_fields)
from .local_store import LOCAL_USER_ID, LOCAL_WORKSPACE_ID, encoded, now
from .provider_bootstrap import server_credentials

log = logging.getLogger('recorder.local_voice')


def local_runtime_credentials(settings, store, engine):
    return server_credentials(settings, engine) or store.secrets(engine)


def _processing_status(recording, turns, jobs):
    if not recording:
        return {'stage': 'waiting'}
    if recording.get('final_state') == 'complete' and recording.get('english_state') == 'complete':
        return {'stage': 'complete'}
    active = [job for job in jobs if job.get('state') in {'running', 'queued', 'retry'}]
    running = [job for job in active if job.get('state') == 'running']
    for group in (running, active):
        if any(job.get('kind') in {'translate', 'live_translate'} for job in group):
            return {'stage': 'translating'}
        if any(job.get('kind') in {'transcribe', 'live_transcribe'} for job in group):
            return {'stage': 'transcribing'}
        if group:
            return {'stage': 'waiting'}
    code = recording.get('processing_error_code')
    if code:
        return {'stage': 'blocked' if code in {'engine_unconfigured', 'translator_unconfigured', 'connection_disabled', 'connection_disconnected'} else 'failed', 'code': code}
    if any(turn.get('english_text') for turn in turns):
        return {'stage': 'up_to_date'}
    return {'stage': 'waiting'}


class LocalVoiceAnalysis:
    def __init__(self, settings, store, *, orchestrator_factory=None, catalogue_factory=None):
        self.settings, self.store = settings, store
        self.tasks: dict[str, asyncio.Task] = {}
        self.orchestrator_factory = orchestrator_factory
        self.catalogue_factory = catalogue_factory

    def _key(self):
        return local_runtime_credentials(self.settings, self.store, 'openai-translator').get('api_key')

    def _current(self, session_id, conversation_id):
        session = self.store.get('sales_sessions', session_id)
        recording = self.store.get('conversations', conversation_id)
        return bool(session and recording and session.get('conversation_id') == conversation_id
                    and session.get('member_id') == LOCAL_USER_ID and session.get('organisation_id') == LOCAL_WORKSPACE_ID
                    and recording.get('owner_id') == LOCAL_USER_ID and recording.get('organisation_id') == LOCAL_WORKSPACE_ID
                    and recording.get('capture_state') != 'deleted')

    def _state(self, conversation_id):
        stored = self.store.get('sales_ai_context', conversation_id) or {}
        rows = self.store.list('sales_ai_extractions', {'conversation_id': conversation_id})
        return stored, rows

    async def snapshot(self, session_id, recording, turns):
        identity = str(recording['id']) if recording else None
        stored, rows = await asyncio.to_thread(self._state, identity) if identity else ({}, [])
        context = stored.get('context') or {}
        catalogue = stored.get('catalogue') or {}
        pending = pending_turns(turns, rows)
        failed = retry_failure(rows, pending=bool(pending))
        retry_wait = bool(failed)
        configured = bool(self._key())
        fields = workspace_fields(context, catalogue)
        refresh_catalogue = catalogue_due(catalogue, stored.get('catalogue_updated_at') or stored.get('updated_at'), fields['requirements'])
        if fields['requirements'] and catalogue.get('requirements_signature') and catalogue['requirements_signature'] != requirement_signature(context):
            refresh_catalogue = True
        if identity and (pending or refresh_catalogue) and (configured or not pending) and identity not in self.tasks and len(self.tasks) < 2 and not retry_wait:
            task = asyncio.create_task(self._analyse(str(session_id), identity))
            self.tasks[identity] = task
            task.add_done_callback(lambda completed, cid=identity: self.tasks.pop(cid, None) if self.tasks.get(cid) is completed else None)
        failed_status, analysis_error = row_analysis_error(failed)
        status = ('running' if identity in self.tasks and pending else 'unavailable' if pending and not configured else failed_status if retry_wait
                  else 'pending' if pending else 'complete' if any(turn.get('english_text') for turn in turns) else 'waiting_for_translation')
        notices = list(catalogue.get('notices') or [])
        if pending and not configured:
            analysis_error = {'code': 'voice_analysis_not_configured', 'message': 'AI product analysis is waiting for the backend OpenAI key. Your transcript is preserved.'}
            notices.append({'code': 'voice_analysis_not_configured', 'message': 'AI product analysis is waiting for the backend OpenAI key. Your transcript is preserved.'})
        if retry_wait:
            notices.append({'code': 'voice_analysis_failed', 'message': analysis_error['message']})
        return {'context': context, 'insights': sorted(rows, key=lambda row: row.get('updated_at') or '')[-30:],
                **fields, 'keywords': intent_keywords(context),
                'recommendations': context.get('recent_recommendations') or [], 'analysis_status': status,
                'analysis_error': analysis_error if status in {'failed', 'unavailable'} else None,
                'catalogue_status': 'searching' if identity in self.tasks else catalogue.get('status', 'waiting'),
                'notices': notices}

    def _orchestrator(self):
        if self.orchestrator_factory:
            return self.orchestrator_factory()
        from app.settings import settings as model_settings
        configuration = SimpleNamespace(openai_api_key=self._key(),
            voice_extraction_model=getattr(self.settings, 'voice_extraction_model', '') or model_settings.voice_extraction_model,
            openai_model=model_settings.openai_model)
        orchestrator = VoiceConversationOrchestrator()
        orchestrator.monitor = ConversationMonitorAgent(settings_override=configuration)
        return orchestrator

    def _catalogue(self):
        if self.catalogue_factory:
            return self.catalogue_factory()
        from .demo_catalogue import LocalDemoCatalogueClient
        return LocalDemoCatalogueClient()

    async def _analyse(self, session_id, conversation_id):
        # The OS lock survives multiple API instances; it is released on process
        # death. Completed speech fingerprints and context are committed together.
        lock = FileLock(self.store.root / f'voice-analysis-{conversation_id}.lock', mode=0o600, thread_local=False)
        fingerprint = None
        try:
            try:
                await asyncio.to_thread(lock.acquire, timeout=0)
            except Timeout:
                return
            if not await asyncio.to_thread(self._current, session_id, conversation_id):
                return
            transcript = await asyncio.to_thread(self.store.get_transcript, conversation_id)
            turns = analysis_turns(transcript.get('turns') or [], (transcript.get('version') or {}).get('coverage_samples'))
            turns.sort(key=lambda turn: (turn.get('start_seconds') or 0, turn.get('ordinal') or 0))
            stored, rows = await asyncio.to_thread(self._state, conversation_id)
            selected = pending_turns(turns, rows)[:24]
            context = deepcopy(stored.get('context') or {})
            if not selected and not workspace_fields(context, stored.get('catalogue') or {})['requirements']:
                return
            previous_context = deepcopy(context)
            if selected:
                fingerprint = batch_fingerprint(selected)
                chunk = {'id': selected[-1].get('id'), 'conversation_id': conversation_id, 'fingerprint': fingerprint,
                     'turn_fingerprints': [turn_fingerprint(turn) for turn in selected],
                     'english_text': '\n'.join(turn['english_text'] for turn in selected),
                     'end_seconds': selected[-1].get('end_seconds'),
                     'recent_dialogue': [{key: turn.get(key) for key in ('english_text', 'speaker_ref', 'speaker_role', 'start_seconds', 'end_seconds')}
                                         for turn in turns if turn.get('analysis_ready') and (turn.get('start_seconds') or 0) <= (selected[-1].get('start_seconds') or 0) and turn.get('english_text')][-24:]}
                result = await asyncio.wait_for(self._orchestrator().run(chunk=chunk, durable_context=context), timeout=45)
                status, error = analysis_outcome(result)
                if status != 'complete':
                    if await asyncio.to_thread(self._current, session_id, conversation_id):
                        await asyncio.to_thread(self.store.put, 'sales_ai_extractions', f'{conversation_id}:{fingerprint}',
                            {'id': fingerprint, 'conversation_id': conversation_id, 'fingerprint': fingerprint,
                             'status': 'failed', 'updated_at': now(), 'error_message': error['code'],
                             'data': failed_analysis_data(result, {'turn_fingerprints': chunk['turn_fingerprints']})})
                    return
                context = result['context']
            try:
                catalogue = await asyncio.wait_for(search_catalogue(self._catalogue(), context), timeout=20)
            except TimeoutError:
                catalogue = {'status': 'unavailable', 'items': [], 'queries': [], 'notices': [{
                    'code': 'catalogue_lookup_failed', 'message': 'Product search timed out. Your transcript and requirements are preserved.'}]}
            except Exception:
                catalogue = {'status': 'unavailable', 'items': [], 'queries': [], 'notices': [{
                    'code': 'catalogue_lookup_failed', 'message': 'Product search is temporarily unavailable. Your requirements are preserved.'}]}
            catalogue['requirements_signature'] = requirement_signature(context)
            retain_catalogue_recommendations(context, catalogue, previous_context)
            if not await asyncio.to_thread(self._current, session_id, conversation_id):
                return
            timestamp = now()
            state = {'conversation_id': conversation_id, 'context': context, 'catalogue': catalogue,
                     'updated_at': timestamp, 'catalogue_updated_at': timestamp}
            if not selected:
                await asyncio.to_thread(self.store.put, 'sales_ai_context', conversation_id, state)
                return
            extraction = {'id': fingerprint, 'conversation_id': conversation_id, 'fingerprint': fingerprint,
                          'status': 'complete', 'created_at': timestamp, 'updated_at': timestamp,
                          'data': {**result.get('extraction', {}), 'analysis_status': 'complete', '_route': result.get('route') or {},
                                   '_catalogue': catalogue,
                                   '_live': {'turn_fingerprints': chunk['turn_fingerprints']}}}
            await asyncio.to_thread(self._commit, conversation_id, fingerprint, state, extraction)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log.warning('local_voice_analysis_failed exception_type=%s', type(error).__name__)
            if fingerprint and await asyncio.to_thread(self._current, session_id, conversation_id):
                await asyncio.to_thread(self.store.put, 'sales_ai_extractions', f'{conversation_id}:{fingerprint}',
                    {'id': fingerprint, 'conversation_id': conversation_id, 'fingerprint': fingerprint,
                     'status': 'failed', 'updated_at': now(), 'error_message': type(error).__name__,
                     'data': {'analysis_status': 'failed', 'analysis_error': {'code': 'voice_analysis_failed',
                              'message': 'AI product analysis is temporarily unavailable and will retry. Your transcript is preserved.'}}})
        finally:
            if lock.is_locked:
                await asyncio.to_thread(lock.release)

    def _commit(self, conversation_id, fingerprint, state, extraction):
        with self.store.transaction() as db:
            for table, identity, value in [('sales_ai_context', conversation_id, state),
                                          ('sales_ai_extractions', f'{conversation_id}:{fingerprint}', extraction)]:
                db.execute('insert into records values(?,?,?) on conflict(table_name,id) do update set data=excluded.data', (table, identity, encoded(value)))

    async def stop(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()
