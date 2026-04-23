import { configService } from './configService';

class AutomationService {
  async triggerTradeEvent(data: {
    event: 'trade_executed' | 'trade_failed';
    payload: any;
  }) {
    const webhookUrl = configService.getSecret('N8N_WEBHOOK_URL');

    if (!webhookUrl) {
      console.log('[AUTOMATION] n8n webhook URL not configured, skipping trigger.');
      return;
    }

    try {
      console.log(`[AUTOMATION] Triggering n8n webhook for ${data.event}...`);
      const response = await fetch('/api/telemetry/n8n', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          webhookUrl,
          event: data.event,
          payload: {
            ...data,
            timestamp: Date.now()
          }
        })
      });

      if (!response.ok) {
        const errorText = await response.text();
        console.error(`[AUTOMATION] n8n webhook failed: ${response.status} ${response.statusText}`, errorText);
      } else {
        console.log('[AUTOMATION] n8n webhook triggered successfully.');
      }
    } catch (error) {
      console.error('[AUTOMATION] Error triggering n8n webhook:', error);
    }
  }
}

export const automationService = new AutomationService();
