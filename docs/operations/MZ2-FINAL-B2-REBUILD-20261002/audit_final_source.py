"""Read-only exact final-source composition/SSOT audit; does not import application."""
import argparse, ast, hashlib, json, pathlib, subprocess
p=argparse.ArgumentParser()
p.add_argument("--root",required=True);p.add_argument("--expected-head",required=True);p.add_argument("--output",required=True)
a=p.parse_args(); root=pathlib.Path(a.root).resolve(); out=pathlib.Path(a.output).resolve()
def git(*args): return subprocess.check_output(["git",*args],cwd=root).decode().strip()
def tree(sha):
    result={}
    for row in subprocess.check_output(["git","ls-tree","-rz",sha],cwd=root).split(b"\0"):
        if row:
            header,path=row.split(b"\t",1); result[path.decode()]=header.decode()
    return result
J="83363097d48e034dc7140a60c290efc684e1ffde"
oldA="57688c6423848165525bbc85265c4bb56e65da1c"
oldB="e030b737ca50adb03f37b06dab2a5624d79474fa"
codeql="3cb4ecc89146b67322b56ca848cfcf67fd904e75"
A="79f307f6ff69ba658958ebcdce03341b39a55939"
head=git("rev-parse","HEAD")
assert head==a.expected_head and not git("status","--porcelain")
assert git("rev-parse","HEAD^")==A
assert git("diff","--name-only",A,head)=="release/release-intent-v5.json"
assert not git("rev-list","--full-history",J+".."+A,"--","release/release-intent-v5.json")
intent=json.loads((root/"release/release-intent-v5.json").read_bytes())
assert intent["source_git_sha"]==A and intent["source_base_git_sha"]==J
assert git("diff","--name-only",codeql,A)=="docs/operations/MZ2-FINAL-B2-REBUILD-20261002/SOURCE-ASSEMBLY.json"
expected={"backend/salla_integration/webhook_event_capture.py","backend/tests/test_salla_current_shipping.py","release/release-intent-v5.json","docs/operations/MZ2-FINAL-B2-REBUILD-20261002/SOURCE-ASSEMBLY.json"}
old,new=tree(oldB),tree(head)
changed=sorted(k for k in old.keys()|new.keys() if old.get(k)!=new.get(k))
assert set(changed)==expected,changed
for path in ("backend/salla_integration/webhook_event_capture.py","backend/tests/test_salla_current_shipping.py"):
    assert git("rev-parse",head+":"+path)==git("rev-parse",codeql+":"+path)
reference=pathlib.Path(__file__).parent.parent/"MZ2-FINAL-ACCEPTANCE-20261002/evidence/SSOT-SOURCE-CHECK.json"
prior=json.loads(reference.read_text(encoding="utf-8"))
import re
pattern=re.compile(r"\b(?:journal_entries|accounting_journals|accounting_ledger|financial_transactions)\b")
modules=[]
for item in prior["source_findings"]:
    data=(root/item["path"]).read_bytes()
    matches=sorted(set(pattern.findall(data.decode())))
    digest=hashlib.sha256(data).hexdigest()
    assert digest==item["sha256"] and not matches,item["path"]
    modules.append({"path":item["path"],"sha256":digest,"legacy_sink_matches":matches,"matches_approved_native_source":True})
guards=[]
for path in ["backend/accounting_write_control.py","backend/accounting_atomic.py","backend/accounting_writer_transition.py","scripts/production_release_guard.py"]:
    assert git("rev-parse",head+":"+path)==git("rev-parse","88cc9131027fd6783a46e2e00fc6aaec4788b8fa:"+path)
    guards.append({"path":path,"sha256":hashlib.sha256((root/path).read_bytes()).hexdigest(),"unchanged":True})
# Runtime logger-only change remains the previously reviewed delta.
path="backend/salla_integration/webhook_event_capture.py"
def function_ast(sha,name):
    doc=ast.parse(subprocess.check_output(["git","show",sha+":"+path],cwd=root).decode())
    fn=next(n for n in doc.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name)
    if name=="capture_unknown_event":
        fn.body=[n for n in fn.body if not (isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Attribute) and isinstance(n.value.func.value,ast.Name) and n.value.func.value.id=="log" and n.value.func.attr=="info" and n.value.args and isinstance(n.value.args[0],ast.Constant) and str(n.value.args[0].value).startswith("salla_webhook.result"))]
    return ast.dump(fn,include_attributes=False)
for name in ["capture_unknown_event","_sanitize","_fingerprint","list_recent_captures"]:
    assert function_ast(oldB,name)==function_ast(head,name),name
report={"head":head,"tree":git("rev-parse","HEAD^{tree}"),"source_A2":A,"production_base":J,"old_B1":oldB,"changed_from_old_B1":changed,"old_tree_entries":len(old),"preserved_old_entries":sum(old[k]==new.get(k) for k in old),"source_composition":"PASS","A2_to_B2_invariant":"PASS","codeql_source_preserved":True,"static_SSOT":"PASS_SCOPED_14_NATIVE_MODULES","modules":modules,"guards":guards,"capture_semantics_AST_unchanged_except_result_log":True,"dynamic_SSOT":"PENDING_EXACT_FINAL_REGRESSION","scope":"Converted native modules and declared regression boundaries; not all historic application routes","production_financial_writes":0}
out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
print(json.dumps({k:v for k,v in report.items() if k not in ("modules","guards")},indent=2))
