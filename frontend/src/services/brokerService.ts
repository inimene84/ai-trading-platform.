import { monitoringService } from './monitoringService';
import { automationService } from './automationService';
import { apiService } from './apiService';

export interface OrderParams {
  symbol: string;
  quantity: number;
  type: 'market' | 'limit';
  price?: number;
  side: 'buy' | 'sell';
  stopLoss?: number;
  takeProfit?: number;
}

export interface ExecutionResult {
  success: boolean;
  orderId?: string;
  error?: string;
  timestamp: number;
  filledPrice?: number;
  tradeId?: number;
}

class BrokerService {
  private getSettings() {
    const saved = localStorage.getItem('quantum_trade_settings');
    return saved ? JSON.parse(saved) : {};
  }

  async executeBinance(params: OrderParams): Promise<ExecutionResult> {
    // Forward to backend live broker endpoint
    try {
      const res = await apiService.placeOrder(
        params.symbol,
        params.side,
        params.quantity,
        params.type,
        params.price || 0,
        params.stopLoss,
        params.takeProfit,
      );
      if (!res.order_id) throw new Error('Backend returned no exchange order ID');
      const result: ExecutionResult = {
        success: true,
        orderId: res.order_id,
        filledPrice: res.filled_price,
        tradeId: res.trade_id,
        timestamp: Date.now()
      };
      await this.logAndTrack(params, result, 'binance');
      return result;
    } catch (e: any) {
      const errRes: ExecutionResult = {
        success: false,
        error: e.message || 'Binance execution failed',
        timestamp: Date.now()
      };
      await this.logAndTrack(params, errRes, 'binance');
      return errRes;
    }
  }

  async executeCTrader(params: OrderParams): Promise<ExecutionResult> {
    try {
      const res = await apiService.placeOrder(
        params.symbol, params.side, params.quantity, params.type,
        params.price || 0, params.stopLoss, params.takeProfit,
      );
      if (!res.order_id) throw new Error('Backend returned no exchange order ID');
      const result: ExecutionResult = {
        success: true,
        orderId: res.order_id,
        filledPrice: res.filled_price,
        tradeId: res.trade_id,
        timestamp: Date.now()
      };
      await this.logAndTrack(params, result, 'ctrader');
      return result;
    } catch (e: any) {
      const errRes: ExecutionResult = {
        success: false,
        error: e.message || 'cTrader execution failed',
        timestamp: Date.now()
      };
      await this.logAndTrack(params, errRes, 'ctrader');
      return errRes;
    }
  }

  private async logAndTrack(params: OrderParams, result: ExecutionResult, broker: string) {
    await monitoringService.logTrade({
      symbol: params.symbol,
      side: params.side,
      quantity: params.quantity,
      price: params.price || 0,
      broker,
      orderId: result.orderId || 'N/A',
      success: result.success,
      timestamp: result.timestamp
    });
    await automationService.triggerTradeEvent({
      event: result.success ? 'trade_executed' : 'trade_failed',
      payload: { ...params, ...result, broker }
    });
  }

  async executeTrade(broker: 'binance' | 'ctrader', params: OrderParams): Promise<ExecutionResult> {
    if (broker === 'binance') {
      return this.executeBinance(params);
    } else {
      return this.executeCTrader(params);
    }
  }
}

export const brokerService = new BrokerService();
