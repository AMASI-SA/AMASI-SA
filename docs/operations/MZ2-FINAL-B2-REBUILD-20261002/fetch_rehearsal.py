import hashlib,io,json,pathlib,subprocess,urllib.request,urllib.error,urllib.parse,zipfile
out=pathlib.Path(__file__).resolve().parent
r=subprocess.run(["git","credential","fill"],input="protocol=https\nhost=github.com\n\n",capture_output=True,text=True,check=True)
cred=dict(line.split("=",1) for line in r.stdout.splitlines() if "=" in line)
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl): return None
req=urllib.request.Request("https://api.github.com/repos/AMASI-SA/AMASI-SA/actions/artifacts/11242597678/zip",headers={"Authorization":"Bearer "+cred["password"],"Accept":"application/vnd.github+json"})
try:
    with urllib.request.build_opener(NoRedirect).open(req,timeout=30) as response: archive=response.read()
except urllib.error.HTTPError as error:
    if error.code not in (301,302,303,307,308): raise SystemExit("Artifact download HTTP "+str(error.code))
    location=error.headers["Location"]
    assert urllib.parse.urlsplit(location).scheme=="https"
    with urllib.request.urlopen(location,timeout=60) as response: archive=response.read()
assert hashlib.sha256(archive).hexdigest()=="bf92bdaa82453aa40ac6f1add816408d4924f167dc5d8f6002f9f9be1b401903"
(out/"B2-rehearsal.zip").write_bytes(archive)
records=[]
with zipfile.ZipFile(io.BytesIO(archive)) as z:
    for name in z.namelist():
        body=z.read(name)
        records.append({"name":name,"bytes":len(body),"sha256":hashlib.sha256(body).hexdigest()})
        if name.endswith(".json"):
            data=json.loads(body)
            print(name,json.dumps(data,ensure_ascii=False)[:6000])
print(json.dumps(records,indent=2))
