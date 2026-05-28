import { useEffect, useState, useCallback } from "react";
import { endpoints } from "@/lib/api";
import { logger } from "@/lib/logger";
import { Play, Square, RefreshCw, Bot, AlertTriangle } from "lucide-react";
import { toast } from "sonner";

const LOG_DOT_COLORS = {
  success: "bg-emerald-500",
  error: "bg-rose-500",
  warning: "bg-amber-500",
  info: "bg-zinc-400",
};

const POLL_INTERVAL_MS = 2500;

const STATE_META = {
  idle: { label: "Ocioso", color: "bg-zinc-100 text-zinc-700 border-zinc-300" },
  running: { label: "Em execução", color: "bg-emerald-100 text-emerald-700 border-emerald-400" },
  paused: { label: "Pausado", color: "bg-amber-100 text-amber-700 border-amber-400" },
  error: { label: "Erro", color: "bg-rose-100 text-rose-700 border-rose-400" },
};

export default function RobotPage() {
  const [status, setStatus] = useState(null);
  const [maxProducts, setMaxProducts] = useState(5);
  const [dryRun, setDryRun] = useState(true);
  const [logs, setLogs] = useState([]);

  const tick = useCallback(async () => {
    try {
      const [{ data: s }, { data: l }] = await Promise.all([
        endpoints.robotStatus(),
        endpoints.robotLogs(50),
      ]);
      setStatus(s);
      setLogs(l);
    } catch (err) {
      logger.error("Failed to fetch robot status/logs:", err);
    }
  }, []);

  useEffect(() => {
    tick();
    const timer = setInterval(tick, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [tick]);

  const start = async () => {
    try {
      await endpoints.robotStart(parseInt(maxProducts, 10) || 5, dryRun);
      toast.success("Robô iniciado");
      tick();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Falha ao iniciar");
    }
  };

  const stop = async () => {
    await endpoints.robotStop();
    toast.info("Parada solicitada");
    tick();
  };

  const meta = STATE_META[status?.state] || STATE_META.idle;

  return (
    <div className="space-y-6">
      <div>
        <div className="label-overline mb-1">Automação JohnDrop</div>
        <h1 className="font-display text-3xl font-bold tracking-tighter">Robô de Cadastro</h1>
        <p className="text-sm text-muted-foreground mt-1 max-w-3xl">
          O robô faz login no JohnDrop, abre o catálogo "Publicar Catálogo / Todos que eu não cadastrei",
          limpa o título, busca o preço na tabela e cadastra o produto. Use <strong>Dry-Run</strong> para
          simular sem submeter.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2 border border-border bg-white p-6 space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <div className="label-overline mb-1">Estado atual</div>
              <span
                data-testid="robot-state-badge"
                className={`inline-flex px-3 py-1 text-xs font-semibold border ${meta.color} rounded-sm uppercase`}
              >
                {status?.state === "running" && <span className="h-2 w-2 rounded-full bg-emerald-500 status-pulse mr-2 my-auto" />}
                {meta.label}
              </span>
            </div>
            <Bot className="h-8 w-8 text-zinc-300" strokeWidth={1.5} />
          </div>

          {status?.current_product && (
            <div className="bg-zinc-50 border border-border p-3 text-sm">
              <div className="label-overline mb-1">Processando</div>
              <div className="font-mono truncate" data-testid="current-product">{status.current_product}</div>
            </div>
          )}

          <div className="grid grid-cols-3 border border-border divide-x divide-border">
            <div className="p-4">
              <div className="label-overline">Processados</div>
              <div className="font-display text-2xl font-bold" data-testid="counter-processed">{status?.processed || 0}</div>
            </div>
            <div className="p-4">
              <div className="label-overline">Sucessos</div>
              <div className="font-display text-2xl font-bold text-emerald-600" data-testid="counter-success">{status?.success || 0}</div>
            </div>
            <div className="p-4">
              <div className="label-overline">Falhas</div>
              <div className="font-display text-2xl font-bold text-rose-600" data-testid="counter-failed">{status?.failed || 0}</div>
            </div>
          </div>

          {status?.message && (
            <div className="bg-rose-50 border border-rose-200 text-rose-700 text-sm p-3 flex items-start gap-2">
              <AlertTriangle className="h-4 w-4 mt-0.5" />
              <span>{status.message}</span>
            </div>
          )}
        </div>

        <div className="border border-border bg-white p-6 space-y-4">
          <div className="label-overline">Controles</div>
          <div>
            <label className="text-xs text-muted-foreground">Máx. produtos por execução</label>
            <input
              data-testid="max-products-input"
              type="number"
              min={1}
              max={100}
              value={maxProducts}
              onChange={(e) => setMaxProducts(e.target.value)}
              className="w-full text-sm border border-border rounded-sm px-3 py-2 mt-1 focus:outline-none focus:ring-2 focus:ring-[#EE7B22]"
            />
          </div>
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input
              data-testid="dry-run-toggle"
              type="checkbox"
              checked={dryRun}
              onChange={(e) => setDryRun(e.target.checked)}
              className="h-4 w-4"
            />
            <span>Dry-Run (não submeter)</span>
          </label>

          <div className="flex flex-col gap-2 pt-2">
            <button
              data-testid="start-robot-btn"
              onClick={start}
              disabled={status?.state === "running"}
              className="w-full bg-[#EE7B22] text-white text-sm font-medium px-4 py-2.5 rounded-sm hover:bg-[#C9651A] disabled:opacity-50 inline-flex items-center justify-center gap-2"
            >
              <Play className="h-4 w-4" />
              Iniciar Robô
            </button>
            <button
              data-testid="stop-robot-btn"
              onClick={stop}
              disabled={status?.state !== "running"}
              className="w-full border border-border text-sm font-medium px-4 py-2.5 rounded-sm hover:bg-zinc-50 disabled:opacity-50 inline-flex items-center justify-center gap-2"
            >
              <Square className="h-4 w-4" />
              Parar
            </button>
            <button
              data-testid="refresh-btn"
              onClick={tick}
              className="w-full text-xs text-muted-foreground hover:text-foreground inline-flex items-center justify-center gap-1.5 py-1"
            >
              <RefreshCw className="h-3.5 w-3.5" /> Atualizar
            </button>
          </div>
        </div>
      </div>

      <div className="border border-border bg-white">
        <div className="px-6 py-3 border-b border-border flex items-center justify-between">
          <span className="label-overline">Logs ao vivo (50 últimos)</span>
          <span className="text-xs text-muted-foreground">{logs.length} entradas</span>
        </div>
        <div className="max-h-[420px] overflow-y-auto divide-y divide-border" data-testid="logs-stream">
          {logs.length === 0 ? (
            <div className="px-6 py-12 text-sm text-muted-foreground text-center">
              Nenhum log ainda. Inicie o robô para ver atividade.
            </div>
          ) : (
            logs.map((log) => (
              <div key={log.id} className="px-6 py-3 flex items-start gap-4 text-sm">
                <span
                  className={`mt-1 inline-block h-2 w-2 rounded-full flex-shrink-0 ${LOG_DOT_COLORS[log.level] || LOG_DOT_COLORS.info}`}
                />
                <div className="flex-1 min-w-0">
                  <div className="font-mono text-xs text-muted-foreground">
                    {new Date(log.created_at).toLocaleTimeString("pt-BR")}
                  </div>
                  <div className="truncate">{log.message}</div>
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
