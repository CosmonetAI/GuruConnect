-- Three-minute mono 16 kHz WAV chunks are 5,760,044 bytes including the WAV header.
-- Keep headroom while preserving the recorder's 3-minute chunking policy.
update storage.buckets
set file_size_limit = 6000000,
    allowed_mime_types = array['audio/wav']
where id = 'recorder-audio';
