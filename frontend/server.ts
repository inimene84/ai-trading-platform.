import express from 'express';
import http from 'http';
import cors from 'cors';
import dotenv from 'dotenv';
import path from 'path';
import { fileURLToPath } from 'url';
import { createServer as createViteServer } from 'vite';
import pg from 'pg';

dotenv.config();

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const app = express();
const PORT = 5173;

app.use(cors());
app.use(express.json());

// --- PostgreSQL Setup ---
const { Pool } = pg;
const pool = new Pool({
  connectionString: process.env.DATABASE_URL || `postgresql://${process.env.POSTGRES_USER || 'postgres'}:${process.env.POSTGRES_PASSWORD || ''}@${process.env.POSTGRES_HOST || 'localhost'}:${process.env.POSTGRES_PORT || '5432'}/${process.env.POSTGRES_DB || 'quantum_trade'}`,
});

// Test DB connection
pool.query('SELECT NOW()', (err, res) => {
  if (err) {
    console.warn('PostgreSQL connection failed. Using mock data for backtesting.', err.message);
  } else {
    console.log('PostgreSQL connected successfully.');
  }
});

// --- API Routes ---

// Get historical data for backtesting
app.get('/api/historical', async (req, res) => {
  const { symbol, interval, limit = 1000 } = req.query;
  
  try {
    // Try to fetch from DB
    const result = await pool.query(
      'SELECT * FROM historical_data WHERE symbol = $1 AND interval = $2 ORDER BY time DESC LIMIT $3',
      [symbol, interval, limit]
    );
    
    if (result.rows.length > 0) {
      return res.json(result.rows.reverse());
    }
  } catch (err) {
    console.error('DB fetch error, falling back to mock:', err);
  }

  // Mock historical data if DB fails or is empty
  const mockData = [];
  let price = symbol.toString().includes('USD') ? 60000 : 1.10;
  if (symbol.toString().includes('ETH')) price = 3000;
  if (symbol.toString().includes('SOL')) price = 150;
  
  const now = Math.floor(Date.now() / 1000);
  let step = 60; // 1 min default
  if (interval === '1h') step = 3600;
  if (interval === '4h') step = 14400;
  if (interval === '1d') step = 86400;

  // Simple volatility based on price
  const volatility = price * 0.002;

  for (let i = 0; i < Number(limit); i++) {
    const change = (Math.random() - 0.5) * volatility;
    const open = price;
    const close = price + change;
    const high = Math.max(open, close) + Math.random() * (volatility * 0.5);
    const low = Math.min(open, close) - Math.random() * (volatility * 0.5);
    
    mockData.push({
      time: now - (Number(limit) - i) * step,
      open,
      high,
      low,
      close,
      volume: Math.random() * 1000
    });
    price = close;
  }
  
  res.json(mockData);
});

import { InfluxDB, Point } from '@influxdata/influxdb-client';

app.post('/api/telemetry/influx', async (req, res) => {
  const { url, token, org, bucket, data } = req.body;
  if (!url || !token || !org || !bucket) return res.status(400).json({ error: 'Missing config' });
  
  try {
    const influxClient = new InfluxDB({ url, token });
    const writeApi = influxClient.getWriteApi(org, bucket, 'ms');
    
    const point = new Point('trades')
      .tag('symbol', data.symbol)
      .tag('side', data.side)
      .tag('broker', data.broker)
      .tag('status', data.success ? 'success' : 'failure')
      .floatField('price', data.price)
      .floatField('quantity', data.quantity)
      .stringField('orderId', data.orderId)
      .timestamp(data.timestamp);
    
    writeApi.writePoint(point);
    await writeApi.close();
    res.json({ success: true });
  } catch (error) {
    console.error('Influx server proxy error:', error);
    res.status(500).json({ error: 'Failed to write to InfluxDB' });
  }
});

app.post('/api/telemetry/telegram', async (req, res) => {
  const { token, chatId, text } = req.body;
  if (!token || !chatId || !text) return res.status(400).json({ error: 'Missing config' });
  
  try {
    const response = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        chat_id: chatId,
        text,
        parse_mode: 'Markdown'
      })
    });
    if (!response.ok) {
      const errText = await response.text();
      return res.status(response.status).json({ error: errText });
    }
    const result = await response.json();
    res.json(result);
  } catch (error) {
    console.error('Telegram server proxy error:', error);
    res.status(500).json({ error: 'Failed to send Telegram alert' });
  }
});

app.post('/api/telemetry/n8n', async (req, res) => {
  const { webhookUrl, event, payload } = req.body;
  if (!webhookUrl || !event || !payload) return res.status(400).json({ error: 'Missing config' });
  
  try {
    const response = await fetch(webhookUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Event-Type': event
      },
      body: JSON.stringify(payload)
    });
    if (!response.ok) {
      const errText = await response.text();
      return res.status(response.status).json({ error: errText });
    }
    res.json({ success: true });
  } catch (error) {
    console.error('n8n server proxy error:', error);
    res.status(500).json({ error: 'Failed to trigger n8n webhook' });
  }
});
// --- Backend API Proxy (works in both dev and production) ---
const BACKEND_URL = process.env.BACKEND_URL || 'http://localhost:8000';

// --- News API Proxy → FastAPI backend /api/news/* ---
app.use('/api/news', async (req, res) => {
  const targetUrl = `${BACKEND_URL}/api/news${req.url || '/'}`;
  try {
    const fetchOpts: RequestInit = { method: req.method, headers: { 'Content-Type': 'application/json' } };
    if (req.method !== 'GET' && req.method !== 'HEAD' && req.body) {
      fetchOpts.body = JSON.stringify(req.body);
    }
    const backendRes = await fetch(targetUrl, fetchOpts);
    res.status(backendRes.status).set('Content-Type', backendRes.headers.get('content-type') || 'application/json');
    res.send(await backendRes.text());
  } catch (error: any) {
    console.error(`News proxy error: ${targetUrl}`, error.message);
    res.status(502).json({ error: 'News backend unavailable', detail: error.message });
  }
});

// --- Market Data API Proxy → FastAPI backend /api/market-data/* ---
app.use('/api/market-data', async (req, res) => {
  const targetUrl = `${BACKEND_URL}/api/market-data${req.url || '/'}`;
  try {
    const fetchOpts: RequestInit = { method: req.method, headers: { 'Content-Type': 'application/json' } };
    if (req.method !== 'GET' && req.method !== 'HEAD' && req.body) {
      fetchOpts.body = JSON.stringify(req.body);
    }
    const backendRes = await fetch(targetUrl, fetchOpts);
    res.status(backendRes.status).set('Content-Type', backendRes.headers.get('content-type') || 'application/json');
    res.send(await backendRes.text());
  } catch (error: any) {
    console.error(`Market data proxy error: ${targetUrl}`, error.message);
    res.status(502).json({ error: 'Market data backend unavailable', detail: error.message });
  }
});


app.use('/api/backend', async (req, res) => {
  const targetPath = req.url || '/';
  const targetUrl = `${BACKEND_URL}${targetPath}`;
  
  try {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    const authHeader = req.header('authorization');
    const apiKeyHeader = req.header('x-api-key');
    if (authHeader) headers.Authorization = authHeader;
    if (apiKeyHeader) headers['X-API-Key'] = apiKeyHeader;
    const fetchOpts: RequestInit = {
      method: req.method,
      headers,
    };
    if (req.method !== 'GET' && req.method !== 'HEAD' && req.body) {
      fetchOpts.body = JSON.stringify(req.body);
    }
    const backendRes = await fetch(targetUrl, fetchOpts);
    const contentType = backendRes.headers.get('content-type') || 'application/json';
    res.status(backendRes.status).set('Content-Type', contentType);
    const data = await backendRes.text();
    res.send(data);
  } catch (error: any) {
    console.error(`Backend proxy error: ${targetUrl}`, error.message);
    res.status(502).json({ error: 'Backend unavailable', detail: error.message });
  }
});

// --- Vite Middleware ---
async function startServer() {
  const httpServer = http.createServer(app);

  if (process.env.NODE_ENV !== 'production') {
    const vite = await createViteServer({
      server: {
        middlewareMode: true,
        hmr: {
          server: httpServer,
        },
      },
      appType: 'spa',
    });
    app.use(vite.middlewares);
  } else {
    const distPath = path.join(process.cwd(), 'dist');
    app.use(express.static(distPath));
    app.get('*', (req, res) => {
      res.sendFile(path.join(distPath, 'index.html'));
    });
  }


  httpServer.listen(PORT, '0.0.0.0', () => {
    console.log(`Server running on http://localhost:${PORT}`);
  });
}

startServer();
