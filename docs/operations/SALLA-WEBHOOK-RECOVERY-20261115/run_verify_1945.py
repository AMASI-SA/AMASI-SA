import datetime,hashlib,json,pathlib,subprocess
b=pathlib.Path('/tmp/salla-webhook-release-1945')
start=datetime.datetime.now(datetime.timezone.utc).isoformat()
with (b/'verify.json').open('xb') as out,(b/'verify.stderr').open('xb') as err:
 result=subprocess.run(['python','-B','scripts/production_release_guard.py','verify','--url','https://mezansalla.com'],cwd='/app',stdout=out,stderr=err)
raw=(b/'verify.json').read_bytes()
receipt={'started_at':start,'finished_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'exit_code':result.returncode,'stdout_bytes':len(raw),'stdout_sha256':hashlib.sha256(raw).hexdigest(),'stderr_bytes':(b/'verify.stderr').stat().st_size}
with (b/'verify-completion.json').open('x') as out:json.dump(receipt,out,sort_keys=True,indent=2)
