import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { logger } from "@/lib/logger";
import { toast } from "sonner";
import { CalendarClock, Trash2, RefreshCw, Loader2, CheckCircle2, AlertTriangle } from "lucide-react";

const STATUS_LABEL = {
  pending: { label: "Aguardando", color: "bg-amber-100 text-amber-800" },
  publishing: { label: "Publicando", color: "bg-blue-100 text-blue-800" },
  published: { label: "Publicado", color: "bg-emerald-100 text-emerald-800" },
  failed: { label: "Falhou", color: "bg-rose-100 text-rose-800" },
  cancelled: { label: "Cancelado", color: "bg-zinc-200 text-zinc-600" },
};

export default function SchedulePage() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");
  const [schedulerInfo, setSchedulerInfo] = useState(null);

  const load = async () => {
    setLoading(true);
    try {
      const [scheduled, status] = await Promise.all([
        api.get("/social/ad/schedule", { params: { status: filter || undefined, limit: 100 } }),
        api.get("/social/ad/scheduler/status"),
      ]);
      setItems(scheduled.data.items || []);
      setSchedulerInfo(status.data);
    } catch (e) {
      logger.error("load schedule", e);
      toast.error("Falha ao carregar agenda");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [scheduled, status] = await Promise.all([
          api.get("/social/ad/schedule", { params: { status: filter || undefined, limit: 100 } }),
          api.get("/social/ad/scheduler/status"),
        ]);
        if (cancelled) return;
        setItems(scheduled.data.items || []);
        setSchedulerInfo(status.data);
      } catch (e) {
        logger.error("load schedule", e);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [filter]);

  const cancel = async (id) => {
    const confirmed = window.confirm("Cancelar este anúncio agendado?");
    if (!confirmed) return;
    try {
      await api.delete(`/social/ad/schedule/${id}`);
      toast.success("Agendamento cancelado");
      load();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Falha ao cancelar");
    }
  };

  return (
    <div className="space-y-5" data-testid="schedule-page">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-display font-bold flex items-center gap-2">
            <CalendarClock className="h-6 w-6" /> Agenda de Anúncios
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Anúncios programados para publicação automática nos horários de pico.
          </p>
        </div>
        <button
          onClick={load}
          data-testid="reload-schedule-btn"
          className="px-3 py-2 text-sm bg-zinc-100 hover:bg-zinc-200 rounded-sm flex items-center gap-2"
        >
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          Atualizar
        </button>
      </header>

      {schedulerInfo && (
        <div className="rounded-sm border border-emerald-200 bg-emerald-50 p-3 text-sm flex items-center gap-3" data-testid="scheduler-status">
          <CheckCircle2 className="h-4 w-4 text-emerald-700" />
          <div className="flex-1">
            Worker <strong>{schedulerInfo.running ? "ativo" : "parado"}</strong> ·
            picos padrão (horário Brasil): {schedulerInfo.default_peaks_br?.join("h, ")}h ·
            agora: {new Date(schedulerInfo.now_br).toLocaleString("pt-BR")}
          </div>
        </div>
      )}

      <div className="flex items-center gap-2 text-xs">
        <span className="text-muted-foreground">Filtrar:</span>
        {["", "pending", "published", "failed", "cancelled"].map((s) => (
          <button
            key={s || "all"}
            onClick={() => setFilter(s)}
            data-testid={`filter-${s || "all"}`}
            className={`px-2.5 py-1 rounded-sm border transition ${
              filter === s ? "bg-zinc-900 text-white border-zinc-900" : "bg-white border-border hover:bg-zinc-50"
            }`}
          >
            {s ? STATUS_LABEL[s]?.label || s : "Todos"}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="py-16 text-center text-muted-foreground">
          <Loader2 className="h-6 w-6 mx-auto animate-spin mb-2" />
          Carregando…
        </div>
      ) : items.length === 0 ? (
        <div className="rounded-sm border border-border bg-white p-10 text-center text-muted-foreground" data-testid="empty-schedule">
          <AlertTriangle className="h-8 w-8 mx-auto mb-2 text-zinc-400" />
          Nenhum anúncio agendado. Vá em <strong>Criar Anúncio</strong> e clique em &quot;Agendar para próximo pico&quot;.
        </div>
      ) : (
        <div className="rounded-sm border border-border bg-white overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-zinc-50 border-b border-border text-xs uppercase tracking-wide text-muted-foreground">
              <tr>
                <th className="text-left px-4 py-2.5 w-16">Imagem</th>
                <th className="text-left px-4 py-2.5">Anúncio</th>
                <th className="text-left px-4 py-2.5 w-44">Publicação prevista</th>
                <th className="text-left px-4 py-2.5 w-32">Status</th>
                <th className="text-left px-4 py-2.5 w-16">Tentativas</th>
                <th className="w-20"></th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => {
                const status = STATUS_LABEL[it.status] || STATUS_LABEL.pending;
                const dt = it.publish_at_local || it.publish_at_utc;
                return (
                  <tr key={it.id} data-testid={`schedule-row-${it.id}`} className="border-b border-border last:border-0">
                    <td className="px-4 py-2">
                      {it.preview?.image_url ? (
                        <img src={it.preview.image_url} alt="" className="h-10 w-10 object-cover rounded-sm" />
                      ) : (
                        <div className="h-10 w-10 bg-zinc-100 rounded-sm" />
                      )}
                    </td>
                    <td className="px-4 py-2">
                      <div className="font-medium line-clamp-1">{it.preview?.headline || it.preview?.product_name || "—"}</div>
                      <div className="text-xs text-muted-foreground">{it.draft_id}</div>
                    </td>
                    <td className="px-4 py-2 text-xs">
                      {dt ? new Date(dt).toLocaleString("pt-BR") : "—"}
                    </td>
                    <td className="px-4 py-2">
                      <span className={`inline-block px-2 py-0.5 text-xs rounded-sm ${status.color}`}>
                        {status.label}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-xs text-muted-foreground">{it.attempts || 0}</td>
                    <td className="px-4 py-2 text-right">
                      {it.status === "pending" && (
                        <button
                          onClick={() => cancel(it.id)}
                          data-testid={`cancel-${it.id}`}
                          className="text-xs text-rose-600 hover:text-rose-700 inline-flex items-center gap-1"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      )}
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
