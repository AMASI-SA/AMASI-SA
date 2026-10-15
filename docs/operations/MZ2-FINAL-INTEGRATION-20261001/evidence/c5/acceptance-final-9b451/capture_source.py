from pathlib import Path
import datetime, hashlib, json, subprocess, sys
root=Path('C:/Users/amasi/mz2-c-rich-shipping-20261001')
out=Path('C:/Users/amasi/mz2-c-evidence-20261001/c5-business-uat-acceptance')
expected_head='9b451cc03b4f8159483cbf58ef0fd128d24530e1'
expected_tree='1756c44f0f631c4f72dc24525c694601e0ee8011'
phase=sys.argv[1]
assert phase in ('before','after')
def git(*args):
    return subprocess.check_output(['git','-C',str(root),*args]).decode('utf-8').strip()
head,tree,status=git('rev-parse','HEAD'),git('rev-parse','HEAD^{tree}'),git('status','--porcelain=v1')
assert head==expected_head and tree==expected_tree and not status, (head,tree,status)
paths=subprocess.check_output(['git','-C',str(root),'ls-files','-z']).decode('utf-8').split('\0')
files={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in paths if name}
record={'captured_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'root':str(root),'head':head,'tree':tree,'status_porcelain':status,'file_count':len(files),'files':files,'full_manifest_sha256':hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
(out/f'source-{phase}.json').write_text(json.dumps(record,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
if phase=='after':
    before=json.loads((out/'source-before.json').read_text(encoding='utf-8'))
    assert record['files']==before['files'], 'Tracked source bytes changed'
    record['identical_to_before']=True
    (out/'source-after.json').write_text(json.dumps(record,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
print(json.dumps({k:v for k,v in record.items() if k!='files'},indent=2))
