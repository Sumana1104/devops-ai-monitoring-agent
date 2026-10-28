"""Read-only end-to-end preflight, prints pass/fail with useful diagnostics."""
from __future__ import annotations
import json
import subprocess
import sys
import time
from urllib.request import urlopen
from urllib.parse import urlencode


def check(name, fn):
    try:
        value=fn()
        print('PASS',name, '-', value)
        return True
    except Exception as exc:
        print('FAIL',name, '-', type(exc).__name__,str(exc)[:350])
        return False


def get(url):
    with urlopen(url, timeout=8) as f:
        return f.read().decode()


def mini():
    result=subprocess.run(['kubectl','--context','opspilot','-n','opspilot','get','deployment','opspilot-demo','-o','json'],check=True,capture_output=True,text=True,timeout=15)
    dep=json.loads(result.stdout)
    if not dep['status'].get('readyReplicas',0): raise RuntimeError('No ready demo replicas')
    return str(dep['status']['readyReplicas']) + ' demo replica Ready'


def prom():
    q=urlencode({'query':'up{job="opspilot-demo"}'})
    last_error='No scrape samples yet'
    for _ in range(6):
        data=json.loads(get('http://127.0.0.1:9090/api/v1/query?'+q))
        results=data['data']['result']
        if results and results[0]['value'][1]=='1':
            return 'Prometheus job=opspilot-demo is UP=1'
        if results:
            last_error='Scrape target UP='+results[0]['value'][1]+'; Docker cannot reach host port 19110'
        time.sleep(5)
    raise RuntimeError(last_error)

checks=[
 ('Kubernetes demo Deployment', mini),
 ('Demo app HTTP response', lambda: json.loads(get('http://127.0.0.1:19110/healthz'))),
 ('Demo app Prometheus /metrics', lambda: 'real counters present' if 'opspilot_http_requests_total' in get('http://127.0.0.1:19110/metrics') else (_ for _ in ()).throw(RuntimeError('missing metric'))),
 ('Local bridge', lambda: json.loads(get('http://127.0.0.1:18008/health'))),
 ('Prometheus availability',lambda: get('http://127.0.0.1:9090/-/ready').strip()),
 ('Alertmanager availability',lambda: get('http://127.0.0.1:19093/-/ready').strip()),
 ('Prometheus scrape target (after ~20 sec)', prom),
 ('Prometheus alert rules', lambda: len(json.loads(get('http://127.0.0.1:9090/api/v1/rules'))['data']['groups'])),
]
results=[check(n,f) for n,f in checks]
print('\nRESULT:',sum(results),'/',len(results),'checks passed')
if not all(results): sys.exit(1)
