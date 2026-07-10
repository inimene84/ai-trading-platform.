import { Node, Edge } from '@xyflow/react';
import { brokerService } from './brokerService';
import { marketDataService } from './marketDataService';
import { apiService } from './apiService';

// Node configuration types
export type FilterConfig = {
  indicator: 'rsi' | 'ema' | 'time' | 'volume';
  period: number;
  threshold: number;
  operator: '>' | '<' | '==' | '>=' | '<=';
  symbol?: string;
};

export type PositionSizerConfig = {
  riskPerTradePct: number;
  useATR: boolean;
  atrMultiplier: number;
  minOrderValue?: number;
};

export type RiskManagementConfig = {
  stopLossType: 'fixed' | 'atr';
  stopLossValue: number;
  takeProfitRatio: number;
  useTrailingStop: boolean;
  trailingStopPct: number;
};

export type KillswitchConfig = {
  maxDailyLossPct: number;
  haltDurationHours: number;
  enabled: boolean;
};

export type TriggerConfig = {
  symbol: string;
  priceThreshold: number;
  volumeThreshold?: number;
  delayMs: number;
};

export type ActionConfig = {
  symbol: string;
  quantity: number;
  orderType: 'market' | 'limit';
  side: 'buy' | 'sell';
  broker: 'binance' | 'ctrader';
  price?: number;
};

// Execution context passed between nodes
export interface ExecutionContext {
  marketData: {
    price: number;
    volume: number;
    rsi?: number;
    ema?: number;
    atr?: number;
    closes?: number[];
  };
  account: {
    equity: number;
    availableBalance: number;
    dailyPnL: number;
  };
  position: {
    symbol: string;
    size: number;
    entryPrice: number;
    stopLoss?: number;
    takeProfit?: number;
  } | null;
  triggered: boolean;
  halted: boolean;
  haltReason?: string;
  logs: ExecutionLog[];
}

export interface ExecutionLog {
  timestamp: number;
  nodeId: string;
  nodeType: string;
  message: string;
  level: 'info' | 'success' | 'error' | 'warning';
  data?: any;
}

export interface FlowRunResult {
  success: boolean;
  logs: ExecutionLog[];
  trades: TradeResult[];
  halted: boolean;
  haltReason?: string;
  executionTime: number;
}

export interface TradeResult {
  nodeId: string;
  symbol: string;
  side: 'buy' | 'sell';
  quantity: number;
  price: number;
  orderId?: string;
  success: boolean;
  error?: string;
  timestamp: number;
}

interface ConditionConfig {
  emaFast?: number;
  emaSlow?: number;
}

function calculateEMA(values: number[], period: number): number {
  const seed = values.slice(0, period).reduce((sum, value) => sum + value, 0) / period;
  const multiplier = 2 / (period + 1);
  return values.slice(period).reduce(
    (ema, value) => (value - ema) * multiplier + ema,
    seed,
  );
}

function calculateRSI(values: number[], period: number): number {
  const changes = values.slice(1).map((value, index) => value - values[index]);
  const recent = changes.slice(-period);
  const gains = recent.reduce((sum, value) => sum + Math.max(value, 0), 0) / period;
  const losses = recent.reduce((sum, value) => sum + Math.max(-value, 0), 0) / period;
  if (losses === 0) return gains === 0 ? 50 : 100;
  const rs = gains / losses;
  return 100 - 100 / (1 + rs);
}

class WorkflowEngine {
  private context: ExecutionContext;
  private trades: TradeResult[] = [];
  private executingNodes: Set<string> = new Set();

  constructor() {
    this.context = {
      marketData: { price: 0, volume: 0 },
      account: { equity: 0, availableBalance: 0, dailyPnL: 0 },
      position: null,
      triggered: false,
      halted: false,
      logs: []
    };
  }

  async executeFlow(nodes: Node[], edges: Edge[]): Promise<FlowRunResult> {
    const startTime = Date.now();
    this.trades = [];
    this.context.logs = [];
    this.context.halted = false;
    this.context.haltReason = undefined;

    this.log('system', 'system', 'Starting workflow execution', 'info');

    // Find trigger nodes (nodes with no incoming edges)
    const triggerNodes = this.findTriggerNodes(nodes, edges);
    
    if (triggerNodes.length === 0) {
      this.log('system', 'system', 'No trigger nodes found in workflow', 'error');
      return {
        success: false,
        logs: this.context.logs,
        trades: [],
        halted: false,
        executionTime: Date.now() - startTime
      };
    }

    // Fetch account data
    try {
      const accountData = await apiService.getAccountData();
      this.context.account.equity = accountData.equity;
      this.context.account.availableBalance = accountData.availableBalance;
      this.context.account.dailyPnL = accountData.dailyPnL;
    } catch (err) {
      this.context.account.equity = 0;
      this.context.account.availableBalance = 0;
      this.context.account.dailyPnL = 0;
      this.context.halted = true;
      this.context.haltReason = 'Account data unavailable — refusing workflow execution';
      this.log('system', 'system', this.context.haltReason, 'error');
    }

    // Execute from each trigger
    for (const triggerNode of triggerNodes) {
      if (this.context.halted) break;
      await this.executeNodeRecursive(triggerNode, nodes, edges);
    }

    const executionTime = Date.now() - startTime;
    
    this.log('system', 'system', 
      `Workflow execution completed in ${executionTime}ms. Trades: ${this.trades.length}, Halted: ${this.context.halted}`,
      this.context.halted ? 'warning' : 'success'
    );

    return {
      success: !this.context.halted,
      logs: this.context.logs,
      trades: this.trades,
      halted: this.context.halted,
      haltReason: this.context.haltReason,
      executionTime
    };
  }

  private findTriggerNodes(nodes: Node[], edges: Edge[]): Node[] {
    const nodesWithIncoming = new Set(edges.map(e => e.target));
    return nodes.filter(n => !nodesWithIncoming.has(n.id));
  }

  private async executeNodeRecursive(node: Node, allNodes: Node[], edges: Edge[]): Promise<any> {
    if (this.context.halted) return null;
    if (this.executingNodes.has(node.id)) return null; // Prevent cycles
    
    this.executingNodes.add(node.id);
    
    try {
      const nodeType = this.getNodeType(node);
      
      // Execute based on node type
      let result: any = null;
      
      switch (nodeType) {
        case 'trigger':
          result = await this.executeTriggerNode(node);
          break;
        case 'condition':
          result = await this.executeConditionNode(node);
          break;
        case 'filter':
          result = await this.executeFilterNode(node);
          break;
        case 'action':
          result = await this.executeActionNode(node);
          break;
        case 'riskManagement':
          result = await this.executeRiskManagementNode(node);
          break;
        case 'positionSizer':
          result = await this.executePositionSizerNode(node);
          break;
        case 'killswitch':
          result = await this.executeKillswitchNode(node);
          break;
        default:
          // Try to match by label
          const label = (node.data?.label || '').toLowerCase();
          if (label.includes('rsi') || label.includes('ema') || label.includes('filter')) {
            result = await this.executeFilterNode(node);
          } else if (label.includes('buy') || label.includes('sell') || label.includes('execute')) {
            result = await this.executeActionNode(node);
          } else {
            result = await this.executeTriggerNode(node);
          }
      }

      // Find outgoing edges and continue execution
      const outgoingEdges = edges.filter(e => e.source === node.id);
      
      for (const edge of outgoingEdges) {
        const targetNode = allNodes.find(n => n.id === edge.target);
        if (!targetNode) continue;

        // Check edge condition if present
        const edgeLabel = edge.label || '';
        const shouldContinue = this.evaluateEdgeCondition(edgeLabel, result, nodeType);
        
        if (shouldContinue) {
          await this.executeNodeRecursive(targetNode, allNodes, edges);
        }
      }

      return result;
    } finally {
      this.executingNodes.delete(node.id);
    }
  }

  private getNodeType(node: Node): string {
    const type = (node.data?.type || '').toLowerCase();
    const label = (node.data?.label || '').toLowerCase();
    
    if (type === 'trigger' || label.includes('trigger') || label.includes('volume') || label.includes('alert')) {
      return 'trigger';
    }
    if (type === 'condition' || label.includes('condition') || label.includes('trend')) {
      return 'condition';
    }
    if (label.includes('filter')) return 'filter';
    if (type === 'action' || label.includes('buy') || label.includes('sell') || label.includes('execute')) {
      return 'action';
    }
    if (label.includes('risk') || label.includes('stop loss') || label.includes('take profit')) {
      return 'riskManagement';
    }
    if (label.includes('position') || label.includes('sizer')) return 'positionSizer';
    if (label.includes('kill') || label.includes('halt') || label.includes('circuit')) {
      return 'killswitch';
    }
    return type;
  }

  private async executeTriggerNode(node: Node): Promise<boolean> {
    const config = node.data?.config as TriggerConfig;
    const symbol = config?.symbol || 'BTCUSDT';
    
    this.log(node.id, 'trigger', `Evaluating trigger for ${symbol}`, 'info');

    try {
      // Fetch market data via backend proxy (geo-block safe)
      const [response, klinesResponse] = await Promise.all([
        fetch(`/api/backend/trading/binance/ticker/24hr?symbol=${symbol.toUpperCase()}`),
        fetch(`/api/backend/trading/binance/klines?symbol=${symbol.toUpperCase()}&interval=1h&limit=100`),
      ]);
      if (!response.ok || !klinesResponse.ok) {
        throw new Error(
          `market data unavailable (ticker=${response.status}, klines=${klinesResponse.status})`,
        );
      }
      const data = await response.json();
      const klines = await klinesResponse.json();
      const closes = Array.isArray(klines)
        ? klines.map((bar: any) => Number(bar[4])).filter(Number.isFinite)
        : [];
      
      this.context.marketData.price = parseFloat(data.lastPrice);
      this.context.marketData.volume = parseFloat(data.volume);
      this.context.marketData.closes = closes;
      if (!Number.isFinite(this.context.marketData.price)
          || !Number.isFinite(this.context.marketData.volume)
          || closes.length < 20) {
        throw new Error('invalid market data payload');
      }

      // Check volume spike condition
      const volumeThreshold = config?.volumeThreshold || 0;
      const priceThreshold = config?.priceThreshold || 0;
      
      const volumeCondition = volumeThreshold === 0 || this.context.marketData.volume >= volumeThreshold;
      const priceCondition = priceThreshold === 0 || this.context.marketData.price >= priceThreshold;
      
      this.context.triggered = volumeCondition && priceCondition;
      
      this.log(node.id, 'trigger', 
        `Price: ${this.context.marketData.price}, Volume: ${this.context.marketData.volume.toFixed(2)}, ` +
        `Triggered: ${this.context.triggered}`,
        this.context.triggered ? 'success' : 'info'
      );

      // Apply delay if configured
      if (config?.delayMs && config.delayMs > 0) {
        await new Promise(resolve => setTimeout(resolve, config.delayMs));
      }

      return this.context.triggered;
    } catch (err) {
      this.log(node.id, 'trigger', `Error fetching market data: ${err}`, 'error');
      return false;
    }
  }

  private async executeConditionNode(node: Node): Promise<boolean> {
    const config = node.data?.config as ConditionConfig | undefined;
    this.log(node.id, 'condition', 'Evaluating condition', 'info');

    // Trend check using actual candle closes.
    if (config?.emaFast && config?.emaSlow) {
      const closes = this.context.marketData.closes || [];
      if (closes.length < Math.max(config.emaFast, config.emaSlow)) {
        this.log(node.id, 'condition', 'Insufficient candle history for EMA condition', 'error');
        return false;
      }
      const fastEMA = calculateEMA(closes, config.emaFast);
      const slowEMA = calculateEMA(closes, config.emaSlow);
      const result = fastEMA > slowEMA;
      
      this.log(node.id, 'condition', 
        `EMA Trend: Fast (${fastEMA.toFixed(2)}) > Slow (${slowEMA.toFixed(2)}) = ${result}`,
        result ? 'success' : 'info'
      );
      return result;
    }

    return true;
  }

  private async executeFilterNode(node: Node): Promise<boolean> {
    const config = node.data?.config as FilterConfig;
    const indicator = config?.indicator || 'rsi';
    
    this.log(node.id, 'filter', `Evaluating ${indicator.toUpperCase()} filter`, 'info');

    let value: number = 50;
    
    if (indicator === 'rsi') {
      const closes = this.context.marketData.closes || [];
      if (closes.length < 15) {
        this.log(node.id, 'filter', 'Insufficient candle history for RSI', 'error');
        return false;
      }
      value = calculateRSI(closes, 14);
      this.context.marketData.rsi = value;
    } else if (indicator === 'ema') {
      const closes = this.context.marketData.closes || [];
      if (closes.length < 20) return false;
      value = calculateEMA(closes, 20);
      this.context.marketData.ema = value;
    } else if (indicator === 'volume') {
      value = this.context.marketData.volume;
    }

    const threshold = config?.threshold || 30;
    const operator = config?.operator || '<';
    
    let passed = false;
    switch (operator) {
      case '>': passed = value > threshold; break;
      case '<': passed = value < threshold; break;
      case '>=': passed = value >= threshold; break;
      case '<=': passed = value <= threshold; break;
      case '==': passed = value === threshold; break;
      default: passed = value < threshold;
    }

    this.log(node.id, 'filter', 
      `${indicator.toUpperCase()}: ${value.toFixed(2)} ${operator} ${threshold} = ${passed ? 'PASS' : 'FAIL'}`,
      passed ? 'success' : 'warning'
    );

    return passed;
  }

  private async executeActionNode(node: Node): Promise<TradeResult | null> {
    const config = node.data?.config as ActionConfig;
    
    if (!config) {
      this.log(node.id, 'action', 'No configuration found', 'error');
      return null;
    }

    const symbol = config.symbol || 'BTCUSDT';
    const side = config.side || 'buy';
    const quantity = config.quantity || 0.01;
    const broker = config.broker || 'binance';
    
    this.log(node.id, 'action', 
      `Executing ${side.toUpperCase()} order for ${quantity} ${symbol} on ${broker.toUpperCase()}`,
      'info'
    );

    try {
      const result = await brokerService.executeTrade(broker, {
        symbol: symbol.toUpperCase(),
        quantity,
        type: config.orderType || 'market',
        side,
        price: config.price || this.context.marketData.price
      });

      const tradeResult: TradeResult = {
        nodeId: node.id,
        symbol: symbol.toUpperCase(),
        side,
        quantity,
        price: config.price || this.context.marketData.price,
        orderId: result.orderId,
        success: result.success,
        error: result.error,
        timestamp: Date.now()
      };

      this.trades.push(tradeResult);

      if (result.success) {
        this.log(node.id, 'action', `Trade successful: ${result.orderId}`, 'success');
        // Update position context
        this.context.position = {
          symbol: symbol.toUpperCase(),
          size: side === 'buy' ? quantity : -quantity,
          entryPrice: config.price || this.context.marketData.price
        };
      } else {
        this.log(node.id, 'action', `Trade failed: ${result.error}`, 'error');
      }

      return tradeResult;
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : 'Unknown error';
      this.log(node.id, 'action', `Trade error: ${errorMsg}`, 'error');
      return null;
    }
  }

  private async executePositionSizerNode(node: Node): Promise<number> {
    const config = node.data?.config as PositionSizerConfig;
    const riskPct = config?.riskPerTradePct || 1;
    const useATR = config?.useATR !== false;
    const atrMultiplier = config?.atrMultiplier || 2;
    
    // Get ATR (using price as proxy if not available)
    const atr = useATR ? (this.context.marketData.atr || this.context.marketData.price * 0.02) : this.context.marketData.price * 0.01;
    
    // Calculate position size: (Equity * Risk%) / (ATR * Multiplier)
    const riskAmount = this.context.account.equity * (riskPct / 100);
    const positionSize = riskAmount / (atr * atrMultiplier);
    
    // Ensure minimum order value
    const minOrderValue = config?.minOrderValue || 10;
    const minQuantity = minOrderValue / this.context.marketData.price;
    const finalSize = Math.max(positionSize, minQuantity);
    
    this.log(node.id, 'positionSizer', 
      `Equity: $${this.context.account.equity.toFixed(2)}, Risk: ${riskPct}%, ` +
      `ATR: ${atr.toFixed(4)}, Calculated size: ${finalSize.toFixed(6)}`,
      'info'
    );

    return finalSize;
  }

  private async executeRiskManagementNode(node: Node): Promise<{sl: number, tp: number, trailing?: number} | null> {
    const config = node.data?.config as RiskManagementConfig;
    
    if (!this.context.position) {
      this.log(node.id, 'riskManagement', 'No active position to set risk levels', 'warning');
      return null;
    }

    const entryPrice = this.context.position.entryPrice;
    const atr = this.context.marketData.atr || entryPrice * 0.02;
    
    let stopLoss: number;
    
    if (config?.stopLossType === 'atr') {
      stopLoss = this.setStopLoss(entryPrice, atr, config?.stopLossValue || 2);
    } else {
      // Fixed stop loss
      const slPct = (config?.stopLossValue || 0.5) / 100;
      stopLoss = entryPrice * (1 - slPct);
    }

    // Calculate take profit based on risk:reward ratio
    const risk = entryPrice - stopLoss;
    const rewardRatio = config?.takeProfitRatio || 2;
    const takeProfit = entryPrice + (risk * rewardRatio);
    
    // Trailing stop
    let trailingStop: number | undefined;
    if (config?.useTrailingStop) {
      const trailPct = (config?.trailingStopPct || 1) / 100;
      trailingStop = entryPrice * (1 - trailPct);
    }

    // Update context
    this.context.position.stopLoss = stopLoss;
    this.context.position.takeProfit = takeProfit;

    this.log(node.id, 'riskManagement', 
      `Entry: ${entryPrice.toFixed(4)}, SL: ${stopLoss.toFixed(4)}, TP: ${takeProfit.toFixed(4)} ` +
      `(R:R = 1:${rewardRatio})`,
      'info'
    );

    return { sl: stopLoss, tp: takeProfit, trailing: trailingStop };
  }

  private async executeKillswitchNode(node: Node): Promise<boolean> {
    const config = node.data?.config as KillswitchConfig;
    
    if (!config?.enabled) {
      return true; // Killswitch disabled
    }

    const maxLoss = config?.maxDailyLossPct || 5;
    const dailyPnL = this.context.account.dailyPnL;
    const equity = this.context.account.equity;
    const lossPct = dailyPnL < 0 ? Math.abs(dailyPnL / equity) * 100 : 0;

    this.log(node.id, 'killswitch', 
      `Daily PnL: $${dailyPnL.toFixed(2)} (${lossPct.toFixed(2)}%), Max Loss: ${maxLoss}%`,
      lossPct >= maxLoss ? 'warning' : 'info'
    );

    if (lossPct >= maxLoss) {
      this.context.halted = true;
      this.context.haltReason = `Daily loss limit exceeded: ${lossPct.toFixed(2)}% >= ${maxLoss}%`;
      this.log(node.id, 'killswitch', this.context.haltReason, 'error');
      
      // In production, persist halt state to backend
      return false;
    }

    return true;
  }

  private evaluateEdgeCondition(label: string, nodeResult: any, nodeType: string): boolean {
    const lowerLabel = label.toLowerCase();
    
    // Handle boolean results from conditions/filters
    if (typeof nodeResult === 'boolean') {
      if (lowerLabel.includes('true') || lowerLabel.includes('pass')) return nodeResult === true;
      if (lowerLabel.includes('false') || lowerLabel.includes('fail')) return nodeResult === false;
      if (lowerLabel.includes('yes')) return nodeResult === true;
      if (lowerLabel.includes('no')) return nodeResult === false;
    }

    // Default: follow edge if node succeeded
    return nodeResult !== null && nodeResult !== false;
  }

  private calculatePositionSize(equity: number, riskPct: number, atr: number): number {
    const riskAmount = equity * (riskPct / 100);
    return riskAmount / atr;
  }

  private setStopLoss(entryPrice: number, atr: number, multiplier: number): number {
    return entryPrice - (atr * multiplier);
  }

  private log(nodeId: string, nodeType: string, message: string, level: ExecutionLog['level']) {
    const logEntry: ExecutionLog = {
      timestamp: Date.now(),
      nodeId,
      nodeType,
      message,
      level
    };
    this.context.logs.push(logEntry);
    console.log(`[${level.toUpperCase()}] ${nodeType}(${nodeId}): ${message}`);
  }
}

export const workflowEngine = new WorkflowEngine();
export default workflowEngine;
