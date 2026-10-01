"""Native cascade work on API threads must permit normal interpreter shutdown."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('context', ['thread', 'portal'])
def test_native_photo_analysis_off_main_thread_exits_and_preserves_quality_gates(context):
    script = '''
from io import BytesIO
import json, threading
from PIL import Image
from app.wedding_photo_quality import analyze_photo
data = BytesIO()
Image.new('RGB', (1024, 1024), (180, 80, 120)).save(data, format='PNG')
def analyze():
    return {'demo': analyze_photo(data.getvalue(), demo=True),
            'live': analyze_photo(data.getvalue(), demo=False),
            'corrupt': analyze_photo(b'corrupt', demo=True)}
'''
    if context == 'thread':
        script += '''
results = []
worker = threading.Thread(target=lambda: results.append(analyze()))
worker.start()
worker.join()
print(json.dumps(results[0]), flush=True)
'''
    else:
        script += '''
from starlette.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.responses import JSONResponse
async def probe(request):
    return JSONResponse(analyze())
with TestClient(Starlette(routes=[Route('/', probe)])) as client:
    print(json.dumps(client.get('/').json()), flush=True)
'''
    executable = sys.executable
    child_env = os.environ.copy()
    if sys.platform == 'win32':
        # Run the interpreter directly while retaining this venv. Killing a
        # timed-out venv launcher alone can leave its native child orphaned.
        executable = sys._base_executable
        child_env['__PYVENV_LAUNCHER__'] = sys.executable
    child = subprocess.Popen([executable, '-c', script], cwd=Path(__file__).resolve().parents[1],
                             env=child_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        stdout, stderr = child.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        child.kill()
        stdout, stderr = child.communicate()
        pytest.fail(f'Native {context} analysis did not exit normally; stdout={stdout!r}; stderr={stderr!r}')
    assert child.returncode == 0, stderr
    results = json.loads(stdout)
    assert results['demo']['usable'] is True and results['demo']['demo_bypass'] is True
    assert results['demo']['face_count'] == 0
    assert results['live']['usable'] is False and results['live']['demo_bypass'] is False
    assert 'no_face_detected' in results['live']['warnings']
    assert results['corrupt']['usable'] is False and results['corrupt']['warnings'] == ['decode_failed']
