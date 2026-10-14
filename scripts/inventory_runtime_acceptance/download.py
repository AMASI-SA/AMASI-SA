"""Download only the owner-confirmed official wheel; fail before installing."""
import hashlib
import json
from pathlib import Path
import sys
import urllib.request

root = Path(sys.argv[1])
url = 'https://d33sy5i8bnduwe.cloudfront.net/simple/emergentintegrations/emergentintegrations-0.1.2-py3-none-any.whl'
expected = 'b2ebc36f37d0d21cb7bc6600ef0c15a134baf7e32aa1cddad4d2e61e738a1728'
with urllib.request.urlopen(url, timeout=60) as response:
    assert response.url == url, 'Unexpected wheel redirect'
    data = response.read()
digest = hashlib.sha256(data).hexdigest()
result = {'url': url, 'bytes': len(data), 'sha256': digest,
          'pass': len(data) == 25093 and digest == expected}
(root/'evidence/wheel.json').write_text(json.dumps(result, indent=2))
print(json.dumps(result))
assert result['pass'], 'OFFICIAL WHEEL MISMATCH: STOP'
wheel = root/'wheelhouse/emergentintegrations-0.1.2-py3-none-any.whl'
wheel.write_bytes(data)
(root/'wheelhouse/verified.txt').write_text(f'{wheel} --hash=sha256:{expected}\n')
