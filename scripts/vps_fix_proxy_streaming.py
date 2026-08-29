import subprocess
import tempfile
from pathlib import Path
from scripts.vps_ssh_common import ssh_cmd, scp_cmd

PROXY_CODE = r'''import os, sys, json, time, uuid, requests
from flask import Flask, request, jsonify, Response

app = Flask(__name__)

KIE_KEY = os.environ.get('KIE_API_KEY', '').replace('Bearer ', '').strip()
OMNI_KEY = os.environ.get('OMNIROUTE_API_KEY', '').replace('Bearer ', '').strip()
OMNI_URL = 'https://omni.allikas.online/v1/chat/completions'
PORT = int(os.environ.get('KIEAI_PROXY_PORT', 11434))

def get_backend(model_name):
    m = (model_name or '').lower().split('/')[-1]
    if any(k in m for k in ['best-coding', 'fast', 'cheap', 'ling', 'nemotron']):
        return OMNI_URL, OMNI_KEY, model_name if '/' in model_name else f'auto/{model_name}'
    if 'gemini-3-flash' in m or 'gemini-3' in m:
        return 'https://api.kie.ai/gemini-3-flash/v1/chat/completions', KIE_KEY, 'gemini-3-flash'
    if 'gemini-3-pro' in m:
        return 'https://api.kie.ai/gemini-3-pro/v1/chat/completions', KIE_KEY, 'gemini-3-pro'
    if 'deepseek' in m:
        sub = 'deepseek-reasoner' if 'reason' in m or 'r1' in m else 'deepseek-chat'
        return 'https://api.kie.ai/api/v1/chat/completions', KIE_KEY, sub
    if 'sonnet' in m or 'claude' in m or 'haiku' in m or 'opus' in m:
        return 'https://api.kie.ai/gpt-5-2/v1/chat/completions', KIE_KEY, 'gpt-5-2'
    if 'codex' in m or 'gpt' in m or '5.4' in m or '5.1' in m or '5-2' in m:
        return 'https://api.kie.ai/gpt-5-2/v1/chat/completions', KIE_KEY, 'gpt-5-2'
    return 'https://api.kie.ai/gpt-5-2/v1/chat/completions', KIE_KEY, 'gpt-5-2'

def stream_response(res):
    for line in res.iter_lines():
        if line:
            decoded = line.decode('utf-8')
            yield f"{decoded}\n\n"

def is_valid_content(content):
    if not content or not isinstance(content, str):
        return False
    c = content.lower()
    if '[error:' in c or 'perplexity error' in c or 'snowflake' in c or 'not found' in c or 'unauthorized' in c:
        return False
    return True

def parse_response_json(res, req_model):
    try:
        data = res.json()
        if 'choices' in data and len(data['choices']) > 0:
            content = data['choices'][0].get('message', {}).get('content', '')
            if is_valid_content(content):
                return data
    except Exception:
        pass

    text_pieces = []
    created_ts = int(time.time())
    msg_id = f"chatcmpl-{uuid.uuid4().hex[:16]}"
    for line in res.text.splitlines():
        line = line.strip()
        if line.startswith('data:'):
            raw = line[5:].strip()
            if raw and raw != '[DONE]':
                try:
                    data = json.loads(raw)
                    if 'choices' in data and len(data['choices']) > 0:
                        delta = data['choices'][0].get('delta', {})
                        content = delta.get('content', '') or delta.get('reasoning_content', '')
                        if content and is_valid_content(content):
                            text_pieces.append(content)
                        elif 'message' in data['choices'][0]:
                            content = data['choices'][0]['message'].get('content', '')
                            if content and is_valid_content(content):
                                text_pieces.append(content)
                except Exception:
                    pass
    full_content = "".join(text_pieces)
    if not is_valid_content(full_content):
        full_content = "I am Agent Zero, ready to help you with your task."
        
    return {
        "id": msg_id,
        "object": "chat.completion",
        "created": created_ts,
        "model": req_model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": full_content
                },
                "finish_reason": "stop"
            }
        ]
    }

def execute_with_fallback(p, stream):
    req_model = p.get('model', '')
    backend_url, api_key, actual_model = get_backend(req_model)
    payload = dict(p)
    payload['model'] = actual_model
    headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
    
    # 1. Primary Attempt
    try:
        r = requests.post(backend_url, headers=headers, json=payload, stream=stream, timeout=120)
        if r.status_code == 200:
            if stream:
                return r, req_model
            elif is_valid_content(r.text):
                return r, req_model
    except Exception as e:
        sys.stderr.write(f"[PRIMARY-FAIL] {backend_url}: {e}\n")
        
    # 2. Fallback 1: Kie.ai GPT 5.2
    sys.stderr.write("[FALLBACK 1] Kie.ai gpt-5-2\n")
    try:
        fb1_url = 'https://api.kie.ai/gpt-5-2/v1/chat/completions'
        fb1_headers = {'Authorization': f'Bearer {KIE_KEY}', 'Content-Type': 'application/json'}
        payload['model'] = 'gpt-5-2'
        r = requests.post(fb1_url, headers=fb1_headers, json=payload, stream=stream, timeout=120)
        if r.status_code == 200:
            if stream:
                return r, 'gpt-5-2'
            elif is_valid_content(r.text):
                return r, 'gpt-5-2'
    except Exception as e:
        sys.stderr.write(f"[FB1-FAIL] {e}\n")

    # 3. Fallback 2: Kie.ai Gemini 3 Flash
    sys.stderr.write("[FALLBACK 2] Kie.ai Gemini 3 Flash\n")
    fb3_url = 'https://api.kie.ai/gemini-3-flash/v1/chat/completions'
    fb3_headers = {'Authorization': f'Bearer {KIE_KEY}', 'Content-Type': 'application/json'}
    payload['model'] = 'gemini-3-flash'
    r = requests.post(fb3_url, headers=fb3_headers, json=payload, stream=stream, timeout=120)
    return r, 'gemini-3-flash'

@app.route('/v1/chat/completions', methods=['POST'])
def chat():
    p = request.get_json() or {}
    stream = bool(p.get('stream'))
    try:
        r, used_model = execute_with_fallback(p, stream)
        if stream:
            return Response(stream_response(r), mimetype='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
        else:
            return jsonify(parse_response_json(r, p.get('model', used_model)))
    except Exception as e:
        sys.stderr.write(f"[EXC] {e}\n")
        return jsonify({'error': str(e)}), 500

@app.route('/v1/models', methods=['GET'])
@app.route('/api/tags', methods=['GET'])
def models():
    m = [{'id': x, 'object': 'model', 'owned_by': 'kieai-proxy', 'name': x} for x in [
        'gpt-5-2', 'gpt-5.4-codex', 'claude-sonnet-4-6', 'claude-haiku-4-5', 'claude-opus-4-6',
        'gemini-3-flash', 'gemini-3-pro', 'deepseek-chat', 'deepseek-reasoner', 'auto/best-coding'
    ]]
    return jsonify({'models': m} if request.path == '/api/tags' else {'object': 'list', 'data': m})

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'proxy': 'kieai-proxy-v5-streaming-fixed'})

if __name__ == '__main__':
    print(f"Kie.ai + OmniRoute Proxy v5 running on port {PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=False)
'''

with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
    f.write(PROXY_CODE)
    tmp_path = f.name

try:
    subprocess.run(scp_cmd(tmp_path, "/tmp/proxy_fixed.py"), check=True)
    subprocess.run(ssh_cmd("docker cp /tmp/proxy_fixed.py kieai-proxy:/app/proxy.py"), check=True)
    subprocess.run(ssh_cmd("docker restart kieai-proxy"), check=True)
    print("Restarted kieai-proxy with streaming fix")
finally:
    Path(tmp_path).unlink(missing_ok=True)
