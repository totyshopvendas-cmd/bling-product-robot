import axios from "axios";

const BACKEND_URL = (process.env.REACT_APP_BACKEND_URL || "").replace(/\/$/, "");
export const API_BASE = BACKEND_URL ? `${BACKEND_URL}/api` : "/api";

const API_TIMEOUT_MS = 60000;

export const api = axios.create({
  baseURL: API_BASE,
  timeout: API_TIMEOUT_MS,
  withCredentials: !BACKEND_URL, // cookie de login só no mesmo domínio
});

// Sessão expirou (servidor com APP_PASSWORD) → volta para a tela de login.
api.interceptors.response.use(
  (res) => res,
  (err) => {
    const url = err?.config?.url || "";
    if (err?.response?.status === 401 && !url.includes("/auth/")) {
      window.dispatchEvent(new Event("totyshop:auth-required"));
    }
    return Promise.reject(err);
  },
);

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

  setDropperCreds: (username, password) =>
    api.post("/settings/dropper", { username, password }),
  getDropperStatus: () => api.get("/settings/dropper"),

  dropperStart: (max_products, dry_run) =>
    api.post("/dropper/start", { max_products, dry_run }),
  dropperStop: () => api.post("/dropper/stop"),
  dropperStatus: () => api.get("/dropper/status"),
  dropperLogs: (limit = 100) => api.get(`/dropper/logs?limit=${limit}`),
  dropperSheet: () => api.get("/dropper/sheet"),
  dropperResetHistorico: () => api.post("/dropper/historico/reset"),
  dropperGetConfig: () => api.get("/dropper/config"),
  dropperPiloto: (url_produto = "", use_ai = false) =>
    api.post("/dropper/bling/piloto", { url_produto, use_ai }, { timeout: 300000 }),
  dropperSetConfig: (fornecedor, usar_categoria) =>
    api.post("/dropper/config", { fornecedor, usar_categoria }),
  dropperSheetDownload: (name) =>
    api.get("/dropper/sheet/download", {
      params: name ? { name } : {},
      responseType: "blob",
      timeout: 120000,
    }),

  robotStart: (max_products, dry_run) =>
    api.post("/robot/start", { max_products, dry_run }),
  robotStop: () => api.post("/robot/stop"),
  robotStatus: () => api.get("/robot/status"),
  robotLogs: (limit = 100) => api.get(`/robot/logs?limit=${limit}`),
  robotLogsClear: () => api.post("/robot/logs/clear"),

  getBrandSettings: () => api.get("/settings/brand"),
  saveBrandSettings: (mode, store_name) =>
    api.post("/settings/brand", { mode, store_name }),

  reviewEstimate: (max_items) => api.post("/review/estimate", { max_items }),
  reviewStart: (product_ids, max_items) =>
    api.post("/review/start", { product_ids, max_items }),
  reviewStatus: () => api.get("/review/status"),
  reviewPause: () => api.post("/review/pause"),
  reviewResume: () => api.post("/review/resume"),
  reviewStop: () => api.post("/review/stop"),
  reviewMarkDelete: (payload) => api.post("/review/mark-delete", payload),
  reviewApproveDeletes: () => api.post("/review/approve-deletes"),
  reviewClearDeleteList: () => api.post("/review/clear-delete-list"),
  blingDeleteProduct: (product_id) => api.delete(`/bling/products/${product_id}`),
};
