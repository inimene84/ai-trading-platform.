import requests

api_key = '69f8a2d0e0d58b83b63d107eb2ec4bc1'

# Test Anthropic-compatible endpoint using Authorization: api_key (without Bearer prefix)
print("Testing Anthropic endpoint with Authorization: api_key (no Bearer prefix)...")
try:
    r = requests.post(
        'https://api.kie.ai/claude/v1/messages',
        headers={
            'Authorization': api_key,
            'x-api-key': api_key,
            'anthropic-version': '2023-06-01',
            'content-type': 'application/json'
        },
        json={
            'model': 'claude-sonnet-4-6',
            'max_tokens': 10,
            'messages': [{'role': 'user', 'content': 'hi'}]
        }
    )
    print('NO BEARER status:', r.status_code)
    print('NO BEARER response:', r.text)
except Exception as e:
    print('NO BEARER error:', e)
