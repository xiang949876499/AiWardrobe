"""Opt-in application PostgreSQL/S3 lifecycle checks using synthetic local services.

Run from repository root:
  $env:WEDDING_RUN_PG_S3='1'
  $env:PYTHONPATH=(Join-Path (Get-Location) 'backend')
  python backend/scripts/check_wedding_pg_s3.py --settings .superpowers/sdd/gap-integration-settings.json

Settings contain postgres_user/host/port/database/password_file, s3_endpoint/region,
s3_test_access_key/s3_test_secret_key. Password is read from a file and never logged.
Only loopback services are allowed. Uses a new random schema/private bucket, removed
in finally; no production data, real people, external providers or paid operations.
An injected fixture renderer isolates state/concurrency; actual FFmpeg acceptance is
separately runnable in tests/test_wedding_render_media.py with WEDDING_ACTUAL_MEDIA=1.
"""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event, Thread
from uuid import uuid4
from urllib.parse import urlparse


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=Path('output/wedding-task4-pg-s3.json'))
    args=parser.parse_args()
    if os.environ.get('WEDDING_RUN_PG_S3')!='1':
        print('SKIPPED: set WEDDING_RUN_PG_S3=1 to enable isolated local integration checks');return
    config=json.loads(args.settings.read_text(encoding='utf-8'))
    if config['postgres_host'] not in ('127.0.0.1','localhost') or urlparse(config['s3_endpoint']).hostname not in ('127.0.0.1','localhost'):
        raise SystemExit('This test harness requires dedicated loopback services')
    from sqlalchemy import create_engine,text,select,event
    from sqlalchemy.engine import URL
    import boto3
    import httpx
    from botocore.config import Config
    import pytest
    from fastapi.testclient import TestClient
    from fastapi import HTTPException
    suffix=uuid4().hex
    schema='wedding_task4_'+suffix
    bucket='wedding-task4-'+suffix
    url=URL.create('postgresql+psycopg',username=config['postgres_user'],
        password=Path(config['postgres_password_file']).read_text().strip(),host=config['postgres_host'],
        port=config['postgres_port'],database=config['postgres_database'])
    admin=create_engine(url,connect_args={'connect_timeout':5},echo=False)
    s3=boto3.client('s3',endpoint_url=config['s3_endpoint'],region_name=config['s3_region'],
        aws_access_key_id=config['s3_test_access_key'],aws_secret_access_key=config['s3_test_secret_key'],
        config=Config(connect_timeout=5,read_timeout=30,retries={'total_max_attempts':2}))
    patch=pytest.MonkeyPatch();checks=[];created_schema=False;created_bucket=False;workers=[]
    try:
        with admin.begin() as conn:conn.execute(text(f'CREATE SCHEMA {schema}'))
        created_schema=True
        s3.create_bucket(Bucket=bucket,ACL='private');created_bucket=True
        s3.put_public_access_block(Bucket=bucket,PublicAccessBlockConfiguration=dict(BlockPublicAcls=True,IgnorePublicAcls=True,BlockPublicPolicy=True,RestrictPublicBuckets=True))
        scoped=url.update_query_dict({'options':f'-csearch_path={schema}','connect_timeout':'5'})
        values={'DATABASE_URL':scoped.render_as_string(hide_password=False),'TESTING':'true','JWT_SECRET':'synthetic-test-only-secret-at-least-thirty-two-bytes',
            'AI_DEMO_MODE':'true','WORKFLOW_PROVIDER':'demo','WEDDING_MEDIA_MODE':'demo','STORAGE_DRIVER':'s3',
            'WEDDING_S3_BUCKET':bucket,'S3_BUCKET':'unrelated-wardrobe-test','S3_ENDPOINT_URL':config['s3_endpoint'],
            'S3_REGION':config['s3_region'],'S3_ACCESS_KEY_ID':config['s3_test_access_key'],'S3_SECRET_ACCESS_KEY':config['s3_test_secret_key']}
        for key,value in values.items():patch.setenv(key,value)
        from app.config import get_settings
        from app.database import get_session_factory,reset_database_connection,init_db
        get_settings.cache_clear();reset_database_connection()
        from app.main import create_app
        from app.models import WeddingProject,WeddingAsset,WeddingObjectCleanup,RenderVersion,WeddingParticipant,utc_now
        from app.wedding_service import WeddingPrivateStorage
        from app.wedding_cleanup import stage_output,cleanup_objects
        from app.wedding_retention import STORAGE_LIMIT,expire_assets
        from app.wedding_render_worker import claim_next_render_version,process_claimed_render_version,recover_renders
        from tests.test_wedding_render_lifecycle import queued,UnitRenderer
        from tests.test_wedding_retention import ready_assets
        from tests.conftest import login
        with TestClient(create_app()) as client,contextlib.redirect_stdout(io.StringIO()):
            factory=get_session_factory();real=WeddingPrivateStorage(get_settings())
            # Prove the newest additive migration on an already-existing table, twice.
            with admin.begin() as conn:
                conn.execute(text(f'ALTER TABLE {schema}.wedding_object_cleanup DROP COLUMN writer_finished_at'))
                conn.execute(text(f'ALTER TABLE {schema}.wedding_render_versions DROP COLUMN attempts'))
            init_db();init_db();checks.append('PostgreSQL additive stage/render migration twice')
            work,headers,ids=ready_assets(client)
            with factory() as db:key=db.get(WeddingAsset,ids[0]).object_key
            assert httpx.get(f"{config['s3_endpoint']}/{bucket}/{key}",timeout=5).status_code==403
            other=login(client,'outsider-task4@example.com')
            assert client.get('/wedding-assets/'+ids[0]).status_code==401
            assert client.get('/wedding-assets/'+ids[0],headers={'Authorization':'Bearer '+other}).status_code==404
            with ThreadPoolExecutor(max_workers=8) as pool:
                codes=list(pool.map(lambda _:client.get('/wedding-assets/'+ids[0],headers=headers).status_code,range(16)))
            assert codes.count(200)==10 and codes.count(429)==6,codes
            checks.append('actual private S3 anonymous403, gateway401/404; concurrent GET cap exactly10 of16')
            with factory() as db:
                project=db.get(WeddingProject,work.project_id);asset=db.get(WeddingAsset,work.frame_asset_id)
                original_size=asset.size_bytes
                from sqlalchemy import func
                used=db.scalar(select(func.sum(WeddingAsset.size_bytes)).where(WeddingAsset.project_id==project.id))
                asset.size_bytes+=STORAGE_LIMIT-used-1;db.commit()
            def upload(_):
                try:return stage_output(factory,real,work.project_id,'quota-race',b'x','.png','image/png')
                except HTTPException as exc:assert exc.detail=='PROJECT_STORAGE_LIMIT';return None
            with ThreadPoolExecutor(max_workers=2) as pool:keys=list(pool.map(upload,range(2)))
            assert sum(k is not None for k in keys)==1
            checks.append('PostgreSQL parent-locked S3 capacity race permits exactly one last byte')
            with factory() as db:db.get(WeddingAsset,work.frame_asset_id).size_bytes=original_size;db.commit()
            pid,h,rid=queued(client,patch)
            claim=claim_next_render_version(factory)
            with factory() as db:db.get(RenderVersion,rid).updated_at=utc_now()-timedelta(days=1);db.commit()
            recover_renders(factory);new=claim_next_render_version(factory)
            assert claim.lease_token!=new.lease_token
            assert process_claimed_render_version(factory,claim,real,UnitRenderer()).error_code=='RENDER_LEASE_STALE'
            statements=[]
            def observe(conn,cursor,sql,params,context,executemany):
                if 'FOR UPDATE' in sql:statements.append(sql)
            event.listen(factory.kw['bind'],'before_cursor_execute',observe)
            try:assert process_claimed_render_version(factory,new,real,UnitRenderer()).status=='ready'
            finally:event.remove(factory.kw['bind'],'before_cursor_execute',observe)
            for i,sql in enumerate(statements):
                if 'wedding_render_versions' in sql:assert i>0 and 'wedding_projects' in statements[i-1]
            checks.append('actual PostgreSQL parent-before-render locks, stale claim fenced, newer completion ready')
            for mutation in ('revoke','delete'):
                pid,h,rid=queued(client,patch);claim=claim_next_render_version(factory)
                entered=Event();release=Event();result=[]
                class Blocked(UnitRenderer):
                    def render(self,*a,**kw):
                        entered.set();assert release.wait(10);return super().render(*a,**kw)
                thread=Thread(target=lambda:result.append(process_claimed_render_version(factory,claim,real,Blocked())))
                workers.append((thread,release))
                thread.start();assert entered.wait(10)
                if mutation=='delete':assert client.delete('/wedding-projects/'+pid,headers=h).status_code==204
                else:
                    with factory() as db:
                        from app.wedding_retention import lock_project
                        lock_project(db,pid)
                        person=db.scalar(select(WeddingParticipant).where(WeddingParticipant.project_id==pid));person.revoked_at=utc_now();person.consent_status='revoked';db.commit()
                release.set();thread.join(15);assert not thread.is_alive() and result and result[0].status!='ready'
                with factory() as db:assert not list(db.scalars(select(WeddingAsset).where(WeddingAsset.project_id==pid,WeddingAsset.asset_type.in_(['preview_video','final_video']))))
                checks.append('actual PostgreSQL/S3 in-flight '+mutation+' fences outputs')
            with factory() as db:db.get(WeddingAsset,ids[1]).expires_at=utc_now()-timedelta(seconds=1);db.commit()
            class Failed:
                def delete(self,key):raise OSError('synthetic transient delete')
            expire_assets(factory,Failed())
            assert client.get('/wedding-assets/'+ids[1],headers=headers).status_code==404
            cleanup_objects(factory,real,now=utc_now()+timedelta(hours=1))
            checks.append('actual S3 expiry removes access before failed deletion and durable retry succeeds')
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps({'status':'PASS','checks':checks,'limits':'local PostgreSQL and Moto; fixture renderer; no live providers or production cloud policy proof'},indent=2),encoding='utf-8')
        print(json.dumps({'status':'PASS','checks':checks}))
    finally:
        for thread,release in workers:
            release.set();thread.join(20)
        # Dedicated generated namespace only; never touch an existing bucket/schema.
        if created_bucket:
            for page in s3.get_paginator('list_objects_v2').paginate(Bucket=bucket):
                keys=[{'Key':row['Key']} for row in page.get('Contents',[])]
                if keys:s3.delete_objects(Bucket=bucket,Delete={'Objects':keys})
            s3.delete_bucket(Bucket=bucket)
        try:
            from app.database import reset_database_connection
            reset_database_connection()
        finally:
            if created_schema:
                with admin.begin() as conn:conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))
            admin.dispose();patch.undo()


if __name__=='__main__':main()
