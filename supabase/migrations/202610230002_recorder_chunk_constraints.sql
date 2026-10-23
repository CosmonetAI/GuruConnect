-- Expand database constraints for the recorder's three-minute chunks.
-- The original schema allowed only 10 seconds / 320,044 bytes.
alter table public.audio_chunks
  drop constraint if exists audio_chunks_sample_count_check,
  drop constraint if exists audio_chunks_bytes_check;

alter table public.audio_chunks
  add constraint audio_chunks_sample_count_check check (sample_count between 1 and 2880000),
  add constraint audio_chunks_bytes_check check (bytes between 46 and 6000000);
