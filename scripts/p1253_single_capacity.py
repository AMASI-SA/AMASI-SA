"""Exactly one candidate/A/warm 100k capacity sample. No percentile or baseline."""
import argparse,json,os,pathlib,platform,subprocess,time
from p1253_diagnostic_contract import CANDIDATE


def capacity_row():
    return {"source":CANDIDATE,"workload":{"name":"100k-A-capacity","count":100000,
            "tenants":["tenant-0"],"concurrency":1,"kind":"single"},
            "state":"warm","warmup_count":1,"repetition":0,"profile":False}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--sources",type=pathlib.Path,required=True)
    parser.add_argument("--output",type=pathlib.Path,required=True)
    args=parser.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    root=pathlib.Path(__file__).resolve().parents[1]
    started=time.perf_counter()
    setup_started=float((out/"job-started.txt").read_text())
    budget=max(0,min(50*60,55*60-(time.time()-setup_started)))
    deadline=started+budget
    mongo="p1253-capacity-mongo";worker="p1253-capacity-worker";image="p1253-diagnostic:local"
    report={"status":"STARTED","scope":"Candidate/A/Warm/1 measured repetition; 100k per tenant in unchanged four-tenant fixture",
            "sample":capacity_row(),"timings":{},"phase":"initialization","measurements":0,"capacity_budget_seconds":budget}
    def save():
        report["elapsed_seconds"]=time.perf_counter()-started
        (out/"capacity-report.json").write_text(json.dumps(report,indent=2))
    def command(*argv):
        remaining=deadline-time.perf_counter()
        if remaining<=0:raise TimeoutError("Capacity deadline reached")
        return subprocess.run(argv,check=True,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                              timeout=remaining).stdout.strip()
    def phase(name,call):
        report["phase"]=name;save();print(name,flush=True);tick=time.perf_counter()
        try:return call()
        finally:report["timings"][name]=time.perf_counter()-tick;save()
    def shell(js):return command("docker","exec",mongo,"mongosh","--port","27261","--quiet","--eval",js)
    def primary():
        for _ in range(60):
            try:shell('if(!db.hello().isWritablePrimary){quit(1)}');return
            except subprocess.CalledProcessError:time.sleep(1)
        raise RuntimeError("Replica primary unavailable")
    def child(mode):
        cmd=["docker","run","--rm","--name",worker,"--network","container:"+mongo,
             "-v",str(args.sources.resolve())+":/sources:ro","-v",str(root)+":/diagnostic:ro",
             "-v",str(out)+":/evidence:ro",image,"python","/diagnostic/scripts/p1253_paired_linux.py",mode,
             "--harness","/sources/candidate","--target","/sources/candidate",
             "--database","dashboard_summary_benchmark_p1253_capacity","--count","100000"]
        if mode=="worker":cmd += ["--sample","/evidence/capacity-sample.json"]
        return json.loads(command(*cmd))
    try:
        report["environment"]={"os":pathlib.Path("/etc/os-release").read_text(),"kernel":platform.platform(),
            "runner_image":os.getenv("ImageVersion"),"cpu":command("lscpu"),"memory":command("free","-b"),
            "mongo_digest":command("docker","image","inspect","mongo:8.0.12","--format","{{json .RepoDigests}}"),
            "python":command("docker","run","--rm","--network","none",image,"python","--version"),
            "image_id":command("docker","image","inspect",image,"--format","{{.Id}}"),
            "mongo_port":27261,"replica_set":"p1253test","settings":"Unchanged PR1276 requirements; default Docker limits; network none"}
        assert report["environment"]["python"]=="Python 3.12.15"
        (out/"packages.txt").write_text(command("docker","run","--rm","--network","none",image,"python","-m","pip","freeze"))
        def start_mongo():
            command("docker","run","-d","--name",mongo,"--network","none","mongo:8.0.12",
                    "--port","27261","--replSet","p1253test","--bind_ip","127.0.0.1")
            for _ in range(60):
                try:shell('db.adminCommand({ping:1})');break
                except subprocess.CalledProcessError:time.sleep(1)
            shell('rs.initiate({_id:"p1253test",members:[{_id:0,host:"127.0.0.1:27261"}]})');primary()
            report["environment"]["mongo_version"]=shell("db.version()")
            assert report["environment"]["mongo_version"]=="8.0.12"
        phase("mongo_initialization",start_mongo)
        report["fixture"]=phase("dataset_seed",lambda:child("seed"))
        expected=phase("fingerprint_before_reset",lambda:child("fingerprint"));report["dataset_fingerprint"]=expected
        assert report["fixture"]["orders_per_tenant"]==100000 and report["fixture"]["tenants"]==4
        def reset():
            command("docker","stop",mongo);command("sync")
            command("sudo","-n","sh","-c","echo 3 > /proc/sys/vm/drop_caches")
            command("docker","start",mongo);primary()
        phase("reset",reset)
        (out/"capacity-sample.json").write_text(json.dumps(capacity_row()))
        result=phase("worker_total",lambda:child("worker"))
        report["result"]=result;report["measurements"]=1;save()
        assert result["source"]["head"]==CANDIDATE and result["sample"]==capacity_row()
        assert len(result["requests"])==1 and result["requests"][0]["tenant"]=="tenant-0"
        assert result["warmup"]["count"]==1 and not result["profile"]
        after=phase("fingerprint_after_measurement",lambda:child("fingerprint"))
        assert after==expected,"Dataset/index drift"
        report["fingerprint_unchanged"]=True
        report["status"]="CAPACITY_PASS";report["phase"]="completed";save()
    except (subprocess.TimeoutExpired,TimeoutError) as exc:
        report["status"]="TIMED_OUT";report["error"]=str(exc);save()
        raise
    except Exception as exc:
        report["status"]="FAILED";report["error"]=str(exc)
        if isinstance(exc,subprocess.CalledProcessError):report["stderr"]=exc.stderr
        save();raise
    finally:
        cleanup=time.perf_counter()
        subprocess.run(["docker","stop","--time","10",worker],timeout=25,check=False)
        subprocess.run(["docker","rm","-f",worker],timeout=25,check=False)
        with (out/"mongo.log").open("w") as log:
            subprocess.run(["docker","logs",mongo],stdout=log,stderr=subprocess.STDOUT,timeout=30,check=False)
        subprocess.run(["docker","rm","-f",mongo],timeout=30,check=False)
        report["timings"]["cleanup"]=time.perf_counter()-cleanup;save()
        sizes={p.name:p.stat().st_size for p in out.iterdir() if p.is_file()}
        (out/"artifact-sizes.json").write_text(json.dumps(sizes,indent=2))

if __name__=="__main__":main()
