/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

export interface WorkflowNodeData {
  label: string;
  type: string;
  icon: any;
  color?: string;
  config?: {
    symbol: string;
    quantity: number;
    orderType: 'market' | 'limit';
    side: 'buy' | 'sell';
    broker: 'binance' | 'ctrader';
    price?: number;
  };
}
