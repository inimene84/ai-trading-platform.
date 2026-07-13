#!/usr/bin/env bash
set -euo pipefail
cd /root/ai-trading-platform-v3

echo "=== ENV STATUS ==="
for k in KIE_API_KEY OPENROUTER_API_KEY XAI_API_KEY GOOGLE_API_KEY TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID OPENROUTER_MODEL GEMINI_MODEL ANTHROPIC_API_KEY; do
  v=$(grep "^${k}=" .env 2>/dev/null | cut -d= -f2- | sed 's/^["'\'']//;s/["'\'']$//' || true)
  if [ -z "${v:-}" ]; then
    echo "$k: MISSING"
  elif echo "$v" | grep -qiE 'changeme|placeholder|your_|xxx'; then
    echo "$k: PLACEHOLDER"
  else
    echo "$k: SET (len=${#v})"
  fi
done
grep "^OPENROUTER_MODEL=" .env 2>/dev/null || echo "OPENROUTER_MODEL: using code default"
grep "^GEMINI_MODEL=" .env 2>/dev/null || echo "GEMINI_MODEL: using code default"

echo ""
echo "=== API TESTS ==="
set -a
# shellcheck disable=SC1091
source <(grep -E '^(TELEGRAM_BOT_TOKEN|OPENROUTER_API_KEY|XAI_API_KEY|GOOGLE_API_KEY|KIE_API_KEY)=' .env | sed 's/^/export /')
set +a

echo -n "Telegram getMe: "
curl -s -o /tmp/tg.json -w "%{http_code}" "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getMe"
echo
head -c 150 /tmp/tg.json; echo

echo -n "OpenRouter models: "
curl -s -o /tmp/or.json -w "%{http_code}" -H "Authorization: Bearer ${OPENROUTER_API_KEY}" https://openrouter.ai/api/v1/models
echo
head -c 150 /tmp/or.json; echo

echo -n "xAI models: "
curl -s -o /tmp/xai.json -w "%{http_code}" -H "Authorization: Bearer ${XAI_API_KEY}" https://api.x.ai/v1/models
echo
head -c 150 /tmp/xai.json; echo

echo -n "Gemini models: "
curl -s -o /tmp/gem.json -w "%{http_code}" "https://generativelanguage.googleapis.com/v1beta/models?key=${GOOGLE_API_KEY}"
echo
head -c 150 /tmp/gem.json; echo

echo -n "Kie chat: "
curl -s -o /tmp/kie.json -w "%{http_code}" -H "Authorization: Bearer ${KIE_API_KEY}" -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet-4-6","max_tokens":30,"messages":[{"role":"user","content":"Reply ONLY with valid JSON: {\"ok\":true}"}]}' \
  https://api.kie.ai/claude/v1/messages
echo
head -c 250 /tmp/kie.json; echo

echo -n "OpenRouter chat (anthropic/claude-3.5-sonnet): "
curl -s -o /tmp/or_chat.json -w "%{http_code}" -H "Authorization: Bearer ${OPENROUTER_API_KEY}" -H "Content-Type: application/json" \
  -d '{"model":"anthropic/claude-3.5-sonnet","messages":[{"role":"user","content":"Say ok"}],"max_tokens":10}' \
  https://openrouter.ai/api/v1/chat/completions
echo
head -c 200 /tmp/or_chat.json; echo
