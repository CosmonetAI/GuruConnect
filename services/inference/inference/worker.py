"""Outbound-only, scoped worker protocol. No Supabase service-role credential."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import signal
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx

log = logging.getLogger("inference")


def validate_audio_url(value: str, origin: str, job: dict) -> str:
    actual, expected = urlparse(value), urlparse(origin)
    if actual.scheme != "https" or actual.netloc != expected.netloc or actual.username or actual.password:
        raise ValueError("invalid_audio_origin")
    if not actual.path.startswith("/storage/v1/object/sign/recorder-artifacts/") or ".." in actual.path.split("/"):
        raise ValueError("invalid_audio_path")
    if str(job["conversation_id"]) not in actual.path.split("/"):
        raise ValueError("invalid_audio_scope")
    return value


class Worker:
    def __init__(self):
        self.base = os.environ["API_URL"].rstrip("/")
        parsed = urlparse(self.base)
        if parsed.scheme != "https" and not (os.environ.get("ALLOW_INSECURE_LOCAL_API") == "true" and parsed.hostname in {"localhost","127.0.0.1"}):
            raise ValueError("worker_api_requires_https")
        self.storage_origin = os.environ["SUPABASE_STORAGE_ORIGIN"].rstrip("/")
        self.token = os.environ["INFERENCE_WORKER_TOKEN"]
        self.stopping = asyncio.Event(); self.engine = None
        self.client = httpx.AsyncClient(timeout=30, follow_redirects=False, headers={"Authorization":f"Bearer {self.token}"})

    async def call(self, path, payload):
        response = await self.client.post(f"{self.base}/internal/v1{path}", json=payload)
        if response.status_code in {401,403}: self.stopping.set(); raise RuntimeError("worker_token_revoked")
        response.raise_for_status(); return response.json()

    async def heartbeat(self, ready=True):
        await self.call("/heartbeat",{"ready":ready,"model_version":self.engine.model_version if self.engine else "loading-pinned-models","metrics":{}})

    async def download(self, job, path):
        audio = job.get("audio", {})
        url = validate_audio_url(job.get("audio_url") or audio["url"],self.storage_origin,job)
        expected_hash = job.get("sha256") or audio.get("sha256")
        expected_bytes = job.get("bytes") or audio.get("bytes")
        if not expected_hash or not expected_bytes or expected_bytes > 230_404_096:
            raise ValueError("invalid_audio_manifest")
        digest = hashlib.sha256(); count=0
        # Never send the worker's bearer token to a Storage URL.
        async with httpx.AsyncClient(timeout=120,follow_redirects=False) as downloader:
            async with downloader.stream("GET",url) as response:
                response.raise_for_status()
                with open(path,"wb") as output:
                    async for chunk in response.aiter_bytes():
                        count+=len(chunk)
                        if count>expected_bytes: raise ValueError("audio_size_exceeded")
                        digest.update(chunk);output.write(chunk)
        if count!=expected_bytes or digest.hexdigest()!=expected_hash: raise ValueError("audio_hash_mismatch")

    async def renew(self, job):
        while True:
            await asyncio.sleep(25)
            await self.heartbeat()
            await self.call(f"/jobs/{job['job_id']}/renew",{"lease_token":job["lease_token"],"fencing_token":job["fencing_token"]})

    async def artifact(self, job, payload):
        data=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
        lease={"lease_token":job["lease_token"],"fencing_token":job["fencing_token"],"sha256":hashlib.sha256(data).hexdigest(),"bytes":len(data)}
        permit=await self.call(f"/jobs/{job['job_id']}/artifacts/authorize",lease)
        actual,expected=urlparse(permit["upload_url"]),urlparse(self.storage_origin)
        if actual.scheme!="https" or actual.netloc!=expected.netloc or not actual.path.startswith('/storage/v1/object/upload/sign/recorder-artifacts/') or str(job['conversation_id']) not in actual.path.split('/') or '..' in actual.path.split('/') or actual.username:
            raise ValueError("invalid_artifact_upload_scope")
        async with httpx.AsyncClient(timeout=120,follow_redirects=False) as uploader:
            response=await uploader.request(permit.get("method","PUT"),permit["upload_url"],headers=permit.get("headers",{"Content-Type":"application/json"}),content=data)
            if response.status_code not in {200,201,400,409}:response.raise_for_status()
        confirmed=await self.call(f"/jobs/{job['job_id']}/artifacts/confirm",lease)
        return confirmed["artifact_id"]

    async def process(self, job):
        lease={"lease_token":job["lease_token"],"fencing_token":job["fencing_token"]}
        renewal=asyncio.create_task(self.renew(job))
        try:
            with tempfile.TemporaryDirectory(prefix="guru-inference-") as scratch:
                path=Path(scratch)/"input.wav";await self.download(job,path)
                job = {**job, "config_snapshot": job.get("config", {}), "final": job.get("kind") == "finalise"}
                result=await asyncio.to_thread(self.engine.process,str(path),job)
                # A revoked/expired renewal must fence the result even if GPU work completes.
                if renewal.done(): renewal.result()
                payload={key:result.get(key) for key in ("language","source_segments","english_segments","diarization_status","diarization_intervals","provenance")}
                for kind in ("source_segments","english_segments"):
                    for segment in payload.get(kind) or []:
                        segment.pop("words",None) if segment.get("words") is None else None
                artifact_id=await self.artifact(job,payload)
                await self.call(f"/jobs/{job['job_id']}/result",{**lease,**payload,"artifact_id":artifact_id})
        except Exception as exc:
            log.warning("inference_failed job_id=%s code=%s",job.get("job_id"),"inference_unavailable")
            try: await self.call(f"/jobs/{job['job_id']}/failure",{**lease,"error_code":"inference_unavailable","uncertain":False})
            except Exception: pass
        finally:
            renewal.cancel();await asyncio.gather(renewal,return_exceptions=True)

    async def run(self):
        from .engine import Engine
        await self.heartbeat(False)
        self.engine=await asyncio.to_thread(Engine)
        try:
            while not self.stopping.is_set():
                try:
                    await self.heartbeat()
                    claimed=await self.call("/claim",{"limit":1})
                    jobs=claimed.get("items",claimed.get("jobs",[]))
                    if jobs: await self.process(jobs[0]);continue
                except httpx.HTTPError:
                    log.warning("worker_cycle_failed code=api_unavailable")
                if not self.stopping.is_set():
                    try:await asyncio.wait_for(self.stopping.wait(),timeout=5)
                    except TimeoutError:pass
        finally:await self.client.aclose()


async def main():
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
    worker=Worker()
    for signum in (signal.SIGTERM,signal.SIGINT):asyncio.get_running_loop().add_signal_handler(signum,worker.stopping.set)
    await worker.run()


if __name__=="__main__":asyncio.run(main())
