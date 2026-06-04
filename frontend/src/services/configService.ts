/**
 * Config Service
 * Handles secure retrieval of API keys and secrets.
 * Priority: 
 * 1. Environment Variables (Injected by platform)
 * 2. Local Storage (User provided via UI)
 */

const LOCAL_STORAGE_KEY = 'quantum_trade_settings';

export const configService = {
  getSecret(key: string): string | undefined {
    // 1. Check environment variables safely
    let envValue;
    try {
      if (typeof process !== 'undefined' && process.env) {
        envValue = (process.env as any)[key];
      }
    } catch (e) {
      // Ignored: process is not defined
    }
    
    // Check Vite's import.meta.env if available
    if (!envValue) {
      try {
        if (typeof import.meta !== 'undefined' && (import.meta as any).env) {
          envValue = (import.meta as any).env[`VITE_${key}`] || (import.meta as any).env[key];
        }
      } catch (e) {}
    }

    if (envValue && envValue !== `MY_${key}`) {
      return envValue;
    }

    // 2. Check local storage
    try {
      const stored = localStorage.getItem(LOCAL_STORAGE_KEY);
      if (stored) {
        const settings = JSON.parse(stored);
        return settings[key];
      }
    } catch (e) {
      console.error('Error reading from local storage:', e);
    }

    return undefined;
  },

  /**
   * Check if a secret is managed by the system (environment variable)
   */
  isSystemManaged(key: string): boolean {
    let envValue;
    try {
      if (typeof process !== 'undefined' && process.env) {
        envValue = (process.env as any)[key];
      }
    } catch (e) {}

    if (!envValue) {
      try {
        if (typeof import.meta !== 'undefined' && (import.meta as any).env) {
          envValue = (import.meta as any).env[`VITE_${key}`] || (import.meta as any).env[key];
        }
      } catch (e) {}
    }

    return !!(envValue && envValue !== `MY_${key}`);
  },

  /**
   * List of all supported secret keys
   */
  getKeys() {
    return [
      'GEMINI_API_KEY',
      'BACKEND_API_TOKEN',
      'XAI_API_KEY',
      'OPENAI_API_KEY',
      'ANTHROPIC_API_KEY',
      'BINANCE_API_KEY',
      'BINANCE_API_SECRET',
      'CTRADER_CLIENT_ID',
      'CTRADER_CLIENT_SECRET',
      'CTRADER_ACCESS_TOKEN',
      'COINGECKO_API_KEY',
      'COINMARKETCAP_API_KEY',
      'ALPHAVANTAGE_API_KEY',
      'POLYGON_API_KEY',
      'FRED_API_KEY',
      'NEWSAPI_KEY',
      'TWELVEDATA_API_KEY',
      'POSTGRES_URL',
      'POSTGRES_HOST',
      'POSTGRES_PORT',
      'POSTGRES_USER',
      'POSTGRES_PASSWORD',
      'POSTGRES_DB',
      'SUPABASE_URL',
      'SUPABASE_ANON_KEY',
      'SUPABASE_SERVICE_ROLE_KEY',
      'MYSQL_URL',
      'MYSQL_HOST',
      'MYSQL_PORT',
      'MYSQL_USER',
      'MYSQL_PASSWORD',
      'MYSQL_DB',
      'INFLUXDB_URL',
      'INFLUXDB_TOKEN',
      'INFLUXDB_ORG',
      'INFLUXDB_BUCKET',
      'INFLUXDB_PRECISION',
      'GRAFANA_URL',
      'GRAFANA_API_KEY',
      'TELEGRAM_BOT_TOKEN',
      'TELEGRAM_CHAT_ID',
      'N8N_WEBHOOK_URL',
      'RISK_PER_TRADE',
      'MAX_POSITIONS',
      'DEFAULT_STOP_LOSS',
      'DEFAULT_TAKE_PROFIT',
      'DAILY_LOSS_LIMIT'
    ];
  }
};
