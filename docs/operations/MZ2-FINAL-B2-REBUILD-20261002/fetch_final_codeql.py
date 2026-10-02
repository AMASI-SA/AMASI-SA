import json,pathlib,subprocess,urllib.request,urllib.error
out=pathlib.Path(__file__).resolve().parent
r=subprocess.run(["git","credential","fill"],input="protocol=https\nhost=github.com\n\n",capture_output=True,text=True,check=True)
credential=dict(line.split("=",1) for line in r.stdout.splitlines() if "=" in line)
def get(path,accept="application/vnd.github+json"):
    request=urllib.request.Request("https://api.github.com/repos/AMASI-SA/AMASI-SA/"+path,headers={"Authorization":"Bearer "+credential["password"],"Accept":accept,"X-GitHub-Api-Version":"2022-11-28"})
    try:
        with urllib.request.urlopen(request,timeout=60) as response: return json.load(response)
    except urllib.error.HTTPError as error: raise SystemExit("GitHub read failed HTTP "+str(error.code))
head="d6c0553ae6a99a85d52b03871b7bf64024180514"
tree="806e935c8ecc3268f98096cd2b477050d235c99e"
pr=get("pulls/1245")
assert pr["head"]["sha"]==head
merge=get("git/commits/"+pr["merge_commit_sha"])
assert merge["tree"]["sha"]==tree
analyses=get("code-scanning/analyses?ref=refs/pull/1245/merge&per_page=100")
matches=[a for a in analyses if a["commit_sha"]==pr["merge_commit_sha"] and a.get("category")=="/language:python"]
assert matches,"No matching exact-B2 Python analysis"
analysis=matches[0]
sarif=get("code-scanning/analyses/"+str(analysis["id"]),"application/sarif+json")
(out/"B2-python.sarif").write_text(json.dumps(sarif),encoding="utf-8")
results=[r for run in sarif.get("runs",[]) for r in run.get("results",[]) if any(l.get("physicalLocation",{}).get("artifactLocation",{}).get("uri")=="backend/salla_integration/webhook_event_capture.py" for l in r.get("locations",[]))]
selected=[r for r in results if r.get("ruleId")=="py/clear-text-logging-sensitive-data"]
alerts=get("code-scanning/alerts?ref=refs/pull/1245/merge&state=open&per_page=100")
report={"head":head,"tree":tree,"pull_request":1245,"test_merge_sha":pr["merge_commit_sha"],"test_merge_tree":merge["tree"]["sha"],"python_analysis_id":analysis["id"],"created_at":analysis["created_at"],"selected_rule":"py/clear-text-logging-sensitive-data","selected_file":"backend/salla_integration/webhook_event_capture.py","selected_rule_file_results":len(selected),"all_results_in_file":[{"rule":r.get("ruleId"),"level":r.get("level")} for r in results],"original_four_open_on_new_pr":[a["number"] for a in alerts if a["number"] in (78,79,80,81)],"alert_dismissals_performed":0,"production_ref":get("git/ref/heads/hotfix/prod-snap-meta-final")["object"]["sha"]}
(out/"B2-codeql-proof.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
print(json.dumps(report,indent=2))
assert not selected
assert not report["original_four_open_on_new_pr"]
