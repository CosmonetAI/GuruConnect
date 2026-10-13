from types import SimpleNamespace
import sys
import pytest
from recorder.audio import pcm_to_wav
from inference.engine import Engine


@pytest.mark.parametrize('failed',[False,True])
def test_speaker_only_retry_keeps_english_and_never_reexecutes_asr(tmp_path,monkeypatch,failed):
    monkeypatch.setitem(sys.modules,'torch',SimpleNamespace(from_numpy=lambda values:SimpleNamespace(unsqueeze=lambda axis:values)))
    class Model:
        def transcribe(self,*args,**kwargs):raise AssertionError('Speaker-only retry must not call Whisper')
    class Pipeline:
        def __call__(self,*args,**kwargs):
            if failed:raise RuntimeError('Explicit test-only diarization failure')
            annotation=SimpleNamespace(itertracks=lambda **kwargs:iter([(SimpleNamespace(start=0.,end=1.),0,'voice-a')]))
            return SimpleNamespace(speaker_diarization=annotation)
    engine=Engine.__new__(Engine);engine.model=Model();engine.pipeline=Pipeline();engine.model_version='test-fixture-not-live';engine.device='cpu'
    audio=tmp_path/'test.wav';audio.write_bytes(pcm_to_wav(b'\0\0'*16000))
    base={'language':'en','english_segments':[{'start':0.,'end':1.,'text':'Saved English, number AB-120'}],'source_segments':None}
    result=engine._process_block(str(audio),{'kind':'diarize','job_id':'job-retry','run_id':'run','base_result':base,'config_snapshot':{'source_pass':True}})
    assert result['english_segments'][0]['text']==base['english_segments'][0]['text']
    assert result['diarization_status']==('failed' if failed else 'completed')
    assert result['provenance']['speaker_only_retry']
    assert not result['provenance']['additional_source_pass']
