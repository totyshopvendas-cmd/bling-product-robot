import { useEffect, useState } from "react";
import { endpoints } from "@/lib/api";
import { Trash2, RefreshCw } from "lucide-react";
import { toast } from "sonner";

const LEVEL_COLORS = {
  success: "bg-emerald-50 border-emerald-200 text-emerald-800",
  error: "bg-rose-50 border-rose-200 text-rose-800",
  warning: "bg-amber-50 border-amber-200 text-amber-800",
  info: "bg-zinc-50 border-zinc-200 text-zinc-700",
};

export default function LogsPage() {
  const [logs, setLogs] = useState([]);
  const [filter, setFilter] = useState("all");

  const load = async () => {
    const { data } = await endpoints.robotLogs(200);
    setLogs(data);
  };

  useEffect(() => {
    load();
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, []);

  const clearAll = async () => {
    if (!window.confirm("Limpar todos os logs?")) return;
    await endpoints.robotLogsClear();
    toast.success("Logs apagados");
    load();
  };

  const visible = filter === "all" ? logs : logs.filter((l) => l.level === filter);

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <div className="label-overline mb-1">Histórico</div>
          <h1 className="font-display text-3xl font-bold tracking-tighter">Logs do Robô</h1>
        </div>
        <div className="flex items-center gap-2">
          <button
            data-testid="logs-refresh"
            onClick={load}
            className="border border-border text-sm px-3 py-2 rounded-sm hover:bg-zinc-50 inline-flex items-center gap-2"
          >
            <RefreshCw className="h-4 w-4" /> Atualizar
          </button>
          <button
            data-testid="logs-clear"
            onClick={clearAll}
            className="border border-rose-300 text-rose-600 text-sm px-3 py-2 rounded-sm hover:bg-rose-50 inline-flex items-center gap-2"
          >
            <Trash2 className="h-4 w-4" /> Limpar
          </button>
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        {["all", "success", "error", "warning", "info"].map((f) => (
          <button
            key={f}
            data-testid={`filter-${f}`}
            onClick={() => setFilter(f)}
            className={`text-xs px-3 py-1.5 rounded-sm border uppercase font-semibold tracking-wider ${
              filter === f ? "bg-[#002FA7] text-white border-[#002FA7]" : "border-border hover:bg-zinc-50"
            }`}
          >
            {f === "all" ? "Todos" : f}
          </button>
        ))}
      </div>

      <div className="border border-border bg-white divide-y divide-border" data-testid="logs-list">
        {visible.length === 0 ? (
          <div className="px-6 py-16 text-center text-sm text-muted-foreground">
            Nenhum log para este filtro.
          </div>
        ) : (
          visible.map((log) => (
            <div key={log.id} className="px-6 py-4 grid grid-cols-12 gap-4 text-sm">
              <div className="col-span-2 font-mono text-xs text-muted-foreground">
                {new Date(log.created_at).toLocaleString("pt-BR")}
              </div>
              <div className="col-span-1">
                <span className={`text-[10px] px-2 py-0.5 rounded-sm uppercase font-semibold border ${LEVEL_COLORS[log.level] || LEVEL_COLORS.info}`}>
                  {log.level}
                </span>
              </div>
              <div className="col-span-9 min-w-0">
                <div className="font-medium">{log.message}</div>
                {log.cleaned_title && (
                  <div className="font-mono text-xs text-muted-foreground mt-1 truncate">
                    → {log.cleaned_title} {log.sale_price ? `· R$ ${log.sale_price}` : ""}
                  </div>
                )}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
