import { apiService } from "./apiService";

export interface ChatMessage {
  role: "user" | "model";
  text: string;
}

/**
 * Dashboard assistant — server-side via OmniRoute (no browser GEMINI_API_KEY).
 */
export const geminiService = {
  async chat(message: string, history: ChatMessage[] = []) {
    const res = await apiService.assistantChat(
      message,
      history.map((h) => ({ role: h.role, text: h.text })),
    );
    return res.reply as string;
  },

  async analyzeMarket(data: unknown) {
    return this.chat(
      `Analyze this trading data and provide trends and potential actions as JSON summary:\n${JSON.stringify(data).slice(0, 6000)}`,
    );
  },

  async analyzeBacktest(results: unknown) {
    return this.chat(
      `Analyze these backtest results. Summarize performance and give 2-3 improvement recommendations:\n${JSON.stringify(results).slice(0, 6000)}`,
    );
  },

  async optimizeWorkflow(workflow: unknown) {
    return this.chat(
      `Review this trading workflow graph and suggest risk/efficiency improvements:\n${JSON.stringify(workflow).slice(0, 6000)}`,
    );
  },
};
