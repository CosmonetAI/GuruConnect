"""Native-emulation metadata/queue stress; not hosted Storage/provider capacity.

Requires the dedicated fixture marker and localhost. It cannot run on a remote
project. Fixture records are committed only in the disposable test cluster.
"""
import argparse
import concurrent.futures
import json
import subprocess
import time
from uuid import NAMESPACE_URL, uuid5


def identity(name: str) -> str:
    return str(uuid5(NAMESPACE_URL, "guru-native-test:" + name))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    command = ["psql", "-h", "127.0.0.1", "-p", str(args.port), "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-qAt"]

    def sql(source: str) -> str:
        result = subprocess.run(command, input=source, text=True, capture_output=True, timeout=40)
        if result.returncode:
            raise RuntimeError(result.stderr)
        return result.stdout.strip()

    if sql("select marker from auth.guru_native_emulation_marker;") != "t":
        raise RuntimeError("Refusing a database without the native-emulation fixture marker")
    sql("create table recorder_private.native_claim_log(job_id uuid primary key,worker_id text not null);")

    for stage, count, workspace_count in [("20", 20, 1), ("100", 100, 20)]:
        start = time.monotonic()
        users = [identity(f"{stage}:user:{i}") for i in range(count)]
        orgs = [identity(f"{stage}:org:{i}") for i in range(workspace_count)]
        conversations = [identity(f"{stage}:conversation:{i}") for i in range(count)]
        parts = [identity(f"{stage}:part:{i}") for i in range(count)]
        setup = ["begin;"]
        for i, user in enumerate(users):
            setup.append(f"insert into auth.users(id,email,email_confirmed_at) values('{user}','native-{stage}-{i}@example.invalid',now());")
        for i, org in enumerate(orgs):
            setup.append(f"insert into public.organisations(id,name,created_by,max_concurrent_jobs) values('{org}','Native stage {stage} org {i}','{users[i]}',4);")
        for i, user in enumerate(users):
            org, conv, part = orgs[i % workspace_count], conversations[i], parts[i]
            customer = identity(f"{stage}:customer:{i}")
            setup.extend([
                f"insert into public.members(organisation_id,user_id,role) values('{org}','{user}','{'owner' if i < workspace_count else 'salesperson'}');",
                f"insert into public.customers(id,organisation_id,owner_id,name) values('{customer}','{org}','{user}','Sam');",
                f"insert into public.conversations(id,organisation_id,owner_id,customer_id,customer_name_snapshot,salesperson_name_snapshot,consent_confirmed) values('{conv}','{org}','{user}','{customer}','Sam','Native {i}',true);",
                f"insert into public.recording_parts(id,conversation_id,ordinal) values('{part}','{conv}',0);",
            ])
        setup.append("commit;")
        sql("\n".join(setup))

        def ingest(i: int) -> None:
            # Out-of-order durable metadata reservations from independent clients.
            org, user, conv, part = orgs[i % workspace_count], users[i], conversations[i], parts[i]
            path = f"{org}/{user}/{conv}/session-audio/no-phone/{part}/"
            sql(f"""begin;
                insert into public.audio_chunks(conversation_id,part_id,sequence,sample_start,sample_count,bytes,sha256,object_path)
                values('{conv}','{part}',1,80000,80000,160044,repeat('b',64),'{path}1.wav'),
                ('{conv}','{part}',0,0,80000,160044,repeat('a',64),'{path}0.wav');
                select public.schedule_job('{conv}',null,'validate','native-stage-{stage}','{{}}');
                commit;""")
            role = "owner" if i < workspace_count else "salesperson"
            visible = sql(f"""begin;set local role authenticated;
                set local request.jwt.claims='{{"sub":"{user}","role":"authenticated"}}';
                select count(*) from public.conversations;
                select count(*) from public.audio_chunks;
                rollback;""").splitlines()
            expected = (count // workspace_count + (1 if i % workspace_count < count % workspace_count else 0)) if role == "owner" else 1
            if len(visible) != 2 or visible[0] != str(expected) or not (2 <= int(visible[1]) <= expected * 2):
                raise AssertionError(f"{stage} user {i} visibility {visible}; expected {expected}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(count, 22)) as executor:
            list(executor.map(ingest, range(count)))
        # Replicated workers use independent DB sessions and commit each lease and
        # result under actual contention; a unique log rejects duplicate publication.
        worker_count = min(count, 22)

        def worker(i: int) -> int:
            worker_id = f"native-{stage}-replica-{i}"
            source = f"""begin;
                create temporary table claimed as select * from public.claim_jobs('{worker_id}','main',1,60);
                select pg_sleep(0.01);
                insert into recorder_private.native_claim_log(job_id,worker_id) select id,'{worker_id}' from claimed;
                select public.finish_job(id,'{worker_id}',lease_token,fence,'{{"validated":true}}') from claimed;
                commit;"""
            return 1 if "t" in sql(source).splitlines() else 0

        completed = 0
        for _ in range(40):
            with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
                completed += sum(executor.map(worker, range(worker_count)))
            if completed == count:
                break
        if completed != count:
            raise AssertionError(f"{stage}: only {completed}/{count} jobs completed")
        check = sql(f"""select count(*) from public.processing_jobs where dedupe_key='native-stage-{stage}' and state='succeeded';
            select count(*) from recorder_private.queue_receipts qr join public.processing_jobs j on j.id=qr.job_id where j.dedupe_key='native-stage-{stage}';
            select count(*) from public.audio_chunks c join public.conversations v on v.id=c.conversation_id where v.organisation_id in ({','.join(repr(org) for org in orgs)});""").splitlines()
        if check != [str(count), "0", str(count * 2)]:
            raise AssertionError(f"{stage}: durable final checks failed {check}")
        print(json.dumps({"stage": stage, "clients": count, "workspaces": workspace_count, "metadata_chunks_persisted": count * 2,
                          "jobs_completed": completed, "duplicate_publications": 0, "seconds": round(time.monotonic() - start, 3),
                          "mode": "native-postgres-emulation; no Storage HTTP/provider inference"}))


if __name__ == "__main__":
    main()
