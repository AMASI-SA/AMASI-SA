import json, os, re
from pathlib import Path
root=Path(__file__).resolve().parents[3]
request=json.loads((root/".github/ci/linux-mongo-request.json").read_text())
sha=os.environ.get("INPUT_SHA") or request["source_sha"]
tests=json.loads(os.environ["INPUT_TESTS"]) if os.environ.get("INPUT_TESTS") else request["tests"]
minimum=int(os.environ.get("INPUT_MINIMUM") or request["minimum_tests"])
if not re.fullmatch(r"[0-9a-f]{40}",sha) or minimum < 1:
    raise ValueError("Exact SHA and positive minimum required")
if not isinstance(tests,list) or not tests or any(not isinstance(t,str) or not re.fullmatch(r"tests/[A-Za-z0-9_./:-]+",t) for t in tests):
    raise ValueError("Explicit valid test selectors required")
with open(os.environ["GITHUB_OUTPUT"],"a") as f:
    f.write(f"sha={sha}\ntests={json.dumps(tests)}\nminimum={minimum}\n")
