import axios from "axios";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
export const API_BASE = `${BACKEND_URL}/api`;

export const api = axios.create({
  baseURL: API_BASE,
  timeout: 60000,
});

export const endpoints = {
  dashboardStats: () => api.get("/dashboard/stats"),

  cleanTitle: (raw_title, sku, use_llm_fallback = false) =>
    api.post("/titles/clean", { raw_title, sku, use_llm_fallback }),
  cleanTitleBatch: (items) => api.post("/titles/clean/batch", { items }),

  uploadPricing: (file) => {
    const fd = new FormData();
    fd.append("file", file);
    return api.post("/pricing/upload", fd, {
      headers: { "Content-Type": "multipart/form-data" },
    });
  },
  pricingStats: () => api.get("/pricing/stats"),
  lookupPrice: (cost) => api.get(`/pricing/lookup?cost=${cost}`),

  blingAuthorizeUrl: () => api.get("/bling/authorize-url"),
  blingStatus: () => api.get("/bling/status"),
  blingDisconnect: () => api.post("/bling/disconnect"),
  blingProducts: (pagina = 1, limite = 20) =>
    api.get(`/bling/products?pagina=${pagina}&limite=${limite}`),

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
