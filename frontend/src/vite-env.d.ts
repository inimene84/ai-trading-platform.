/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_BACKEND_URL?: string;
  readonly VITE_BINANCE_DIRECT_WS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
