import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

def verify(path, minimum):
    cases = list(ET.parse(path).iter("testcase"))
    rejected = [c.attrib.get("name") for c in cases
                if any(c.find(tag) is not None for tag in ("skipped", "failure", "error"))]
    if minimum < 1 or len(cases) < minimum or rejected:
        raise ValueError(f"Zero-skip gate failed: count={len(cases)}, minimum={minimum}, rejected={rejected}")
    return {"executed": len(cases), "skipped": 0, "failures": 0, "errors": 0}

if __name__ == "__main__":
    result = verify(sys.argv[1], int(sys.argv[2]))
    Path(sys.argv[3]).write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
