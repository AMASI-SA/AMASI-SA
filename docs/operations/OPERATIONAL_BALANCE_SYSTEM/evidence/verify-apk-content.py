from pathlib import Path
import hashlib,json,zipfile,re,subprocess
ROOT=Path(r'D:/CodexBuilds/operational-android-20261005')
APK=ROOT/'app/android/app/build/outputs/apk/debug/app-debug.apk'
REPO=Path(r'C:/Users/amasi/.codex/worktrees/operational-mobile-20261005')
head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO).decode().strip()
tree=subprocess.check_output(['git','rev-parse','HEAD^{tree}'],cwd=REPO).decode().strip()
assert head=='641537fc45961b31f268eae2bedd4c3356c20bfa'
assert not subprocess.check_output(['git','status','--porcelain'],cwd=REPO).strip()
parity=json.loads((ROOT/'source-parity.json').read_text(encoding='utf-8-sig'))
for name,digest in parity['hashes'].items():
    target=ROOT/'app'/Path(name).relative_to('frontend')
    assert hashlib.sha256(target.read_bytes()).hexdigest()==digest,name
with zipfile.ZipFile(APK) as z:
    assert z.testzip() is None
    names=z.namelist()
    assert 'assets/index.android.bundle' in names
    bundle=z.read('assets/index.android.bundle')
    assert b'http://10.0.2.2:8135/api' in bundle
    forbidden=[b'mezansalla.com',b'f285e8fe-06a6-48b4-b095-f1c42d63f7c3',b'BEGIN PRIVATE KEY',b'BEGIN RSA PRIVATE KEY']
    hits={s.decode():[n for n in names if not n.endswith('/') and s in z.read(n)] for s in forbidden}
    assert not any(hits.values()),{k:v for k,v in hits.items() if v}
    credential_files=[n for n in names if re.search(r'(^|/)(\.env(?:\.[^/]*)?|google-services\.json|.*\.(pem|p12|jks|keystore))$',n,re.I)]
    public_certificates=[]
    for name in credential_files[:]:
        if name=='assets/expo-root.pem':
            cert=z.read(name)
            assert cert.startswith(b'-----BEGIN CERTIFICATE-----') and b'PRIVATE KEY' not in cert
            assert cert==(ROOT/'app/node_modules/expo-updates/android/src/main/certificates/expo-root.pem').read_bytes()
            assert hashlib.sha256(cert).hexdigest()=='901e7b0da287ca154fc09d982d37769b09ee291255b239265b5b0a8b75165baa'
            public_certificates.append({'file':name,'kind':'unchanged dependency public root certificate'})
            credential_files.remove(name)
    assert not credential_files,credential_files
    result={'status':'APK_CONTENT_STATIC_CHECKS_PASS','native_head':head,'native_tree':tree,'apk_path':str(APK),'apk_sha256':hashlib.sha256(APK.read_bytes()).hexdigest(),'apk_bytes':APK.stat().st_size,'source_files_verified':len(parity['hashes']),'embedded_bundle_sha256':hashlib.sha256(bundle).hexdigest(),'embedded_test_api_present':True,'production_origin_and_ota_project_matches':0,'credential_file_matches':0,'public_dependency_certificates':public_certificates,'private_key_marker_matches':0,'native_abis':sorted({n.split('/')[1] for n in names if n.startswith('lib/') and n.endswith('.so')}),'device_uat':'NOT_EXECUTED_POLICY_BLOCKED','production_financial_writes':0}
(ROOT/'apk-content-proof.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result,indent=2))
