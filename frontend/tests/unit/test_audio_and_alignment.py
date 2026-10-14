import io
import struct
import wave
import pytest
from recorder.audio import assemble_wav, validate_wav, pcm_to_wav, ownership_segments
from inference.alignment import align_segments


def test_independent_chunks_and_tail_assembly():
    chunks = [pcm_to_wav(struct.pack('<h', i) * n) for i, n in [(1,80000),(2,80000),(3,17)]]
    for chunk, expected in zip(chunks,[80000,80000,17]): assert validate_wav(chunk).samples == expected
    complete=validate_wav(assemble_wav(chunks),max_samples=200000)
    assert complete.samples==160017
    excerpt=validate_wav(assemble_wav(chunks,79998,80003))
    assert struct.unpack('<5h',excerpt.pcm)==(1,1,2,2,2)


@pytest.mark.parametrize('mutation', ['truncated','appended','rate','stereo','count'])
def test_rejects_corrupt_or_untrusted_client_audio(mutation):
    raw=bytearray(pcm_to_wav(b'\0\0'*80000)); expected=80000
    if mutation=='truncated':raw=raw[:-2]
    if mutation=='appended':raw+=b'evil'
    if mutation=='rate':struct.pack_into('<I',raw,24,48000)
    if mutation=='stereo':struct.pack_into('<H',raw,22,2)
    if mutation=='count':expected=79999
    with pytest.raises(ValueError):validate_wav(bytes(raw),expected)


def test_overlap_ownership_preserves_genuine_repetitions():
    segment={'start':0,'end':3,'text':'yes yes yes','speaker':'speaker1','words':[
        {'start':0,'end':0.8,'text':'yes '},{'start':1,'end':1.8,'text':'yes '},{'start':2,'end':2.8,'text':'yes'}]}
    owned=ownership_segments([segment],28,30,60,'window2')
    assert owned[0]['text']=='yes';assert owned[0]['speaker']=='window2:speaker1'
    assert owned[0]['start']==30


def test_multi_speaker_english_segment_is_not_assigned_wholesale():
    turns=[{'start':0,'end':10,'text':'Two speakers in one translated sentence'}]
    intervals=[{'start':0,'end':8,'speaker':'a'},{'start':8,'end':10,'speaker':'b'}]
    aligned=align_segments(turns,intervals,'run1')
    assert aligned[0]['speaker'] is None
    assert aligned[0]['speaker_confidence']=='uncertain'


def test_overlap_and_low_coverage_remain_unresolved():
    segment=[{'start':0,'end':3,'text':'overlap'}]
    overlapping=[{'start':0,'end':3,'speaker':'a'},{'start':1,'end':2,'speaker':'b'}]
    assert align_segments(segment,overlapping,'job')[0]['speaker_confidence']=='overlap'
    assert align_segments(segment,[{'start':0,'end':0.3,'speaker':'a'}],'job')[0]['speaker'] is None


def test_supported_speaker_is_scoped_to_run():
    segment=[{'start':0,'end':3,'text':'one voice'}]; intervals=[{'start':0,'end':3,'speaker':'a'}]
    assert align_segments(segment,intervals,'run-a')[0]['speaker']!='run-b:a'


def test_native_turn_and_diarization_references_keep_the_same_run_scope():
    segment={'start':0.,'end':1.,'text':'native English','speaker':'run-a:job-a:voice-a'}
    assert ownership_segments([segment],0,0,30,'run-a:window-a')[0]['speaker']=='run-a:job-a:voice-a'
