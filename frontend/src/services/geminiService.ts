import { GoogleGenAI, ThinkingLevel } from "@google/genai";
import { configService } from "./configService";

let aiClient: GoogleGenAI | null = null;

function getAiClient() {
  if (!aiClient) {
    const apiKey = configService.getSecret('GEMINI_API_KEY');
    if (!apiKey) throw new Error("GEMINI_API_KEY is not configured.");
    aiClient = new GoogleGenAI({ apiKey });
  }
  return aiClient;
}

export const geminiService = {
  /**
   * General chat with Gemini
   */
  async chat(message: string, history: { role: string; parts: { text: string }[] }[] = []) {
    const ai = getAiClient();
    const response = await ai.models.generateContent({
      model: "gemini-3-flash-preview",
      contents: [...history, { role: "user", parts: [{ text: message }] }],
      config: {
        systemInstruction: "You are a professional trading assistant for QuantumTrade Pro. You help users with market analysis, trading strategies, and building AI agent workflows. Be concise, professional, and insightful.",
      },
    });
    return response.text;
  },

  /**
   * Analyze trading data for insights
   */
  async analyzeMarket(data: any) {
    const ai = getAiClient();
    const response = await ai.models.generateContent({
      model: "gemini-3.1-flash-lite-preview",
      contents: `Analyze this trading data and provide a brief summary of trends and potential actions: ${JSON.stringify(data)}`,
      config: {
        systemInstruction: "You are a professional market analyst. Provide concise, actionable trading insights based on the provided data. Focus on immediate trends and clear 'Buy', 'Sell', or 'Hold' recommendations with brief justifications.",
      },
    });
    return response.text;
  },

  /**
   * Analyze backtest results
   */
  async analyzeBacktest(results: any) {
    const ai = getAiClient();
    const response = await ai.models.generateContent({
      model: "gemini-3.1-pro-preview",
      contents: `Analyze these backtest results and provide a summary of performance and recommendations for improvement: ${JSON.stringify(results)}`,
      config: {
        systemInstruction: "You are a quantitative trading expert. Analyze the provided backtest results (profit, win rate, drawdown, trades). Provide a concise summary of the strategy's performance and 2-3 specific, actionable recommendations to improve the results.",
      },
    });
    return response.text;
  },

  /**
   * Help build or optimize a workflow
   */
  async optimizeWorkflow(workflow: any) {
    const ai = getAiClient();
    const response = await ai.models.generateContent({
      model: "gemini-3.1-pro-preview",
      contents: `Review this trading bot workflow and suggest improvements or missing nodes: ${JSON.stringify(workflow)}`,
      config: {
        thinkingConfig: { thinkingLevel: ThinkingLevel.HIGH },
        systemInstruction: "You are an expert in algorithmic trading and workflow automation. Analyze the provided React Flow structure and suggest optimizations for risk management, efficiency, and profit potential.",
      },
    });
    return response.text;
  }
};
