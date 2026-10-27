"""Bounded WAV framing and concurrent streaming MP3 encoding."""
from __future__ import annotations

import asyncio
import struct
from collections.abc import AsyncIterator


def wav_header(samples: int) -> bytes:
    data_bytes = samples * 2
    return b"RIFF" + struct.pack("<I4s4sIHHIIHH4sI", 36 + data_bytes, b"WAVE", b"fmt ", 16,
                                 1, 1, 16000, 32000, 2, 16, b"data", data_bytes)


async def mp3_chunks(pcm: AsyncIterator[bytes], samples: int, *, timeout_seconds: float = 300):
    """Pump input, binary output and stderr concurrently to avoid pipe deadlocks.

    Cancellation (including client disconnect) kills the child and closes its
    input producer. Neither provider errors nor FFmpeg stderr escape to clients.
    """
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "wav", "-i", "pipe:0",
        "-c:a", "libmp3lame", "-b:a", "128k", "-f", "mp3", "pipe:1",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None

    async def feed():
        try:
            process.stdin.write(wav_header(samples))
            await process.stdin.drain()
            async for chunk in pcm:
                process.stdin.write(chunk)
                await process.stdin.drain()
        finally:
            process.stdin.close()
            await pcm.aclose()

    async def drain_errors():
        # Discard diagnostics while keeping stderr's pipe bounded and drained.
        while await process.stderr.read(4096):
            pass

    producer = asyncio.create_task(feed())
    errors = asyncio.create_task(drain_errors())
    try:
        async with asyncio.timeout(timeout_seconds):
            while chunk := await process.stdout.read(65536):
                yield chunk
            await producer
            await errors
            if await process.wait() != 0:
                raise RuntimeError("playback_encoding_failed")
    finally:
        producer.cancel()
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        async def discard_output():
            # Process.wait() also waits for pipe transports to finish. A client
            # can disconnect with stdout full, so drain after kill before wait.
            while await process.stdout.read(65536):
                pass
        output = asyncio.create_task(discard_output())
        reaped = asyncio.create_task(process.wait())
        try:
            async with asyncio.timeout(3):
                await asyncio.gather(producer, output, errors, reaped, return_exceptions=True)
        except TimeoutError:
            for task in (producer, output, errors, reaped):
                task.cancel()
            await asyncio.gather(producer, output, errors, reaped, return_exceptions=True)
