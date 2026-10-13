import json
import pytest
import httpx
from recorder.audio import pcm_to_wav
from recorder.providers import ElevenLabsAdapter, AzureTranslator, ProviderError, checked_response, language_code, protect_terms


@pytest.fixture
def transport(monkeypatch):
    client=httpx.AsyncClient
    def install(handler):
        monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:client(transport=httpx.MockTransport(handler),**kwargs))
    return install


@pytest.mark.parametrize('source,target',[('kan','kn'),('hin','hi'),('ben','bn'),('eng','en'),('auto',None)])
def test_correct_language_mapping(source,target):assert language_code(source)==target


@pytest.mark.asyncio
async def test_elevenlabs_real_request_contract_and_source_language(transport):
    def handler(request):
        body=request.content.decode('latin1')
        assert 'name="model_id"\r\n\r\nscribe_v2' in body
        assert 'name="diarize"\r\n\r\ntrue' in body
        assert 'name="language_code"\r\n\r\nkn' in body
        assert 'name="use_multi_channel"\r\n\r\nfalse' in body
        assert 'name="transcript_edit"' not in body and 'name="num_speakers"' not in body
        return httpx.Response(200,json={'language_code':'kan','language_probability':0.9,'text':'ನಮಸ್ಕಾರ','words':[{'type':'word','start':0.1,'end':1,'speaker_id':'speaker_0','text':'ನಮಸ್ಕಾರ'}]})
    transport(handler)
    result=await ElevenLabsAdapter('test-only').process(pcm_to_wav(b'\0\0'*16000),'kn','window1')
    assert result['english_segments'] is None
    assert result['source_segments'][0]['text']=='ನಮಸ್ಕಾರ'
    assert result['language']=='kn'


@pytest.mark.asyncio
async def test_timeout_preserves_uncertain_paid_execution(transport):
    def handler(request):raise httpx.ReadTimeout('test timeout',request=request)
    transport(handler)
    with pytest.raises(ProviderError) as error:await ElevenLabsAdapter('test-only').process(pcm_to_wav(b'\0\0'*16000))
    assert error.value.uncertain and not error.value.retryable


@pytest.mark.asyncio
@pytest.mark.parametrize('status,code,uncertain',[(401,'invalid_credentials',False),(429,'provider_rate_limited',False),(503,'provider_unavailable',True)])
async def test_provider_safe_classification(transport,status,code,uncertain):
    transport(lambda request:httpx.Response(status,json={'private':'must-not-be-returned'}))
    with pytest.raises(ProviderError) as error:await ElevenLabsAdapter('test-only').process(pcm_to_wav(b'\0\0'*16000))
    assert str(error.value)==code and error.value.uncertain is uncertain


def test_protection_escapes_markup_and_keeps_repeated_codes():
    text='<script> Buy AB-123 twice, 25 kg and 25 kg'
    protected,terms=protect_terms(text)
    assert '<script>' not in protected
    assert 'notranslate' in protected and terms.count('25 kg')==2
    assert 'AB-123' in terms


@pytest.mark.asyncio
async def test_azure_preserves_codes_and_does_not_force_file_language(transport):
    def handler(request):
        assert request.url.params['to']=='en' and 'from' not in request.url.params
        source=json.loads(request.content)[0]['Text']
        assert 'notranslate' in source
        return httpx.Response(200,json=[{'translations':[{'text':'Order <span class="notranslate">X123</span> and <span class="notranslate">25</span>','to':'en'}]}])
    transport(handler)
    assert await AzureTranslator('test-key','centralindia').translate('ऑर्डर X123 25')=='Order X123 and 25'


@pytest.mark.asyncio
async def test_bad_numeric_translation_is_visible_and_keeps_source(transport):
    transport(lambda request:httpx.Response(200,json=[{'translations':[{'text':'Order X124','to':'en'}]}]))
    with pytest.raises(ProviderError,match='translation_term_preservation_failed'):await AzureTranslator('test-key','centralindia').translate('ऑर्डर X123')


@pytest.mark.asyncio
async def test_public_translator_health_is_not_credential_success(transport):
    transport(lambda request:httpx.Response(200,json={'translation':{'en':{}}}))
    assert (await AzureTranslator('test-key','centralindia').test(False))['connected'] is False


@pytest.mark.asyncio
async def test_scribe_health_requires_authenticated_account_metadata(transport):
    def handler(request):
        assert request.url.path=='/v1/user'
        assert request.headers['xi-api-key']=='test-only'
        return httpx.Response(200,json={'user_id':'test-fixture-user'})
    transport(handler)
    assert (await ElevenLabsAdapter('test-only').test(False))['connected']


@pytest.mark.asyncio
@pytest.mark.parametrize('detail',[
    {'status':'unauthorized'},
    {'code':'unauthorized','status':'invalid_api_key'},
])
async def test_scribe_legacy_unauthorized_identifies_exact_user_read_restriction(transport,detail):
    message='The API key you used is missing the permission user_read to execute this operation.'
    transport(lambda request:httpx.Response(401,json={'detail':{**detail,'message':message}}))
    with pytest.raises(ProviderError) as error:
        await ElevenLabsAdapter('test-only').test(False)
    assert error.value.code=='metadata_permission_denied'
    assert not error.value.retryable and not error.value.uncertain
    assert 'user_read' not in str(error.value) and 'message' not in error.value.__dict__


@pytest.mark.parametrize('status,detail,metadata,expected',[
    (401,{'status':'unauthorized'},False,'invalid_credentials'),
    (401,{'code':'invalid_api_key','status':'unauthorized'},True,'invalid_credentials'),
    (403,{'code':'unknown_error','status':'unauthorized'},True,'provider_access_denied'),
    (403,{'code':'unauthorized'},True,'metadata_permission_denied'),
    (400,{'code':'unauthorized'},True,'invalid_credentials'),
])
def test_legacy_user_read_restriction_is_limited_to_metadata_and_authoritative_tag(status,detail,metadata,expected):
    message='The API key you used is missing the permission user_read to execute this operation.'
    with pytest.raises(ProviderError) as error:
        checked_response(httpx.Response(status,json={'detail':{**detail,'message':message}}),metadata=metadata)
    assert error.value.code==expected


@pytest.mark.parametrize('message',[
    None,
    'Unauthorized',
    'An error mentions user_read.',
    'The API key you used is missing the permission voices_read to execute this operation.',
    'The API key you used is missing the permission user_read to execute this operation. fixture-secret',
    {'private':'fixture-secret'},
])
def test_bare_or_untrusted_unauthorized_messages_never_become_metadata_permission(message):
    with pytest.raises(ProviderError) as error:
        checked_response(httpx.Response(401,json={'detail':{'status':'unauthorized','message':message}}),metadata=True)
    assert error.value.__dict__=={'code':'invalid_credentials','retryable':False,'uncertain':False}
    assert 'fixture-secret' not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize('status,detail,expected',[
    (400,{'code':'invalid_api_key'},'invalid_credentials'),
    (403,{'code':'invalid_api_key'},'invalid_credentials'),
    (401,{'status':'missing_permissions'},'metadata_permission_denied'),
    (403,{'code':'insufficient_permissions'},'metadata_permission_denied'),
    (401,{'code':'quota_exceeded','status':'invalid_api_key'},'provider_quota_exceeded'),
    (402,{'code':'insufficient_credits'},'provider_quota_exceeded'),
    (403,{'code':'subscription_required'},'provider_plan_access_denied'),
    (403,{'code':'model_access_denied'},'provider_model_access_denied'),
    (403,{'code':'unknown_ip_policy_error'},'provider_access_denied'),
    (403,{'code':'unknown_error','status':'missing_permissions'},'provider_access_denied'),
    (404,{},'provider_request_rejected'),
])
async def test_scribe_account_check_distinguishes_key_scope_and_quota_failures(transport,status,detail,expected):
    secret='fixture-key-must-never-be-disclosed'
    transport(lambda request:httpx.Response(status,json={'detail':{**detail,'message':secret},'private':secret}))
    with pytest.raises(ProviderError) as error:
        await ElevenLabsAdapter(secret).test(False)
    assert error.value.code==expected
    assert not error.value.retryable and not error.value.uncertain
    assert secret not in str(error.value) and secret not in repr(error.value.__dict__)


@pytest.mark.asyncio
@pytest.mark.parametrize('status,detail,expected',[
    (400,{'code':'invalid_api_key'},'invalid_credentials'),
    (401,{'status':'missing_permissions'},'provider_permission_denied'),
    (401,{'status':'quota_exceeded'},'provider_quota_exceeded'),
    (402,{'code':'payment_required'},'provider_payment_required'),
])
async def test_scribe_processing_uses_structured_rejection_without_metadata_scope(transport,status,detail,expected):
    transport(lambda request:httpx.Response(status,json={'detail':detail}))
    with pytest.raises(ProviderError) as error:
        await ElevenLabsAdapter('test-only').process(pcm_to_wav(b'\0\0'*16000))
    assert error.value.code==expected and not error.value.uncertain


@pytest.mark.parametrize('body',[
    {'detail':'fixture-secret-plain-message'},
    {'detail':{'code':'fixture-secret-untrusted-tag','message':'fixture-secret-plain-message'}},
    {'detail':{'code':['invalid_api_key'],'status':{'private':'fixture-secret'}}},
    ['fixture-secret'],
])
def test_unknown_error_bodies_cannot_supply_application_errors_or_secret_messages(body):
    with pytest.raises(ProviderError) as error:
        checked_response(httpx.Response(400,json=body),metadata=True)
    assert str(error.value)=='provider_request_rejected'
    assert error.value.__dict__=={'code':'provider_request_rejected','retryable':False,'uncertain':False}


def test_non_json_access_denial_remains_safe_and_does_not_invent_metadata_permission():
    with pytest.raises(ProviderError) as error:
        checked_response(httpx.Response(403,content=b'<html>fixture-secret</html>'),metadata=True)
    assert str(error.value)=='provider_access_denied'
    assert error.value.__cause__ is None and error.value.__context__ is None


@pytest.mark.parametrize('status,expected,uncertain',[
    (429,'provider_rate_limited',False),
    (503,'provider_unavailable',True),
])
def test_transient_statuses_preserve_retry_and_paid_execution_uncertainty(status,expected,uncertain):
    with pytest.raises(ProviderError) as error:
        checked_response(httpx.Response(status,json={'detail':{'code':'invalid_api_key'}}),metadata=True)
    assert error.value.code==expected and error.value.retryable
    assert error.value.uncertain is uncertain


@pytest.mark.asyncio
@pytest.mark.parametrize('body',[{'translation':{'en':{}}},{'user_id':''},{'user_id':True},[]])
async def test_scribe_account_check_never_accepts_public_or_malformed_metadata(transport,body):
    transport(lambda request:httpx.Response(200,json=body))
    with pytest.raises(ProviderError,match='provider_invalid_response'):
        await ElevenLabsAdapter('test-only').test(False)


@pytest.mark.asyncio
async def test_translator_timeout_is_held_for_charge_reconciliation(transport):
    def handler(request):raise httpx.ReadTimeout('Explicit timeout fixture',request=request)
    transport(handler)
    with pytest.raises(ProviderError) as error:await AzureTranslator('test-only','region').translate('नमस्ते')
    assert error.value.uncertain


@pytest.mark.asyncio
async def test_long_turn_is_split_into_bounded_standard_requests(transport):
    calls=[]
    def handler(request):
        text=json.loads(request.content)[0]['Text'];calls.append(len(text))
        assert len(text)<=45000
        return httpx.Response(200,json=[{'translations':[{'text':text,'to':'en'}]}])
    transport(handler)
    result=await AzureTranslator('test-only','region').translate('Hello world '*5000)
    assert len(calls)>1 and result.count('Hello')==5000
