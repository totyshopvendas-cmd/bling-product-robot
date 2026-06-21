import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { logger } from "@/lib/logger";
import { toast } from "sonner";
import { Loader2, RefreshCw, RotateCw, Trash2, Zap, Clock, CheckCircle2, AlertTriangle } from "lucide-react";

const STATUS_META = {
  pending:    { label: "Aguardando JohnDrop", color: "bg-amber-100 text-amber-800", Icon: Clock },
  processing: { label: "Enriquecendo",        color: "bg-blue-100 text-blue-800",   Icon: Loader2 },
  done:       { label: "Concluído",            color: "bg-emerald-100 text-emerald-800", Icon: CheckCircle2 },
  giveup:     { label: "Desistiu",            color: "bg-rose-100 text-rose-700",   Icon: AlertTriangle },
};

export default function EnrichQueuePage() {
  const [data, setData] = useState({ items: [], summary: {}, worker: {} });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const fetchData = async () => {
      try {
        const { data } = await api.get("/enrich/queue", { params: { limit: 100 } });
        if (!cancelled) setData(data);
      } catch (e) {
        logger.error("queue load", e);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    fetchData();
    const t = setInterval(fetchData, 8000);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  const reload = () => api.get("/enrich/queue", { params: { limit: 100 } }).then((r) => setData(r.data));

  const tickNow = async () => {
    try {
      await api.post("/enrich/queue/tick-now");
      toast.success("Verificação disparada");
      setTimeout(reload, 2000);
    } catch (e) { toast.error("Erro ao disparar"); }
  };

  const retry = async (sku) => {
    try {
      await api.post(`/enrich/queue/${sku}/retry`);
      toast.success(`${sku} re-enfileirado`);
      reload();
    } catch (e) { toast.error("Erro ao retentar"); }
  };

  const remove = async (sku) => {
    if (!window.confirm(`Remover ${sku} da fila?`)) return;
    try {
      await api.delete(`/enrich/queue/${sku}`);
      toast.success("Removido");
      reload();
    } catch (e) { toast.error("Erro"); }
  };

  if (loading) return <div className="py-16 text-center"><Loader2 className="h-6 w-6 mx-auto animate-spin" /></div>;

  const s = data.summary || {};

  return (
    <div className="space-y-5" data-testid="enrich-queue-page">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-display font-bold flex items-center gap-2">
            <Clock className="h-6 w-6" /> Fila de Enriquecimento
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Produtos cadastrados na JohnDrop aguardando sync para o Bling. O worker verifica a cada {data.worker?.poll_interval_s || 90}s e enriquece automaticamente quando o produto chega completo (com imagens).
          </p>
        </div>
        <div className="flex gap-2">
          <button onClick={tickNow} data-testid="tick-now" className="px-3 py-2 text-sm bg-emerald-600 text-white hover:bg-emerald-700 rounded-sm flex items-center gap-2">
            <Zap className="h-4 w-4" /> Verificar agora
          </button>
          <button onClick={reload} className="px-3 py-2 text-sm bg-zinc-100 hover:bg-zinc-200 rounded-sm flex items-center gap-2">
            <RefreshCw className="h-4 w-4" /> Atualizar
          </button>
        </div>
      </header>

      <div className="grid grid-cols-4 gap-3">
        <Card label="Aguardando" value={s.pending || 0} color="amber" Icon={Clock} />
        <Card label="Enriquecendo" value={s.processing || 0} color="blue" Icon={Loader2} spin />
        <Card label="Concluídos" value={s.done || 0} color="emerald" Icon={CheckCircle2} />
        <Card label="Desistidos" value={s.giveup || 0} color="rose" Icon={AlertTriangle} />
      </div>

      {data.items.length === 0 ? (
        <div className="rounded-sm border border-border bg-white p-10 text-center text-muted-foreground" data-testid="empty-queue">
          Nenhum produto na fila. Cadastre produtos via robô JohnDrop e eles aparecem aqui.
        </div>
      ) : (
        <div className="rounded-sm border border-border bg-white overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-zinc-50 border-b border-border text-xs uppercase tracking-wide text-muted-foreground">
              <tr>
                <th className="text-left px-4 py-2.5 w-32">SKU</th>
                <th className="text-left px-4 py-2.5">Título</th>
                <th className="text-left px-4 py-2.5 w-40">Status</th>
                <th className="text-left px-4 py-2.5 w-20">Tent.</th>
                <th className="text-left px-4 py-2.5 w-36">Última verif.</th>
                <th className="text-left px-4 py-2.5 w-24"></th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((it) => {
                const meta = STATUS_META[it.status] || STATUS_META.pending;
                return (
                  <tr key={it.sku} data-testid={`queue-row-${it.sku}`} className="border-b border-border last:border-0 hover:bg-zinc-50">
                    <td className="px-4 py-2 font-mono text-xs">{it.sku}</td>
                    <td className="px-4 py-2 text-xs text-zinc-700 line-clamp-1">{it.raw_title}</td>
                    <td className="px-4 py-2">
                      <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 text-xs rounded-sm ${meta.color}`}>
                        <meta.Icon className={`h-3.5 w-3.5 ${meta.Icon === Loader2 ? "animate-spin" : ""}`} />
                        {meta.label}
                      </span>
                      {it.last_error && (
                        <div className="text-xs text-rose-600 mt-0.5 line-clamp-1" title={it.last_error}>{it.last_error}</div>
                      )}
                      {it.last_status && it.status === "pending" && !it.last_error && (
                        <div className="text-xs text-zinc-500 mt-0.5">{it.last_status}</div>
                      )}
                    </td>
                    <td className="px-4 py-2 text-xs">{it.attempts || 0}</td>
                    <td className="px-4 py-2 text-xs text-muted-foreground">
                      {it.last_check ? new Date(it.last_check).toLocaleTimeString("pt-BR") : "—"}
                    </td>
                    <td className="px-4 py-2 text-right space-x-1">
                      {(it.status === "giveup" || it.status === "pending") && (
                        <button onClick={() => retry(it.sku)} data-testid={`retry-${it.sku}`} className="text-xs text-blue-600 hover:text-blue-800 inline-flex items-center gap-1">
                          <RotateCw className="h-3 w-3" />
                        </button>
                      )}
                      <button onClick={() => remove(it.sku)} data-testid={`remove-${it.sku}`} className="text-xs text-rose-600 hover:text-rose-800 inline-flex items-center gap-1">
                        <Trash2 className="h-3 w-3" />
                      </button>
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

function Card({ label, value, color, Icon, spin }) {
  const map = { amber: "border-amber-200 bg-amber-50 text-amber-800", blue: "border-blue-200 bg-blue-50 text-blue-800", emerald: "border-emerald-200 bg-emerald-50 text-emerald-800", rose: "border-rose-200 bg-rose-50 text-rose-800" };
  return (
    <div className={`rounded-sm border p-4 ${map[color]}`}>
      <div className="flex items-center justify-between">
        <span className="text-xs uppercase">{label}</span>
        <Icon className={`h-4 w-4 ${spin ? "animate-spin" : ""}`} />
      </div>
      <div className="text-2xl font-bold mt-1">{value}</div>
    </div>
  );
}
