import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import {
  RefreshCw, Play, Loader2, CheckCircle2, AlertTriangle,
  XCircle, Package, Tag,
} from "lucide-react";
import { api } from "@/lib/api";
import { logger } from "@/lib/logger";

const POLL_MS = 4000;

export default function StockSyncPage() {
  const [status, setStatus] = useState(null);
  const [running, setRunning] = useState(false);
  const [starting, setStarting] = useState(false);
  const [filter, setFilter] = useState("all"); // all | updated | not_found | errors
  const pollRef = useRef(null);

  const loadStatus = useCallback(async () => {
    try {
      const { data } = await api.get("/stock-sync/status");
      setStatus(data);
      setRunning(Boolean(data?.running));
    } catch (err) {
      logger.error("stock-sync status:", err);
    }
  }, []);

  useEffect(() => {
    loadStatus();
    pollRef.current = setInterval(loadStatus, POLL_MS);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [loadStatus]);

  const runSync = async () => {
    if (running) return;
    setStarting(true);
    try {
      const { data } = await api.post("/stock-sync/run");
      if (data?.ok) {
        toast.success("Sincronização iniciada — pode levar alguns minutos");
        setRunning(true);
        await loadStatus();
      } else {
        toast.warning(data?.message || "Sincronização já em execução");
      }
    } catch (err) {
      logger.error("stock-sync run:", err);
      toast.error("Falha ao iniciar sincronização");
    } finally {
      setStarting(false);
    }
  };

  const lastRun = status?.last_run;
  const liveSummary = status?.in_memory_summary;
  const report = lastRun?.reports || liveSummary?.reports || [];

  const counts = {
    updated: report.filter((r) => r.stock_applied || r.price_applied).length,
    not_found: report.filter((r) => r.error === "not_in_bling").length,
    errors: report.filter((r) => r.error && r.error !== "not_in_bling").length,
    total: report.length,
  };

  const filtered = report.filter((r) => {
    if (filter === "all") return true;
    if (filter === "updated") return r.stock_applied || r.price_applied;
    if (filter === "not_found") return r.error === "not_in_bling";
    if (filter === "errors") return r.error && r.error !== "not_in_bling";
    return true;
  });

  return (
    <div className="space-y-6" data-testid="stock-sync-page">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-display font-semibold tracking-tight">
            Sincronização de Estoque
          </h1>
          <p className="text-sm text-muted-foreground mt-1 max-w-3xl">
            O robô faz login na JohnDrop, varre <strong>Meus Produtos</strong> + os{" "}
            <strong>Alertas</strong> do sino e atualiza estoque e preço no Bling.
            Variações são distribuídas conforme a descrição (cores esgotadas ficam
            em 0; com número específico recebem aquele valor; o restante é dividido
            igualmente).
          </p>
        </div>
        <button
          data-testid="run-stock-sync"
          onClick={runSync}
          disabled={running || starting}
          className="inline-flex items-center gap-2 bg-[#EE7B22] hover:bg-[#d96d1c] text-white px-4 py-2 rounded-sm text-sm font-medium disabled:opacity-50 transition-colors"
        >
          {running ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              Sincronizando...
            </>
          ) : starting ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" /> Iniciando...
            </>
          ) : (
            <>
              <Play className="h-4 w-4" /> Rodar agora
            </>
          )}
        </button>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Card
          testid="sync-card-total"
          icon={<Package className="h-4 w-4" />}
          label="SKUs processados"
          value={counts.total}
        />
        <Card
          testid="sync-card-updated"
          icon={<CheckCircle2 className="h-4 w-4 text-emerald-600" />}
          label="Atualizados"
          value={counts.updated}
          tone="emerald"
        />
        <Card
          testid="sync-card-notfound"
          icon={<AlertTriangle className="h-4 w-4 text-zinc-500" />}
          label="Sem cadastro no Bling"
          value={counts.not_found}
          tone="zinc"
        />
        <Card
          testid="sync-card-errors"
          icon={<XCircle className="h-4 w-4 text-rose-600" />}
          label="Erros"
          value={counts.errors}
          tone="rose"
        />
      </div>

      {/* Live run banner */}
      {running && (
        <div
          data-testid="sync-running-banner"
          className="border border-orange-200 bg-orange-50 px-4 py-3 text-sm text-orange-900 flex items-center gap-3"
        >
          <Loader2 className="h-4 w-4 animate-spin" />
          <span>
            Sincronização em andamento desde{" "}
            <strong>{(status?.started_at || "").slice(11, 19)}</strong> — pode demorar
            até 10-15 min para varrer todo o catálogo.
          </span>
        </div>
      )}

      {/* Filter tabs */}
      <div className="flex items-center gap-2 text-sm">
        {[
          { id: "all", label: "Todos" },
          { id: "updated", label: "Atualizados" },
          { id: "not_found", label: "Sem Bling" },
          { id: "errors", label: "Erros" },
        ].map((f) => (
          <button
            key={f.id}
            data-testid={`stock-filter-${f.id}`}
            onClick={() => setFilter(f.id)}
            className={`px-3 py-1.5 rounded-sm transition-colors ${
              filter === f.id
                ? "bg-zinc-900 text-white"
                : "bg-white border border-border text-zinc-700 hover:bg-zinc-50"
            }`}
          >
            {f.label}
          </button>
        ))}
        <button
          data-testid="refresh-status"
          onClick={loadStatus}
          className="ml-auto inline-flex items-center gap-1 text-xs px-3 py-1.5 border border-border hover:bg-zinc-50"
        >
          <RefreshCw className="h-3 w-3" /> Atualizar
        </button>
      </div>

      {/* Table */}
      <div className="border border-border bg-white">
        <div className="overflow-x-auto">
          <table
            data-testid="stock-sync-table"
            className="w-full text-sm"
          >
            <thead className="bg-zinc-50 text-xs text-zinc-600">
              <tr>
                <th className="px-3 py-2 text-left">SKU</th>
                <th className="px-3 py-2 text-left">Fonte</th>
                <th className="px-3 py-2 text-right">Novo Estoque</th>
                <th className="px-3 py-2 text-right">Novo Preço</th>
                <th className="px-3 py-2 text-center">Formato</th>
                <th className="px-3 py-2 text-left">Resultado</th>
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 ? (
                <tr>
                  <td
                    colSpan={6}
                    className="px-3 py-12 text-center text-muted-foreground text-sm"
                  >
                    {report.length === 0
                      ? 'Nenhum sync executado ainda. Clique em "Rodar agora".'
                      : "Nenhum item neste filtro."}
                  </td>
                </tr>
              ) : (
                filtered.map((r) => (
                  <tr key={r.sku} className="border-t border-border">
                    <td className="px-3 py-2 font-mono text-xs">{r.sku}</td>
                    <td className="px-3 py-2 text-xs">
                      <span className={`inline-block px-2 py-0.5 rounded-sm ${
                        r.source === "alert"
                          ? "bg-amber-50 text-amber-800 border border-amber-200"
                          : "bg-zinc-50 text-zinc-700 border border-zinc-200"
                      }`}>
                        {r.source}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-right font-mono">{r.stock ?? "—"}</td>
                    <td className="px-3 py-2 text-right font-mono">
                      {r.price ? `R$ ${Number(r.price).toFixed(2).replace(".", ",")}` : "—"}
                    </td>
                    <td className="px-3 py-2 text-center text-xs">
                      {r.formato === "V" ? (
                        <span className="px-2 py-0.5 bg-purple-50 text-purple-800 border border-purple-200">
                          Variações
                        </span>
                      ) : r.formato === "S" ? (
                        <span className="px-2 py-0.5 bg-zinc-50 border border-zinc-200">
                          Simples
                        </span>
                      ) : "—"}
                    </td>
                    <td className="px-3 py-2">
                      {r.error === "not_in_bling" ? (
                        <span className="inline-flex items-center gap-1 text-xs text-zinc-600">
                          <AlertTriangle className="h-3 w-3" /> Sem cadastro
                        </span>
                      ) : r.error ? (
                        <span className="inline-flex items-center gap-1 text-xs text-rose-700">
                          <XCircle className="h-3 w-3" /> {r.error}
                        </span>
                      ) : (
                        <span className="inline-flex flex-wrap items-center gap-1 text-xs">
                          {r.stock_applied && (
                            <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-emerald-50 text-emerald-800 border border-emerald-200">
                              <Package className="h-3 w-3" /> estoque ok
                            </span>
                          )}
                          {r.price_applied && (
                            <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-blue-50 text-blue-800 border border-blue-200">
                              <Tag className="h-3 w-3" /> preço ok
                            </span>
                          )}
                          {!r.stock_applied && !r.price_applied && r.found_in_bling && (
                            <span className="text-xs text-zinc-500">sem mudança</span>
                          )}
                          {r.distribution && Object.keys(r.distribution).length > 0 && (
                            <span
                              className="text-xs text-zinc-500"
                              title={Object.entries(r.distribution)
                                .map(([k, v]) => `${k}: ${v}`)
                                .join(" • ")}
                            >
                              ({Object.keys(r.distribution).length} variações)
                            </span>
                          )}
                        </span>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Footer info */}
      {lastRun?.finished_at && (
        <div className="text-xs text-muted-foreground" data-testid="last-run-info">
          Última execução concluída em{" "}
          <strong>{lastRun.finished_at.slice(0, 19).replace("T", " ")}</strong>
          {" • "}
          Catálogo: {lastRun.catalog_count ?? liveSummary?.catalog_count ?? "—"} •
          Alertas: {lastRun.alerts_count ?? liveSummary?.alerts_count ?? "—"}
        </div>
      )}
    </div>
  );
}

function Card({ testid, icon, label, value, tone = "default" }) {
  const toneClass = {
    default: "border-border",
    emerald: "border-emerald-200",
    zinc: "border-zinc-200",
    rose: "border-rose-200",
  }[tone];
  return (
    <div
      data-testid={testid}
      className={`border bg-white px-4 py-3 ${toneClass}`}
    >
      <div className="flex items-center gap-2 text-xs text-zinc-500">
        {icon}
        <span>{label}</span>
      </div>
      <div className="text-2xl font-mono font-semibold mt-1">{value}</div>
    </div>
  );
}
