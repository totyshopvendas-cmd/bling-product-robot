import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { RefreshCw, Play, Loader2, CheckCircle2, XCircle, Search, Zap, AlertTriangle } from "lucide-react";
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
  const [lojas, setLojas] = useState([]);
  const [includeSubcats, setIncludeSubcats] = useState(true);
  const [renameLoja, setRenameLoja] = useState(null); // {loja_id, current_alias}
  const [renameValue, setRenameValue] = useState("");
  const [addLojaOpen, setAddLojaOpen] = useState(false);
  const [newLojaId, setNewLojaId] = useState("");
  const [newLojaName, setNewLojaName] = useState("");
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
      // Preferimos o api-sync (fluxo novo). Fallback para auto-sync legado.
      const { data } = await api.get("/category-mapping/sync-api/status");
      setAutoSyncStatus(data);
      setSyncingNew(Boolean(data?.running));
    } catch (err) {
      logger.error("apisync status:", err);
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
      const { data } = await api.get("/category-mapping/lojas");
      const items = data?.items || [];
      setLojas(items);
      const names = items.map((l) => l.name);
      try {
        const legacy = await api.get("/category-mapping/marketplaces");
        (legacy.data?.items || []).forEach((n) => {
          if (!names.includes(n)) names.push(n);
        });
      } catch (_) { /* ignora */ }
      setAvailableMkts(names.sort());
    } catch (err) {
      logger.error("marketplaces:", err);
    }
  }, []);

  const runApiSync = async () => {
    try {
      const { data } = await api.post("/category-mapping/sync-api", {
        include_subcategorias: includeSubcats,
        dry_run: false,
      });
      if (data?.ok) {
        toast.success("Sincronização API iniciada — usando Bling API oficial");
        setSyncingNew(true);
      } else {
        toast.warning(data?.message || "Já em execução");
      }
    } catch (err) {
      toast.error("Falha ao iniciar sincronização");
    }
  };

  const saveLojaAlias = async () => {
    if (!renameLoja || !renameValue.trim()) return;
    try {
      await api.put("/category-mapping/lojas/alias", {
        loja_id: renameLoja.loja_id,
        alias: renameValue.trim(),
      });
      toast.success(`Loja renomeada para "${renameValue.trim()}"`);
      setRenameLoja(null);
      setRenameValue("");
      loadMarketplaces();
    } catch (err) {
      toast.error("Falha ao renomear");
    }
  };

  const addKnownLoja = async () => {
    const id = parseInt(newLojaId.trim(), 10);
    if (!id || !newLojaName.trim()) {
      toast.warning("Informe ID da loja e nome");
      return;
    }
    try {
      await api.post("/category-mapping/lojas/known", {
        loja_id: id,
        name: newLojaName.trim(),
      });
      toast.success(`Loja "${newLojaName.trim()}" adicionada`);
      setAddLojaOpen(false);
      setNewLojaId("");
      setNewLojaName("");
      loadMarketplaces();
    } catch (err) {
      toast.error("Falha ao adicionar loja");
    }
  };

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
    applied: previews.filter((p) => p.applied).length,
    pending: previews.filter((p) => p.approved && !p.applied).length,
    no_suggestion: previews.filter((p) => !p.suggestion_id).length,
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
            <strong>Como funciona:</strong> ao clicar em{" "}
            <span className="text-emerald-700 font-semibold">Sincronizar via API Bling</span>{" "}
            usamos a API oficial do Bling (<code>/categorias/lojas</code>) — sem Playwright,
            sem timeouts. A IA faz <strong>classificação semântica</strong> (ex: &quot;Barbeador&quot; →
            &quot;Beleza e Cuidados Pessoais&quot;, não &quot;Eletrônicos&quot;) contra os vínculos que
            você já tem em cada loja. Só cria o vínculo real no Bling quando a{" "}
            <strong>confiança ≥ 60%</strong> — categorias sem match adequado ficam marcadas
            como &quot;Sem sugestão IA&quot; para você revisar. Marque{" "}
            <strong>&quot;Incluir subcategorias&quot;</strong> para processar filhas também.
          </p>
        </div>
        <div className="flex flex-col gap-2 items-end">
          <label className="flex items-center gap-2 text-xs text-zinc-600 select-none">
            <input
              type="checkbox"
              data-testid="toggle-include-subcats"
              checked={includeSubcats}
              onChange={(e) => setIncludeSubcats(e.target.checked)}
              className="h-3.5 w-3.5"
            />
            Incluir subcategorias
          </label>
          <button
            data-testid="run-catmap-sync-api"
            onClick={runApiSync}
            disabled={syncingNew || running}
            className="inline-flex items-center gap-2 bg-emerald-600 hover:bg-emerald-700 text-white px-4 py-2 text-sm font-medium disabled:opacity-50"
          >
            {syncingNew ? (
              <><Loader2 className="h-4 w-4 animate-spin" /> Sincronizando via API...</>
            ) : (
              <>
                <Zap className="h-4 w-4" />
                Sincronizar via API Bling
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
            className="inline-flex items-center gap-2 bg-zinc-500 hover:bg-zinc-600 text-white px-3 py-1.5 text-xs font-medium disabled:opacity-50"
            title="Fluxo Playwright — só funciona rodando local (o container não acessa bling.com.br)"
          >
            {running ? (
              <><Loader2 className="h-3 w-3 animate-spin" /> Escaneando...</>
            ) : (
              <><Play className="h-3 w-3" /> Rescan via Playwright (avançado)</>
            )}
          </button>
        </div>
      </div>

      {autoSyncStatus?.run?.status === "error" && !syncingNew && (
        <div
          data-testid="autosync-error-banner"
          className="border border-rose-300 bg-rose-50 px-4 py-3 text-sm text-rose-900 space-y-2"
        >
          <div className="flex items-center gap-2 font-semibold">
            <AlertTriangle className="h-4 w-4" />
            Última sincronização falhou
          </div>
          <div className="text-xs font-mono bg-white/60 border border-rose-200 px-2 py-1 whitespace-pre-wrap break-words">
            {String(autoSyncStatus.run.error || "").slice(0, 500)}
          </div>
          <p className="text-xs">
            <strong>Diagnóstico:</strong> este container não consegue navegar para <code>bling.com.br</code>{" "}
            (timeout de rede). O fluxo automático via Playwright precisa rodar num
            ambiente com acesso à web do Bling. Enquanto isso, use a lista abaixo
            (via <strong>API oficial do Bling</strong>) para ver marketplaces conectados
            e vínculos existentes.
          </p>
        </div>
      )}

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
        <Card label="Aplicados no Bling" value={counts.applied} tone="emerald" />
        <Card label="Aguardando aplicar" value={counts.pending} tone="blue" />
        <Card label="Sem sugestão IA" value={counts.no_suggestion} tone="rose" />
      </div>

      {/* Lojas conectadas */}
      {lojas.length > 0 && (
        <div className="border border-border bg-white" data-testid="lojas-panel">
          <div className="px-4 py-2 border-b border-border bg-zinc-50 flex items-center justify-between">
            <h3 className="text-sm font-semibold text-zinc-800">
              Lojas conectadas ({lojas.length})
            </h3>
            <div className="flex items-center gap-2">
              <span className="text-xs text-zinc-500 hidden md:inline">
                Renomear: ✏️ · Adicionar loja sem vínculos:
              </span>
              <button
                data-testid="btn-add-loja"
                onClick={() => setAddLojaOpen(true)}
                className="text-xs px-2 py-1 border border-emerald-400 text-emerald-700 hover:bg-emerald-50"
              >
                + Adicionar loja
              </button>
            </div>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2 p-3">
            {lojas.map((l) => (
              <div
                key={l.loja_id}
                data-testid={`loja-card-${l.loja_id}`}
                className="border border-border p-2.5 flex items-center justify-between text-sm"
              >
                <div className="min-w-0 flex-1">
                  <div className="font-medium truncate">
                    {l.name}
                    {l.custom_alias && (
                      <span className="ml-1 text-xs text-emerald-700">✓</span>
                    )}
                    {l.manual && (
                      <span
                        data-testid={`loja-manual-${l.loja_id}`}
                        className="ml-1 text-[10px] px-1 py-0.5 bg-blue-50 text-blue-700 border border-blue-200"
                      >
                        manual
                      </span>
                    )}
                  </div>
                  <div className="text-xs text-zinc-500 truncate">
                    ID {l.loja_id} · {l.linked_count} vínculos · código exemplo{" "}
                    <code className="bg-zinc-100 px-1">{l.sample_code}</code>
                  </div>
                </div>
                <button
                  data-testid={`rename-loja-${l.loja_id}`}
                  onClick={() => {
                    setRenameLoja(l);
                    setRenameValue(l.custom_alias ? l.name : "");
                  }}
                  className="ml-2 text-xs px-2 py-1 border border-border hover:bg-zinc-50"
                >
                  ✏️
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Modal de renomear loja */}
      {renameLoja && (
        <div
          data-testid="rename-loja-modal"
          className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4"
          onClick={() => setRenameLoja(null)}
        >
          <div
            className="bg-white border border-border max-w-md w-full p-5 space-y-3"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-lg font-semibold">Renomear loja</h3>
            <p className="text-sm text-muted-foreground">
              Loja ID <strong>{renameLoja.loja_id}</strong> · nome atual:{" "}
              <strong>{renameLoja.name}</strong>
              <br />
              Código de exemplo: <code>{renameLoja.sample_code}</code>
            </p>
            <input
              data-testid="rename-loja-input"
              type="text"
              placeholder="Ex: Kwai Shop, TikTok Shop, Nuvemshop..."
              value={renameValue}
              onChange={(e) => setRenameValue(e.target.value)}
              className="w-full border border-border px-3 py-2 text-sm"
              autoFocus
            />
            <div className="flex gap-2 justify-end">
              <button
                onClick={() => setRenameLoja(null)}
                className="text-sm px-3 py-1.5 border border-border hover:bg-zinc-50"
              >
                Cancelar
              </button>
              <button
                data-testid="save-loja-alias"
                onClick={saveLojaAlias}
                disabled={!renameValue.trim()}
                className="text-sm px-3 py-1.5 bg-zinc-900 text-white disabled:opacity-40"
              >
                Salvar
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Modal para adicionar loja Bling sem vínculos */}
      {addLojaOpen && (
        <div
          data-testid="add-loja-modal"
          className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4"
          onClick={() => setAddLojaOpen(false)}
        >
          <div
            className="bg-white border border-border max-w-md w-full p-5 space-y-3"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-lg font-semibold">Adicionar loja Bling</h3>
            <p className="text-sm text-muted-foreground">
              Para lojas que ainda não têm vínculos de categoria. Pegue o{" "}
              <strong>Código da loja API Bling</strong> na tela de autenticação do canal
              de venda no Bling (ex: <code>205274346</code>).
            </p>
            <div className="space-y-2">
              <div>
                <label className="text-xs text-zinc-600">Código da loja (ID)</label>
                <input
                  data-testid="add-loja-id-input"
                  type="text"
                  inputMode="numeric"
                  placeholder="205274346"
                  value={newLojaId}
                  onChange={(e) => setNewLojaId(e.target.value.replace(/[^0-9]/g, ""))}
                  className="w-full border border-border px-3 py-2 text-sm"
                />
              </div>
              <div>
                <label className="text-xs text-zinc-600">Nome do canal de venda</label>
                <input
                  data-testid="add-loja-name-input"
                  type="text"
                  placeholder="Ex: TotyShop - Amazon"
                  value={newLojaName}
                  onChange={(e) => setNewLojaName(e.target.value)}
                  className="w-full border border-border px-3 py-2 text-sm"
                />
              </div>
            </div>
            <div className="flex gap-2 justify-end">
              <button
                onClick={() => setAddLojaOpen(false)}
                className="text-sm px-3 py-1.5 border border-border hover:bg-zinc-50"
              >
                Cancelar
              </button>
              <button
                data-testid="save-add-loja"
                onClick={addKnownLoja}
                disabled={!newLojaId.trim() || !newLojaName.trim()}
                className="text-sm px-3 py-1.5 bg-emerald-600 text-white disabled:opacity-40"
              >
                Adicionar
              </button>
            </div>
          </div>
        </div>
      )}

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
          Sincronizando via API Bling — fase: <strong>{autoSyncStatus?.run?.phase || "..."}</strong>
          {autoSyncStatus?.run?.total_pairs != null && ` — ${autoSyncStatus.run.total_pairs} pares processados`}
          {autoSyncStatus?.run?.created != null && ` — ${autoSyncStatus.run.created} criados`}
          {autoSyncStatus?.run?.errors ? ` — ${autoSyncStatus.run.errors} erros` : ""}
        </div>
      )}

      {!syncingNew && autoSyncStatus?.last_summary && (
        <div className="border border-emerald-100 bg-emerald-50/40 px-4 py-2 text-xs text-emerald-900" data-testid="autosync-summary">
          Última sincronização: <strong>{autoSyncStatus.last_summary.created}</strong> vínculos criados,{" "}
          <strong>{autoSyncStatus.last_summary.errors}</strong> erros,{" "}
          <strong>{autoSyncStatus.last_summary.skipped_no_ref}</strong> sem referência
          {" "}(total {autoSyncStatus.last_summary.total_pairs} pares).
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
              <th className="px-3 py-2 text-center">Status</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr><td colSpan={5} className="px-3 py-12 text-center text-muted-foreground text-sm">
                {previews.length === 0
                  ? 'Nenhum vínculo ainda. Clique em "Sincronizar Novas" para começar.'
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
                  {p.applied ? (
                    <span
                      data-testid="status-applied"
                      className="inline-flex items-center gap-1 text-xs px-2 py-1 bg-emerald-50 border border-emerald-300 text-emerald-800"
                    >
                      <CheckCircle2 className="h-3 w-3" /> Aplicado no Bling
                    </span>
                  ) : !p.suggestion_id ? (
                    <span
                      data-testid="status-no-suggestion"
                      className="inline-flex items-center gap-1 text-xs px-2 py-1 bg-rose-50 border border-rose-200 text-rose-700"
                    >
                      <XCircle className="h-3 w-3" /> Sem sugestão IA
                    </span>
                  ) : p.approved ? (
                    <span
                      data-testid="status-pending-apply"
                      className="inline-flex items-center gap-1 text-xs px-2 py-1 bg-amber-50 border border-amber-200 text-amber-800"
                    >
                      <Loader2 className="h-3 w-3" /> Aguardando aplicar
                    </span>
                  ) : (
                    <button
                      data-testid="btn-approve-manual"
                      onClick={() => approve(p, true)}
                      disabled={!p.suggestion_id}
                      className="inline-flex items-center gap-1 text-xs px-2 py-1 border border-border hover:bg-zinc-50 disabled:opacity-40"
                    >
                      Aprovar manualmente
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
