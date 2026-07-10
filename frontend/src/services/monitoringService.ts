import { configService } from './configService';

class MonitoringService {
  constructor() {}

  async logTrade(data: {
    symbol: string;
    side: string;
    quantity: number;
    price: number;
    broker: string;
    orderId: string;
    success: boolean;
    timestamp: number;
  }) {
    // 1. Log to Console
    console.log(`[MONITORING] Trade ${data.success ? 'Success' : 'Failed'}: ${data.side} ${data.quantity} ${data.symbol} @ ${data.price} on ${data.broker}`);

    // 2. Log to InfluxDB via Proxy
    this.writeToInflux(data);

    // 3. Send Telegram Alert via Proxy
    await this.sendTelegramAlert(data);
  }

  private async writeToInflux(data: any) {
    try {
      const adminKey = configService.getSecret('ADMIN_API_KEY');
      const response = await fetch('/api/telemetry/influx', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(adminKey ? { 'X-API-Key': adminKey } : {}),
        },
        body: JSON.stringify({ data })
      });
      if (!response.ok) {
        console.error('Error writing to InfluxDB:', await response.text());
      }
    } catch (error) {
      console.error('Error writing to InfluxDB:', error);
    }
  }

  private async sendTelegramAlert(data: any) {
    const emoji = data.success ? '✅' : '❌';
    const text = `${emoji} *Trade ${data.success ? 'Executed' : 'Failed'}*\n\n` +
      `*Symbol:* ${data.symbol}\n` +
      `*Side:* ${data.side.toUpperCase()}\n` +
      `*Quantity:* ${data.quantity}\n` +
      `*Price:* ${data.price}\n` +
      `*Broker:* ${data.broker.toUpperCase()}\n` +
      `*Order ID:* \`${data.orderId}\`\n` +
      `*Time:* ${new Date(data.timestamp).toLocaleString()}`;

    try {
      const adminKey = configService.getSecret('ADMIN_API_KEY');
      const response = await fetch('/api/telemetry/telegram', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(adminKey ? { 'X-API-Key': adminKey } : {}),
        },
        body: JSON.stringify({ text })
      });
      if (!response.ok) {
        console.error('Telegram alerting failed:', await response.text());
      }
    } catch (error) {
      console.error('Error sending Telegram alert:', error);
    }
  }

  getGrafanaDashboardUrl() {
    const url = configService.getSecret('GRAFANA_URL');
    if (!url) return null;
    return url;
  }
}

export const monitoringService = new MonitoringService();
