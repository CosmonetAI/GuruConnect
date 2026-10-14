import pytest
from inference.worker import validate_audio_url


def test_job_scoped_private_audio_destination():
    job={'conversation_id':'conv-1'}
    url='https://project.supabase.co/storage/v1/object/sign/recorder-artifacts/org/user/conv-1/artifacts/job/input.wav?token=secret'
    assert validate_audio_url(url,'https://project.supabase.co',job)==url


@pytest.mark.parametrize('url',[
 'http://project.supabase.co/storage/v1/object/sign/recorder-artifacts/org/user/conv-1/a.wav',
 'https://attacker.example/storage/v1/object/sign/recorder-artifacts/org/user/conv-1/a.wav',
 'https://project.supabase.co/storage/v1/object/sign/recorder-artifacts/org/user/conv-2/a.wav',
 'https://user:password@project.supabase.co/storage/v1/object/sign/recorder-artifacts/conv-1/a.wav',
 'https://project.supabase.co/storage/v1/object/sign/recorder-artifacts/conv-1/../a.wav',
 'https://project.supabase.co/storage/v1/object/public/recorder-artifacts/conv-1/a.wav'])
def test_rejects_ssrf_and_cross_conversation_urls(url):
    with pytest.raises(ValueError):validate_audio_url(url,'https://project.supabase.co',{'conversation_id':'conv-1'})
