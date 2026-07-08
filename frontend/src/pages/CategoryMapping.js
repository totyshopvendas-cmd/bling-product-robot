import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { RefreshCw, Play, Loader2, CheckCircle2, XCircle, Search, Zap } from "lucide-react";
import { api } from "@/lib/api";
import { logger } from "@/lib/logger";

const POLL_MS = 4000;

export default function CategoryMappingPage() {
  const [status, setStatus] = useState(null);
  const [autoSyncStatus, setAutoSyncStatus] = useState(null);
  const [pendingNew, setPendingNew] = useState(null);
  const [running, setRunning] = useState(false);
  const [syncingNew, setSyncingNew] = useState(false);
  const [previews, setPreviews] = useState([]);
  const [availableMkts, setAvailableMkts] = useState([]);
  const [filterMkt, setFilterMkt] = useState("");
  const [filterQuery, setFilterQuery] = useState("");
  const [bling_user, setBlingUser] = useState("");
  const [bling_pass, setBlingPass] = useState("");
  const [showAuth, setShowAuth] = useState(false);
  const [authMode, setAuthMode] = useState("scan"); // "scan" | "sync"
  const pollRef = useRef(null);

  const loadStatus = useCallback(async () => {
    try {
      const { data } = await api.get("/category-mapping/status");
      setStatus(data);
      setRunning(Boolean(data?.running));
    } catch (err) {
      logger.error("catmap status:", err);
    }
  }, []);

  const loadAutoSyncStatus = useCallback(async () => {
    try {
      const { data } = await api.get("/category-mapping/sync-new/status");
      setAutoSyncStatus(data);
      setSyncingNew(Boolean(data?.running));
    } catch (err) {
      logger.error("autosync status:", err);
    }
  }, []);

  const loadPendingCount = useCallback(async () => {
    try {
      const { data } = await api.get("/category-mapping/new-count");
      setPendingNew(data?.pending ?? null);
    } catch (err) {
      logger.error("pending count:", err);
    }
  }, []);

  const loadMarketplaces = useCallback(async () => {
    try {
      const { data } = await api.get("/category-mapping/marketplaces");
      setAvailableMkts(data?.items || []);
    } catch (err) {
      logger.error("marketplaces:", err);
    }
  }, []);

  const loadPreviews = useCallback(async () => {
    try {
      const params = {};
      if (filterMkt) params.marketplace = filterMkt;
      const { data } = await api.get("/category-mapping/previews", { params });
      setPreviews(data.items || []);
    } catch (err) {
      logger.error("catmap previews:", err);
    }
  }, [filterMkt]);

  useEffect(() => {
    loadStatus();
    loadAutoSyncStatus();
    loadPendingCount();
    loadMarketplaces();
    loadPreviews();
    pollRef.current = setInterval(() => {
      loadStatus();
      loadAutoSyncStatus();
      if (running || syncingNew) {
        loadPreviews();
        loadPendingCount();
        loadMarketplaces();
      }
    }, POLL_MS);
    return () => pollRef.current && clearInterval(pollRef.current);
  }, [loadStatus, loadAutoSyncStatus, loadPendingCount, loadMarketplaces, loadPreviews, running, syncingNew]);

  const runScan = async () => {
    if (!bling_user || !bling_pass) {
      setAuthMode("scan");
      setShowAuth(true);
      return;
    }
    try {
      const { data } = await api.post("/category-mapping/scan", { bling_user, bling_pass });
      if (data?.ok) {
        toast.success("Scan iniciado — pode levar 5-10 min");
        setRunning(true);
        setShowAuth(false);
      } else {
        toast.warning(data?.message || "Já em execução");
      }
    } catch (err) {
      toast.error("Falha ao iniciar scan");
    }
  };

  const runSyncNew = async () => {
    if (!bling_user || !bling_pass) {
      setAuthMode("sync");
      setShowAuth(true);
      return;
    }
    try {
      const { data } = await api.post("/category-mapping/sync-new", {
        bling_user, bling_pass, apply: true,
      });
      if (data?.ok) {
        toast.success("Sincronização iniciada — mapeando e aplicando novas categorias");
        setSyncingNew(true);
        setShowAuth(false);
      } else {
        toast.warning(data?.message || "Já em execução");
      }
    } catch (err) {
      toast.error("Falha ao sincronizar novas");
    }
  };

  const confirmAuth = () => {
    if (authMode === "sync") runSyncNew();
    else runScan();
  };

  const approve = async (item, approved) => {
    try {
      await api.post("/category-mapping/approve", {
        bling_category_id: item.bling_category_id,
        marketplace: item.marketplace,
        approved,
      });
      setPreviews((prev) => prev.map((p) =>
        p.bling_category_id === item.bling_category_id && p.marketplace === item.marketplace
          ? { ...p, approved } : p
      ));
    } catch (err) {
      toast.error("Falha ao salvar");
    }
  };

  const uniqueMkts = Array.from(
    new Set([...availableMkts, ...previews.map((p) => p.marketplace)])
  ).filter(Boolean).sort();
  const filtered = previews.filter((p) => {
    if (filterQuery) {
      const q = filterQuery.toLowerCase();
      return (
        (p.bling_category_name || "").toLowerCase().includes(q) ||
        (p.suggestion_name || "").toLowerCase().includes(q)
      );
    }
    return true;
  });
  const counts = {
    total: previews.length,
    approved: previews.filter((p) => p.approved).length,
    high_conf: previews.filter((p) => p.confidence >= 0.7).length,
    low_conf: previews.filter((p) => p.confidence < 0.5).length,
  };

  return (
    <div className="space-y-6" data-testid="category-mapping-page">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-display font-semibold tracking-tight">
            Vincular Categorias Multiloja
          </h1>
          <p className="text-sm text-muted-foreground mt-1 max-w-3xl">
            Robô abre o Bling, lê a árvore de categorias de cada marketplace conectado
            (Amazon, Shopee, ML, Kwai, etc.), e a IA sugere o mapeamento de cada categoria
            Bling. Ao clicar em <strong>&quot;Sincronizar Novas&quot;</strong> o robô detecta apenas
            as categorias ainda não mapeadas, faz o match com IA e aplica automaticamente
            no Bling — sem revisão manual.
          </p>
        </div>
        <div className="flex flex-col gap-2 items-end">
          <button
            data-testid="run-catmap-sync-new"
            onClick={runSyncNew}
            disabled={syncingNew || running}
            className="inline-flex items-center gap-2 bg-emerald-600 hover:bg-emerald-700 text-white px-4 py-2 text-sm font-medium disabled:opacity-50"
          >
            {syncingNew ? (
              <><Loader2 className="h-4 w-4 animate-spin" /> Sincronizando...</>
            ) : (
              <>
                <Zap className="h-4 w-4" />
                Sincronizar Novas
                {typeof pendingNew === "number" && pendingNew > 0 && (
                  <span className="ml-1 bg-white text-emerald-700 rounded-full px-2 py-0.5 text-xs font-semibold">
                    {pendingNew}
                  </span>
                )}
              </>
            )}
          </button>
          <button
            data-testid="run-catmap-scan"
            onClick={runScan}
            disabled={running || syncingNew}
            className="inline-flex items-center gap-2 bg-[#EE7B22] hover:bg-[#d96d1c] text-white px-4 py-2 text-sm font-medium disabled:opacity-50"
          >
            {running ? (
              <><Loader2 className="h-4 w-4 animate-spin" /> Escaneando...</>
            ) : (
              <><Play className="h-4 w-4" /> Rescan completo</>
            )}
          </button>
        </div>
      </div>

      {showAuth && (
        <div className="border border-amber-200 bg-amber-50 p-4 space-y-3">
          <p className="text-sm text-amber-900">
            <strong>Credenciais Bling necessárias</strong> — o robô precisa
            logar no painel web (Playwright) porque a API pública do Bling não
            expõe o endpoint de vinculação multiloja. As credenciais NÃO são
            salvas — só usadas nesta execução.
          </p>
          <div className="grid grid-cols-2 gap-3">
            <input
              data-testid="bling-user-input"
              type="text"
              placeholder="Usuário Bling"
              value={bling_user}
              onChange={(e) => setBlingUser(e.target.value)}
              className="border border-border px-3 py-2 text-sm"
            />
            <input
              data-testid="bling-pass-input"
              type="password"
              placeholder="Senha Bling"
              value={bling_pass}
              onChange={(e) => setBlingPass(e.target.value)}
              className="border border-border px-3 py-2 text-sm"
            />
          </div>
          <button
            data-testid="confirm-scan-btn"
            onClick={confirmAuth}
            className="text-sm bg-zinc-900 text-white px-3 py-1.5"
          >
            {authMode === "sync" ? "Confirmar e Sincronizar" : "Confirmar e Escanear"}
          </button>
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Card label="Pares gerados" value={counts.total} />
        <Card label="Aprovados" value={counts.approved} tone="emerald" />
        <Card label="Alta confiança (≥70%)" value={counts.high_conf} tone="blue" />
        <Card label="Baixa confiança (<50%)" value={counts.low_conf} tone="rose" />
      </div>

      {running && (
        <div className="border border-orange-200 bg-orange-50 px-4 py-3 text-sm text-orange-900 flex items-center gap-3">
          <Loader2 className="h-4 w-4 animate-spin" />
          Scan em andamento. Status: <strong>{status?.run?.status}</strong>{" "}
          {status?.run?.done && `— ${status.run.done}/${status.run.total_pairs} pares`}
        </div>
      )}

      {syncingNew && (
        <div className="border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900 flex items-center gap-3" data-testid="autosync-banner">
          <Loader2 className="h-4 w-4 animate-spin" />
          Sincronizando novas categorias — fase: <strong>{autoSyncStatus?.run?.phase || "..."}</strong>
          {autoSyncStatus?.run?.new_count != null && ` — ${autoSyncStatus.run.new_count} novas`}
          {autoSyncStatus?.run?.applied_count != null && ` — ${autoSyncStatus.run.applied_count} aplicadas`}
        </div>
      )}

      {!syncingNew && autoSyncStatus?.last_summary && (
        <div className="border border-emerald-100 bg-emerald-50/40 px-4 py-2 text-xs text-emerald-900" data-testid="autosync-summary">
          Última auto-sync: {autoSyncStatus.last_summary.new_count} novas categorias →
          {" "}{autoSyncStatus.last_summary.created_pairs} pares gerados,
          {" "}{autoSyncStatus.last_summary.applied} vínculos aplicados no Bling.
        </div>
      )}

      <div className="flex items-center gap-3 text-sm">
        <select
          data-testid="filter-marketplace"
          value={filterMkt}
          onChange={(e) => setFilterMkt(e.target.value)}
          className="border border-border px-3 py-1.5"
        >
          <option value="">Todos marketplaces</option>
          {uniqueMkts.map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
        <div className="flex items-center gap-2 border border-border px-2">
          <Search className="h-4 w-4 text-zinc-500" />
          <input
            data-testid="filter-query"
            value={filterQuery}
            onChange={(e) => setFilterQuery(e.target.value)}
            placeholder="Buscar categoria..."
            className="px-2 py-1.5 text-sm outline-none w-56"
          />
        </div>
        <button
          onClick={() => { loadStatus(); loadPreviews(); }}
          className="ml-auto text-xs inline-flex items-center gap-1 border border-border px-3 py-1.5"
        >
          <RefreshCw className="h-3 w-3" /> Atualizar
        </button>
      </div>

      <div className="border border-border bg-white overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-zinc-50 text-xs text-zinc-600">
            <tr>
              <th className="px-3 py-2 text-left">Categoria Bling</th>
              <th className="px-3 py-2 text-left">Marketplace</th>
              <th className="px-3 py-2 text-left">Sugestão IA</th>
              <th className="px-3 py-2 text-right">Confiança</th>
              <th className="px-3 py-2 text-center">Ação</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr><td colSpan={5} className="px-3 py-12 text-center text-muted-foreground text-sm">
                {previews.length === 0
                  ? 'Nenhum scan executado ainda. Clique em "Escanear categorias".'
                  : "Nenhum item neste filtro."}
              </td></tr>
            ) : filtered.slice(0, 300).map((p, i) => (
              <tr key={`${p.bling_category_id}-${p.marketplace}-${i}`} className="border-t border-border">
                <td className="px-3 py-2">{p.bling_category_name}</td>
                <td className="px-3 py-2 text-xs">{p.marketplace}</td>
                <td className="px-3 py-2">
                  {p.suggestion_name || <span className="text-zinc-400 italic">sem sugestão</span>}
                </td>
                <td className="px-3 py-2 text-right font-mono text-xs">
                  <span className={`px-2 py-0.5 ${
                    p.confidence >= 0.7 ? "bg-emerald-50 text-emerald-800"
                    : p.confidence >= 0.4 ? "bg-amber-50 text-amber-800"
                    : "bg-rose-50 text-rose-800"
                  }`}>
                    {((p.confidence || 0) * 100).toFixed(0)}%
                  </span>
                </td>
                <td className="px-3 py-2 text-center">
                  {p.approved ? (
                    <button
                      onClick={() => approve(p, false)}
                      className="inline-flex items-center gap-1 text-xs px-2 py-1 bg-emerald-50 border border-emerald-300 text-emerald-800"
                    >
                      <CheckCircle2 className="h-3 w-3" /> Aprovado
                    </button>
                  ) : (
                    <button
                      onClick={() => approve(p, true)}
                      disabled={!p.suggestion_id}
                      className="inline-flex items-center gap-1 text-xs px-2 py-1 border border-border hover:bg-zinc-50 disabled:opacity-40"
                    >
                      Aprovar
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {filtered.length > 300 && (
        <div className="text-xs text-muted-foreground">
          Mostrando 300 de {filtered.length}. Use o filtro para refinar.
        </div>
      )}
    </div>
  );
}

function Card({ label, value, tone = "default" }) {
  const t = { default: "border-border", emerald: "border-emerald-200",
              blue: "border-blue-200", rose: "border-rose-200" }[tone];
  return (
    <div className={`border bg-white px-4 py-3 ${t}`}>
      <div className="text-xs text-zinc-500">{label}</div>
      <div className="text-2xl font-mono font-semibold mt-1">{value}</div>
    </div>
  );
}
