"""CI-only runner: immutable local checkout, networkless tests plus disposable Mongo."""
import json, os, platform, re, subprocess, sys, time
from pathlib import Path
from verify_results import verify

def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)
def output(*args):
    return run(*args, stdout=subprocess.PIPE).stdout.strip()

def main():
    source=Path(os.environ["TEST_SOURCE"]).resolve()
    evidence=Path(os.environ["TEST_EVIDENCE"]).resolve(); evidence.mkdir(parents=True,exist_ok=True)
    sha=os.environ["TEST_SHA"]
    if not re.fullmatch(r"[0-9a-f]{40}",sha): raise ValueError("Full immutable SHA required")
    head=output("git","-C",str(source),"rev-parse","HEAD")
    if head != sha: raise ValueError("Checkout does not match requested SHA")
    if output("git","-C",str(source),"status","--porcelain","--untracked-files=no"):
        raise ValueError("Tracked source is dirty")
    tests=json.loads(os.environ["TEST_PATHS"])
    if not isinstance(tests,list) or not tests: raise ValueError("Explicit test list required")
    for item in tests:
        if not isinstance(item,str) or not re.fullmatch(r"tests/[A-Za-z0-9_./:-]+",item):
            raise ValueError("Invalid test selector")
        file=(source/"backend"/item.split("::")[0]).resolve()
        if not file.is_relative_to(source/"backend/tests") or not file.is_file():
            raise ValueError("Test file absent or outside tests")
    meta={"head":head,"tree":output("git","-C",str(source),"rev-parse","HEAD^{tree}"),
          "os":platform.platform(),"os_release":Path("/etc/os-release").read_text(),
          "runner_image":os.getenv("ImageVersion"),"tests":tests,
          "workflow_sha":os.getenv("GITHUB_SHA"),"mongo_tag":"mongo:8.0.12"}
    (evidence/"identity.json").write_text(json.dumps(meta,indent=2))
    mongo="isolated-test-mongo"; image="isolated-backend-tests:local"
    try:
        run("docker","build","-t",image,str(Path(__file__).parent))
        run("docker","pull","mongo:8.0.12")
        meta["python"]=output("docker","run","--rm","--network","none",image,"python","--version")
        meta["mongo_digest"]=output("docker","image","inspect","mongo:8.0.12","--format","{{json .RepoDigests}}")
        meta["test_image_id"]=output("docker","image","inspect",image,"--format","{{.Id}}")
        (evidence/"packages.txt").write_text(output("docker","run","--rm","--network","none",image,"python","-m","pip","freeze"))
        run("docker","run","-d","--name",mongo,"--network","none","mongo:8.0.12",
            "--port","27018","--replSet","reviewtest","--bind_ip","127.0.0.1")
        def shell(js):
            return output("docker","exec",mongo,"mongosh","--port","27018","--quiet","--eval",js)
        for attempt in range(60):
            try: shell("db.adminCommand({ping:1})"); break
            except subprocess.CalledProcessError: time.sleep(1)
        else: raise RuntimeError("Mongo did not become reachable")
        shell('rs.initiate({_id:"reviewtest",members:[{_id:0,host:"127.0.0.1:27018"}]})')
        for attempt in range(60):
            try: shell('if(!db.hello().isWritablePrimary){quit(1)}'); break
            except subprocess.CalledProcessError: time.sleep(1)
        else: raise RuntimeError("Replica primary unavailable")
        meta["mongo_version"]=shell("db.version()")
        if meta["mongo_version"] != "8.0.12": raise ValueError("Mongo version mismatch")
        meta["replica_hello"]=json.loads(shell("JSON.stringify(db.hello())"))
        (evidence/"identity.json").write_text(json.dumps(meta,indent=2))
        # No host environment, credentials, Docker socket, published port or external network.
        cmd=["docker","run","--rm","--network","container:"+mongo,
             "-v",str(source)+":/source:ro","-v",str(evidence)+":/evidence",
             "-e","MZ2_TEST_MONGO_URI=mongodb://127.0.0.1:27018/?replicaSet=reviewtest",
             image,"python","-m","pytest","--noconftest","-p","no:cacheprovider","-q","--tb=short",
             *tests,"--junitxml=/evidence/results.xml"]
        with (evidence/"pytest.log").open("w") as log:
            result=subprocess.run(cmd,text=True,stdout=log,stderr=subprocess.STDOUT)
        print((evidence/"pytest.log").read_text())
        gate=verify(evidence/"results.xml",int(os.environ["TEST_MINIMUM"]))
        (evidence/"zero-skip.json").write_text(json.dumps(gate,indent=2))
        if result.returncode: raise RuntimeError(f"pytest exit {result.returncode}")
    finally:
        with (evidence/"mongo.log").open("w") as log:
            subprocess.run(["docker","logs",mongo],stdout=log,stderr=subprocess.STDOUT)
        subprocess.run(["docker","rm","-f",mongo],check=False)

if __name__ == "__main__": main()
