"""Host-side orchestration only. Full benchmark intentionally unavailable."""
import argparse, collections, json, os, pathlib, platform, subprocess, sys, time
from p1253_diagnostic_contract import BASELINE, CANDIDATE, schedule, smoke_workloads, request_ids, summarize, estimate_matrix


def command(*args):
    return subprocess.run(args,check=True,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout.strip()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--sources",type=pathlib.Path,required=True)
    parser.add_argument("--output",type=pathlib.Path,required=True)
    args=parser.parse_args(); out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    root=pathlib.Path(__file__).resolve().parents[1]
    mongo="p1253-smoke-mongo"; image="p1253-diagnostic:local"
    report={"status":"STARTED","scope":"12 records/tenant smoke; NOT performance validation",
            "plan":estimate_matrix(),"samples":[],"environment":{}}
    def save(): (out/"report.json").write_text(json.dumps(report,indent=2))
    def shell(js):return command("docker","exec",mongo,"mongosh","--port","27261","--quiet","--eval",js)
    def primary():
        for _ in range(60):
            try:
                shell('if(!db.hello().isWritablePrimary){quit(1)}');return
            except subprocess.CalledProcessError:time.sleep(1)
        raise RuntimeError("Mongo replica primary unavailable")
    def child(mode,sha=CANDIDATE,sample=None):
        target="/sources/"+("baseline" if sha==BASELINE else "candidate")
        cmd=["docker","run","--rm","--network","container:"+mongo,
             "-v",str(args.sources.resolve())+":/sources:ro",
             "-v",str(root)+":/diagnostic:ro","-v",str(out)+":/evidence:ro",image,
             "python","/diagnostic/scripts/p1253_paired_linux.py",mode,
             "--harness","/sources/candidate","--target",target,
             "--database","dashboard_summary_benchmark_p1253_smoke"]
        if sample:cmd += ["--sample","/evidence/"+sample]
        return json.loads(command(*cmd))
    try:
        report["environment"]={"os":pathlib.Path("/etc/os-release").read_text(),"kernel":platform.platform(),
          "runner_image":os.getenv("ImageVersion"),"cpu":command("lscpu"),
          "mongo_digest":command("docker","image","inspect","mongo:8.0.12","--format","{{json .RepoDigests}}"),
          "python":command("docker","run","--rm","--network","none",image,"python","--version"),
          "image_id":command("docker","image","inspect",image,"--format","{{.Id}}"),
          "environment_base":"PR1276 2392ba6b2957cbc820cf7d806288b56589438c0b; same requirements and network isolation",
          "limits":"same GitHub-hosted VM, one sequential test container, default Docker CPU/memory limits",
          "mongo_port":27261,"replica_set":"p1253test","profile":False}
        assert report["environment"]["python"] == "Python 3.12.15"
        (out/"packages.txt").write_text(command("docker","run","--rm","--network","none",image,"python","-m","pip","freeze"))
        command("docker","run","-d","--name",mongo,"--network","none","mongo:8.0.12",
                "--port","27261","--replSet","p1253test","--bind_ip","127.0.0.1")
        for _ in range(60):
            try:shell("db.adminCommand({ping:1})");break
            except subprocess.CalledProcessError:time.sleep(1)
        shell('rs.initiate({_id:"p1253test",members:[{_id:0,host:"127.0.0.1:27261"}]})')
        primary()
        assert shell("db.version()") == "8.0.12"
        report["fixture"]=child("seed")
        expected=child("fingerprint")
        report["dataset"]=expected
        rows=list(schedule(2,smoke_workloads()))
        report["planned_measured_runs"]=len(rows)
        report["full_run_blocked"]=True
        save()
        for index,row in enumerate(rows):
            name=f"sample-{index:03d}.json"
            (out/name).write_text(json.dumps(row))
            # All fingerprint/seed reads are finished BEFORE this reset.
            stopped=time.time()
            command("docker","stop",mongo)
            command("sync")
            cached_before=pathlib.Path("/proc/meminfo").read_text()
            command("sudo","-n","sh","-c","echo 3 > /proc/sys/vm/drop_caches")
            cached_after=pathlib.Path("/proc/meminfo").read_text()
            command("docker","start",mongo);primary()
            status=json.loads(shell('JSON.stringify({uptime:db.serverStatus().uptime,cache_bytes:db.serverStatus().wiredTiger.cache["bytes currently in the cache"]})'))
            reset={"stopped_at":stopped,"worker_started_at":time.time(),"mongo_restarted":True,
                   "os_drop_caches_exit":0,"meminfo_before":cached_before,"meminfo_after":cached_after,
                   "mongo_metadata_only":status,"data_fingerprint_after_reset":False}
            result=child("worker",row["source"],name)
            result["reset"]=reset
            assert result["source"]["head"]==row["source"]
            assert result["before_measurement_data_commands"]==0
            assert result["warmup"]["count"]==(0 if row["state"]=="cold" else 1)
            if row["state"]=="warm":assert result["warmup"]["commands"]>0
            assert [r["tenant"] for r in result["requests"]]==request_ids(row["workload"])
            assert child("fingerprint")==expected,"Dataset/index drift"
            (out/f"result-{index:03d}-{row['source']}.json").write_text(json.dumps(result,indent=2))
            report["samples"].append(result);save()
            print(f"{index+1}/{len(rows)} {row['workload']['name']} {row['state']} {row['source'][:8]} PASS",flush=True)
        groups=collections.defaultdict(list)
        parity=collections.defaultdict(dict)
        a_signatures=collections.defaultdict(set)
        for result in report["samples"]:
            row=result["sample"]
            pkey=(row["workload"]["name"],row["state"],row["repetition"])
            parity[pkey][row["source"]]=[(r["tenant"],r["financial_signature"]) for r in result["requests"]]
            for r in result["requests"]:
                key=(row["source"],row["workload"]["name"],row["state"],r["tenant"],r["request_index"])
                groups[key].append(dict(source=row["source"],workload=row["workload"]["name"],state=row["state"],
                    tenant=r["tenant"],profile=False,repetition=row["repetition"],ok=r["ok"],endpoint_seconds=r["endpoint_seconds"]))
                if r["tenant"]=="tenant-0":a_signatures[row["source"]].add(r["financial_signature"])
        assert all(p[BASELINE]==p[CANDIDATE] for p in parity.values()),"Financial parity mismatch"
        assert all(len(s)==1 for s in a_signatures.values()),"A financial result changed with independent tenants"
        counts=collections.Counter((r["sample"]["source"],r["sample"]["state"]) for r in report["samples"])
        assert len(set(counts.values()))==1
        report["summary"]=[dict(key=list(k),statistics=summarize(v)) for k,v in groups.items()]
        report["financial_parity"]=True;report["stable_A"]=True
        report["equal_samples"]={str(k):v for k,v in counts.items()}
        report["status"]="SMOKE_PASS";save()
    except Exception as exc:
        report["status"]="FAILED";report["error"]=str(exc)
        if isinstance(exc,subprocess.CalledProcessError):report["stderr"]=exc.stderr
        save();raise
    finally:
        with (out/"mongo.log").open("w") as log:
            subprocess.run(["docker","logs",mongo],stdout=log,stderr=subprocess.STDOUT)
        subprocess.run(["docker","rm","-f",mongo],check=False)

if __name__ == "__main__":main()
