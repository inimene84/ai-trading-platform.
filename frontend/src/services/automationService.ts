import { configService } from './configService';

class AutomationService {
  async triggerTradeEvent(data: {
    event: 'trade_executed' | 'trade_failed';
    payload: any;
  }) {
    try {
      console.log(`[AUTOMATION] Triggering n8n webhook for ${data.event}...`);
      const adminKey = configService.getSecret('ADMIN_API_KEY');
      const response = await fetch('/api/telemetry/n8n', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(adminKey ? { 'X-API-Key': adminKey } : {}),
        },
        body: JSON.stringify({
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
