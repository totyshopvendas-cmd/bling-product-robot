import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { logger } from "@/lib/logger";
import { toast } from "sonner";
import {
  Activity, Loader2, RefreshCw, Clock, Sparkles, CheckCircle2, XCircle, Trash2,
} from "lucide-react";

const STAGE_META = {
  queued:        { Icon: Clock,         label: "Na fila",        color: "text-zinc-600",    bg: "bg-zinc-100" },
  waiting_sync:  { Icon: Clock,         label: "Aguardando sync", color: "text-amber-700",   bg: "bg-amber-100" },
  enriching:     { Icon: Sparkles,      label: "Enriquecendo",   color: "text-blue-700",    bg: "bg-blue-100" },
  done:          { Icon: CheckCircle2,  label: "Concluído",      color: "text-emerald-700", bg: "bg-emerald-100" },
  failed:        { Icon: XCircle,       label: "Falhou",         color: "text-rose-700",    bg: "bg-rose-100" },
};

const POLL_INTERVAL_MS = 3000;

export default function EnrichmentProgressPage() {
  const [data, setData] = useState({ items: [], summary: {} });
  const [autoRefresh, setAutoRefresh] = useState(true);

  const load = async () => {
    try {
      const { data } = await api.get("/enrich/progress", { params: { limit: 50 } });
      setData(data);
    } catch (e) {
      logger.error("progress", e);
    }
  };

  useEffect(() => {
    let cancelled = false;
    const fetchOnce = async () => {
      try {
        const { data } = await api.get("/enrich/progress", { params: { limit: 50 } });
        if (!cancelled) setData(data);
      } catch (e) {
        logger.error("progress", e);
      }
    };
    fetchOnce();
    if (!autoRefresh) return undefined;
    const t = setInterval(fetchOnce, POLL_INTERVAL_MS);
    return () => { cancelled = true; clearInterval(t); };
  }, [autoRefresh]);

  const clear = async () => {
    const ok = window.confirm("Limpar todo o histórico de progresso?");
    if (!ok) return;
    try {
      await api.delete("/enrich/progress");
      toast.success("Histórico limpo");
      load();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Erro ao limpar");
    }
  };

  const s = data.summary || {};

  return (
    <div className="space-y-5" data-testid="enrichment-progress-page">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-display font-bold flex items-center gap-2">
            <Activity className="h-6 w-6" /> Progresso do Enriquecimento
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Acompanha em tempo real cada SKU sendo processado pelo robô e pelo enriquecimento em lote.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1.5 text-xs cursor-pointer">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
              data-testid="auto-refresh-toggle"
            />
            Auto-atualizar (3s)
          </label>
          <button
            onClick={load}
            data-testid="manual-reload"
            className="px-3 py-2 text-sm bg-zinc-100 hover:bg-zinc-200 rounded-sm flex items-center gap-2"
          >
            <RefreshCw className={`h-4 w-4 ${autoRefresh ? "animate-spin" : ""}`} />
            Atualizar
          </button>
          <button
            onClick={clear}
            data-testid="clear-history"
            className="px-3 py-2 text-sm border border-rose-300 text-rose-700 hover:bg-rose-50 rounded-sm flex items-center gap-2"
          >
            <Trash2 className="h-4 w-4" /> Limpar histórico
          </button>
        </div>
      </header>

      {/* Summary cards */}
      <div className="grid grid-cols-4 gap-3">
        <Card label="Total" value={s.total || 0} color="zinc" Icon={Activity} />
        <Card label="Ativos" value={s.active || 0} color="blue" Icon={Loader2} spinning={(s.active || 0) > 0} />
        <Card label="Concluídos" value={s.done || 0} color="emerald" Icon={CheckCircle2} />
        <Card label="Falhas" value={s.failed || 0} color="rose" Icon={XCircle} />
      </div>

      {/* Items table */}
      {data.items.length === 0 ? (
        <div className="rounded-sm border border-border bg-white p-10 text-center text-muted-foreground" data-testid="empty-progress">
          Nenhum enriquecimento ativo. Quando o robô JohnDrop cadastrar um produto, ou você rodar &quot;Enriquecer em Lote&quot;, aparece aqui em tempo real.
        </div>
      ) : (
        <div className="rounded-sm border border-border bg-white overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-zinc-50 border-b border-border text-xs uppercase tracking-wide text-muted-foreground">
              <tr>
                <th className="text-left px-4 py-2.5 w-32">SKU</th>
                <th className="text-left px-4 py-2.5">Produto</th>
                <th className="text-left px-4 py-2.5 w-44">Estágio</th>
                <th className="text-left px-4 py-2.5 w-32">Detalhe</th>
                <th className="text-left px-4 py-2.5 w-36">Atualizado</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((it) => {
                const meta = STAGE_META[it.stage] || STAGE_META.queued;
                const dt = it.updated_at;
                return (
                  <tr key={it.sku} data-testid={`progress-row-${it.sku}`} className="border-b border-border last:border-0 hover:bg-zinc-50">
                    <td className="px-4 py-2 font-mono text-xs">{it.sku}</td>
                    <td className="px-4 py-2">
                      <div className="text-xs text-zinc-700 line-clamp-1">{it.title || "—"}</div>
                      {it.product_id && (
                        <div className="text-xs text-muted-foreground font-mono">pid: {it.product_id}</div>
                      )}
                    </td>
                    <td className="px-4 py-2">
                      <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 text-xs rounded-sm ${meta.bg} ${meta.color}`}>
                        <meta.Icon className={`h-3.5 w-3.5 ${it.stage === "enriching" || it.stage === "waiting_sync" ? "animate-pulse" : ""}`} />
                        {meta.label}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-xs">
                      {it.stage === "enriching" && (
                        <span className="text-zinc-600">
                          estoque={it.saldo ?? 0} · imgs={it.imagens ?? 0}
                        </span>
                      )}
                      {it.stage === "done" && (
                        <span className="text-emerald-700">
                          {it.variations_created
                            ? `${it.variations_created} variações`
                            : "OK"}
                        </span>
                      )}
                      {it.stage === "failed" && (
                        <span className="text-rose-600 truncate block max-w-[200px]" title={it.error}>{it.error || "—"}</span>
                      )}
                    </td>
                    <td className="px-4 py-2 text-xs text-muted-foreground">
                      {dt ? new Date(dt).toLocaleTimeString("pt-BR") : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}


function Card({ label, value, color, Icon, spinning = false }) {
  const colors = {
    zinc: "border-border bg-white",
    blue: "border-blue-200 bg-blue-50",
    emerald: "border-emerald-200 bg-emerald-50",
    rose: "border-rose-200 bg-rose-50",
  };
  const iconColors = {
    zinc: "text-zinc-500",
    blue: "text-blue-600",
    emerald: "text-emerald-600",
    rose: "text-rose-600",
  };
  return (
    <div className={`rounded-sm border p-4 ${colors[color]}`} data-testid={`card-${label.toLowerCase()}`}>
      <div className="flex items-center justify-between">
        <span className="text-xs uppercase text-zinc-600">{label}</span>
        <Icon className={`h-4 w-4 ${iconColors[color]} ${spinning ? "animate-spin" : ""}`} />
      </div>
      <div className="text-2xl font-bold mt-1">{value}</div>
    </div>
  );
}
