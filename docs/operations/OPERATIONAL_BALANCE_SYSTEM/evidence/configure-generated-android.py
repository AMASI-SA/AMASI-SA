from pathlib import Path
p=Path('android/app/src/main/AndroidManifest.xml')
s=p.read_text(encoding='utf-8')
import re
s=re.sub(r' android:usesCleartextTraffic="[^"]*"','',s)
s=s.replace('<application ', '<application android:usesCleartextTraffic="true" ',1)
p.write_text(s,encoding='utf-8')
p=Path('android/app/build.gradle');s=p.read_text(encoding='utf-8');s=s.replace('react {','react {\n    debuggableVariants = []',1);p.write_text(s,encoding='utf-8')
