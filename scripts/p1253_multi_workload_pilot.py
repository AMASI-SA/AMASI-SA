"""Balanced independent-tenant smoke/pilot controller; unchanged measurement worker."""
import argparse,json,os,pathlib,platform,subprocess,time
from p1253_diagnostic_contract import BASELINE, CANDIDATE, schedule, request_ids


def matrix_rows(mode):
    if mode not in ("smoke","pilot"):raise ValueError("Only smoke/pilot allowed")
    count,repetitions=(12,2) if mode=="smoke" else (100000,10)
    selected=[{"name":"100k-independent-"+str(n),"count":count,
               "tenants":[f"tenant-{i}" for i in range(n)],"concurrency":n,
               "kind":"single" if n==1 else "independent"} for n in range(1,5)]
    return list(schedule(repetitions,selected))


def select_mode(event,requested,confirmation):
    if event=="push":return "smoke"
    if event!="workflow_dispatch" or requested not in ("smoke","pilot"):
        raise ValueError("Unsupported diagnostic event/matrix")
    if requested=="pilot" and confirmation!="P1253_PILOT_160_ONLY":
        raise ValueError("Explicit pilot confirmation required")
    return requested


def plan_budget(mode,available_seconds=None):
    rows=matrix_rows(mode)
    if mode=="pilot":
        # Planning only: same-host run37524615896 A/Warm costs. Scaling with
        # tenants and treating Cold like Warm are UNMEASURED assumptions.
        measurement=sum(20.532*r["workload"]["concurrency"] for r in rows)
        warmup=sum(21.042*r["workload"]["concurrency"]*r["warmup_count"] for r in rows)
        overhead=len(rows)*(2.382+10.999+3.602)
        preparation=600.0
    else:
        measurement=0.0;warmup=0.0;overhead=300.0;preparation=300.0
    estimated=measurement+warmup+overhead+preparation
    available_seconds=available_seconds if available_seconds is not None else (340*60 if mode=="pilot" else 20*60)
    return {"mode":mode,"measured_windows":len(rows),"warmup_groups":sum(r["warmup_count"] for r in rows),
            "measurement_seconds_estimate":measurement,"warmup_seconds_estimate":warmup,
            "reset_fingerprint_worker_seconds_estimate":overhead,"preparation_seconds_allowance":preparation,
            "planning_seconds":estimated,"planning_seconds_with_margin":estimated*1.25,
            "margin":0.25,"measured_multi_tenant_estimate":False,
            "assumptions":"Pilot uses A/Warm rate with linear tenant scaling; Cold and contention unknown. Not a guarantee or RCA. Smoke is a fixed small-run allowance.",
            "available_seconds":available_seconds,"fits_available":estimated*1.25<=available_seconds}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--mode",choices=["smoke","pilot"],default="smoke")
    parser.add_argument("--plan-only",action="store_true")
    parser.add_argument("--sources",type=pathlib.Path)
    parser.add_argument("--output",type=pathlib.Path)
    args=parser.parse_args()
    if args.plan_only:print(json.dumps(plan_budget(args.mode),indent=2));return
    if args.sources is None or args.output is None:parser.error("sources/output required")
    mode=select_mode(os.getenv("GITHUB_EVENT_NAME"),args.mode,os.getenv("P1253_CONFIRMATION",""))
    rows=matrix_rows(mode);count=rows[0]["workload"]["count"]
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    root=pathlib.Path(__file__).resolve().parents[1]
    started=time.perf_counter()
    setup_started=float((out/"job-started.txt").read_text())
    budget=max(0,min((340 if mode=="pilot" else 20)*60,(350 if mode=="pilot" else 25)*60-(time.time()-setup_started)))
    deadline=started+budget
    mongo="p1253-capacity-mongo";worker="p1253-capacity-worker";image="p1253-diagnostic:local"
    report={"status":"STARTED","scope":"Same host independent tenants; Cold/Warm; balanced BC/CB; seed once",
            "mode":mode,"plan":rows,"budget_plan":plan_budget(mode,budget),"samples":[],"timings":{},"phase":"initialization","measurements":0,"capacity_budget_seconds":budget}
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
    def child(child_mode,source=CANDIDATE):
        cmd=["docker","run","--rm","--name",worker,"--network","container:"+mongo,
             "-v",str(args.sources.resolve())+":/sources:ro","-v",str(root)+":/diagnostic:ro",
             "-v",str(out)+":/evidence:ro",image,"python","/diagnostic/scripts/p1253_paired_linux.py",child_mode,
             "--harness","/sources/candidate","--target",
             "/sources/baseline" if source==BASELINE else "/sources/candidate",
             "--database","dashboard_summary_benchmark_p1253_capacity","--count",str(count)]
        if child_mode=="worker":cmd += ["--sample","/evidence/capacity-sample.json"]
        return json.loads(command(*cmd))
    try:
        if not report["budget_plan"]["fits_available"]:raise RuntimeError("Planning budget exceeds available time; no measurement started")
        report["environment"]={"os":pathlib.Path("/etc/os-release").read_text(),"kernel":platform.platform(),
            "runner_image":os.getenv("ImageVersion"),"runner_name":os.getenv("RUNNER_NAME"),
            "run_id":os.getenv("GITHUB_RUN_ID"),"job":os.getenv("GITHUB_JOB"),"cpu":command("lscpu"),"memory":command("free","-b"),
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
        assert report["fixture"]["orders_per_tenant"]==count and report["fixture"]["tenants"]==4
        def reset():
            command("docker","stop",mongo);command("sync")
            command("sudo","-n","sh","-c","echo 3 > /proc/sys/vm/drop_caches")
            command("docker","start",mongo);primary()
        for index,row in enumerate(rows):
            phase(f"reset_{index:02d}",reset)
            (out/"capacity-sample.json").write_text(json.dumps(row))
            (out/f"sample-{index:02d}.json").write_text(json.dumps(row))
            result=phase(f"worker_{index:02d}",lambda:child("worker",row["source"]))
            report["samples"].append(result);report["measurements"]+=1;save()
            (out/f"result-{index:02d}.json").write_text(json.dumps(result,indent=2))
            assert result["source"]["head"]==row["source"] and result["sample"]==row
            assert [r["tenant"] for r in result["requests"]]==request_ids(row["workload"])
            assert result["source"]["tree"]=={BASELINE:"73cf78b1b170fc37458ccc39ec29e6caaab432e1",CANDIDATE:"8f0c68640154b68606dbbb7c04e058a34dd0cd49"}[row["source"]]
            assert result["warmup"]["count"]==row["warmup_count"] and not result["profile"]
            if row["state"]=="cold":assert result["warmup"]["commands"]==0
            else:assert result["warmup"]["commands"]>0
            assert result["before_measurement_data_commands"]==0
            after=phase(f"fingerprint_after_{index:02d}",lambda:child("fingerprint"))
            assert after==expected,"Dataset/index drift"
            print(f"Sample {index+1}/{len(rows)} source={row['source']} endpoint={result['requests'][0]['endpoint_seconds']}",flush=True)
        assert len(report["samples"])==len(rows)
        a_signatures=set()
        for index in range(0,len(rows),2):
            pair=report["samples"][index:index+2]
            for key in ["financial_signature","full_signature"]:
                assert [(r["tenant"],r[key]) for r in pair[0]["requests"]]==[(r["tenant"],r[key]) for r in pair[1]["requests"]],key+" mismatch"
            for result in pair:
                assert len({r["financial_signature"] for r in result["requests"]})==len(result["requests"]),"Independent tenant evidence missing"
                a_signatures.add(result["requests"][0]["financial_signature"])
        assert len(a_signatures)==1,"A result changed across workloads/states/sources"
        report["financial_parity"]=True;report["full_response_parity"]=True;report["stable_A"]=True
        report["fingerprint_unchanged"]=True
        report["status"]="SMOKE_PASS" if mode=="smoke" else "PILOT_COMPLETED";report["phase"]="completed";save()
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
