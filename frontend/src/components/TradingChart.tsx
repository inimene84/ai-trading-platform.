import React, { useEffect, useRef } from 'react';
import { createChart, ColorType, CandlestickSeries, HistogramSeries } from 'lightweight-charts';

interface TradingChartProps {
  data: {
    time: string | number;
    open: number;
    high: number;
    low: number;
    close: number;
    volume?: number;
  }[];
}

export const TradingChart: React.FC<TradingChartProps> = ({ data }) => {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<any>(null);
  const candlestickSeriesRef = useRef<any>(null);
  const volumeSeriesRef = useRef<any>(null);

  // Initialize chart
  useEffect(() => {
    if (!chartContainerRef.current) return;

    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#71717a',
      },
      grid: {
        vertLines: { color: '#1f1f22' },
        horzLines: { color: '#1f1f22' },
      },
      width: chartContainerRef.current.clientWidth,
      height: 400,
      timeScale: {
        borderColor: '#27272a',
        timeVisible: true,
        secondsVisible: false,
      },
      rightPriceScale: {
        borderColor: '#27272a',
      },
      crosshair: {
        vertLine: {
          color: '#3f3f46',
          labelBackgroundColor: '#18181b',
        },
        horzLine: {
          color: '#3f3f46',
          labelBackgroundColor: '#18181b',
        },
      },
    }) as any;

    const candlestickSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#10b981',
      downColor: '#ef4444',
      borderVisible: false,
      wickUpColor: '#10b981',
      wickDownColor: '#ef4444',
    });

    const volumeSeries = chart.addSeries(HistogramSeries, {
      color: '#26a69a',
      priceFormat: {
        type: 'volume',
      },
      priceScaleId: '',
    });

    volumeSeries.priceScale().applyOptions({
      scaleMargins: {
        top: 0.7,
        bottom: 0,
      },
    });

    chartRef.current = chart;
    candlestickSeriesRef.current = candlestickSeries;
    volumeSeriesRef.current = volumeSeries;

    const handleResize = () => {
      if (chartContainerRef.current && chartRef.current) {
        chartRef.current.applyOptions({ width: chartContainerRef.current.clientWidth });
      }
    };

    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
    };
  }, []);

  // Update data
  useEffect(() => {
    if (!candlestickSeriesRef.current || !volumeSeriesRef.current || !data.length) return;

    // If data is just one item, it's an update
    if (data.length === 1) {
      const item = data[0];
      candlestickSeriesRef.current.update(item);
      volumeSeriesRef.current.update({
        time: item.time,
        value: item.volume || 0,
        color: item.close >= item.open ? 'rgba(16, 185, 129, 0.3)' : 'rgba(239, 68, 68, 0.3)',
      });
    } else {
      // Full data set
      candlestickSeriesRef.current.setData(data);
      const volumeData = data.map(d => ({
        time: d.time,
        value: d.volume || 0,
        color: d.close >= d.open ? 'rgba(16, 185, 129, 0.3)' : 'rgba(239, 68, 68, 0.3)',
      }));
      volumeSeriesRef.current.setData(volumeData);
    }
  }, [data]);

  return <div ref={chartContainerRef} className="w-full h-full min-h-[400px]" />;
};
