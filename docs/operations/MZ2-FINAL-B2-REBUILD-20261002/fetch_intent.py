"""Download and verify a GitHub CI intent artifact without release actions."""
import argparse,hashlib,io,json,pathlib,subprocess,urllib.request,urllib.error,urllib.parse,zipfile
p=argparse.ArgumentParser()
p.add_argument("--root",required=True);p.add_argument("--source",required=True);p.add_argument("--artifact",required=True);p.add_argument("--sha256",required=True);p.add_argument("--out",required=True)
a=p.parse_args();root=pathlib.Path(a.root);out=pathlib.Path(a.out);out.mkdir(parents=True,exist_ok=True)
r=subprocess.run(["git","credential","fill"],input="protocol=https\nhost=github.com\n\n",capture_output=True,text=True,check=True,cwd=root)
cred=dict(line.split("=",1) for line in r.stdout.splitlines() if "=" in line)
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl): return None
req=urllib.request.Request("https://api.github.com/repos/AMASI-SA/AMASI-SA/actions/artifacts/"+a.artifact+"/zip",headers={"Authorization":"Bearer "+cred["password"],"Accept":"application/vnd.github+json"})
try:
    with urllib.request.build_opener(NoRedirect).open(req,timeout=30) as response: archive=response.read()
except urllib.error.HTTPError as error:
    if error.code not in (301,302,303,307,308): raise SystemExit("Artifact HTTP "+str(error.code))
    location=error.headers["Location"];assert urllib.parse.urlsplit(location).scheme=="https"
    with urllib.request.urlopen(location,timeout=30) as response: archive=response.read()
assert hashlib.sha256(archive).hexdigest()==a.sha256
(out/"source-intent.zip").write_bytes(archive)
with zipfile.ZipFile(io.BytesIO(archive)) as z: content=z.read("release-intent-v5.json")
intent=json.loads(content)
assert intent["source_git_sha"]==a.source
assert intent["source_base_git_sha"]=="83363097d48e034dc7140a60c290efc684e1ffde"
assert intent["branch"]=="hotfix/prod-snap-meta-final" and intent["schema_version"]==2 and intent["protocol_version"]==5
tree={}
for row in subprocess.check_output(["git","ls-tree","-rz",a.source],cwd=root).split(b"\0"):
    if row:
        header,path=row.split(b"\t",1);mode,kind,oid=header.decode().split();tree[path.decode()]={"mode":mode,"oid":oid}
records=[];counts={}
for key,prefix in (("frontend_source","frontend/"),("backend_runtime_source","backend/"),("release_control_source","")):
    manifest=intent[key];assert manifest["file_count"]==len(manifest["files"]);counts[key]=len(manifest["files"])
    for rec in manifest["files"]:
        actual=tree[prefix+rec["path"]];assert actual["oid"]==rec["git_blob"] and actual["mode"]==rec["mode"]
        records.append(rec)
proc=subprocess.Popen(["git","cat-file","--batch"],cwd=root,stdin=subprocess.PIPE,stdout=subprocess.PIPE)
data,_=proc.communicate("".join(r["git_blob"]+"\n" for r in records).encode());assert proc.returncode==0
offset=0
for rec in records:
    end=data.index(b"\n",offset);oid,kind,size=data[offset:end].decode().split();size=int(size);start=end+1;body=data[start:start+size]
    assert oid==rec["git_blob"] and kind=="blob" and size==rec["bytes"] and hashlib.sha256(body).hexdigest()==rec["sha256"]
    offset=start+size+1
assert offset==len(data)
(out/"release-intent-v5.json").write_bytes(content)
report={"result":"PASS","source":a.source,"artifact":int(a.artifact),"archive_sha256":a.sha256,"intent_sha256":hashlib.sha256(content).hexdigest(),"matched_git_records":counts,"total":len(records),"runtime_id":intent["runtime_identity"]["release_id"]}
(out/"artifact-validation.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8");print(json.dumps(report,indent=2))
