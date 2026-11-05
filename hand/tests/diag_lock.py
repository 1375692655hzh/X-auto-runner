import os
import sys
import tempfile

os.environ["XAR_SIMULATE"] = "1"
os.environ["XAR_STATE"] = tempfile.mkdtemp(prefix="xar-dbg2-")
sys.path.insert(0, "/opt/xar/hand")
from pathlib import Path
from ledger import Ledger, _ledger_lock

print("1. constructing Ledger...", flush=True)
led = Ledger("wcixot", caps={"like": 5})
print("2. ledger path:", led.path, flush=True)
print("3. acquiring lock...", flush=True)
with _ledger_lock(Path(str(led.path) + ".lock")):
    print("4. lock acquired", flush=True)
    led.record("like")
    print("5. record done, used =", led.used("like"), flush=True)
print("6. PASS", flush=True)
