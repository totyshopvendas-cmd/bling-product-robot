import axios from "axios";

const BACKEND_URL = (process.env.REACT_APP_BACKEND_URL || "").replace(/\/$/, "");
export const API_BASE = BACKEND_URL ? `${BACKEND_URL}/api` : "/api";

const API_TIMEOUT_MS = 60000;

export const api = axios.create({
  baseURL: API_BASE,
  timeout: API_TIMEOUT_MS,
});

export const endpoints = {
  dashboardStats: () => api.get("/dashboard/stats"),

  cleanTitle: (raw_title, sku, use_llm_fallback = false) =>
    api.post("/titles/clean", { raw_title, sku, use_llm_fallback }),
  cleanTitleBatch: (items) => api.post("/titles/clean/batch", { items }),

  uploadPricing: (file) => {
    const fd = new FormData();
    fd.append("file", file);
    return api.post("/pricing/upload", fd, { timeout: 180000 });
  },
  loadPricing: () => api.post("/pricing/load", {}, { timeout: 180000 }),
  pricingStats: () => api.get("/pricing/stats"),
  lookupPrice: (cost) => api.get(`/pricing/lookup?cost=${cost}`),

  blingAuthorizeUrl: (origin) =>
    api.get("/bling/authorize-url", { params: origin ? { origin } : {} }),
  blingOAuthConfig: (origin) =>
    api.get("/bling/oauth-config", { params: origin ? { origin } : {} }),
  blingStatus: () => api.get("/bling/status"),
  blingPing: () => api.get("/bling/ping"),
  blingDisconnect: () => api.post("/bling/disconnect"),
  blingProducts: (pagina = 1, limite = 20) =>
    api.get(`/bling/products?pagina=${pagina}&limite=${limite}`),
  saveBlingApp: (client_id, client_secret) =>
    api.post("/settings/bling-app", { client_id, client_secret }),

  setJohnDropCreds: (username, password) =>
    api.post("/settings/johndrop", { username, password }),
  getJohnDropStatus: () => api.get("/settings/johndrop"),

  robotStart: (max_products, dry_run) =>
    api.post("/robot/start", { max_products, dry_run }),
  robotStop: () => api.post("/robot/stop"),
  robotStatus: () => api.get("/robot/status"),
  robotLogs: (limit = 100) => api.get(`/robot/logs?limit=${limit}`),
  robotLogsClear: () => api.post("/robot/logs/clear"),
};
