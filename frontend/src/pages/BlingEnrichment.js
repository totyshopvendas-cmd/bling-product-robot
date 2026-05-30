import { useEffect, useState, useCallback } from "react";
import { api } from "@/lib/api";
import { logger } from "@/lib/logger";
import { Sparkles, RefreshCw, CheckCircle2, AlertTriangle, Clock } from "lucide-react";
import { toast } from "sonner";

const REFRESH_INTERVAL_MS = 5000;

const STATUS_META = {
  success: { color: "bg-emerald-100 text-emerald-800 border-emerald-300", icon: CheckCircle2, label: "Sucesso" },
  error: { color: "bg-rose-100 text-rose-800 border-rose-300", icon: AlertTriangle, label: "Erro" },
  not_found: { color: "bg-amber-100 text-amber-800 border-amber-300", icon: Clock, label: "Aguardando" },
};

export default function BlingEnrichmentPage() {
  const [stats, setStats] = useState(null);
  const [logs, setLogs] = useState([]);
  const [expanded, setExpanded] = useState(null);
  const [manual, setManual] = useState({ sku: "", title: "", description: "" });
  const [running, setRunning] = useState(false);

  const load = useCallback(async () => {
    try {
      const [s, l] = await Promise.all([
        api.get("/bling/enrichment/stats"),
        api.get("/bling/enrichment/logs?limit=100"),
      ]);
      setStats(s.data);
      setLogs(l.data);
    } catch (err) {
      logger.error("Failed to load enrichment data:", err);
    }
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, REFRESH_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [load]);

  const runManual = async () => {
    if (!manual.sku || !manual.title) {
      toast.error("Informe SKU e Título");
      return;
    }
    setRunning(true);
    try {
      const { data } = await api.post("/bling/enrich", {
        sku: manual.sku,
        raw_title: manual.title,
        raw_description: manual.description,
      });
      if (data.ok) {
        toast.success("Enriquecido com sucesso");
      } else {
        toast.error("Falhou: " + (data.reason || "desconhecido"));
      }
      load();
    } catch (err) {
      toast.error("Erro: " + (err?.response?.data?.detail || err.message));
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <div className="label-overline mb-1">Bling ERP</div>
        <h1 className="font-display text-3xl font-bold tracking-tighter">Enriquecimento de Produtos</h1>
        <p className="text-sm text-muted-foreground mt-1 max-w-3xl">
          Após cada cadastro no JohnDrop, o produto aparece automaticamente no Bling. O sistema então
          gera <strong>descrição curta</strong>, <strong>8 bullets técnicos</strong> e seleciona a{" "}
          <strong>categoria</strong> ideal usando IA — sempre removendo marcas e EANs, e respeitando as regras de formatação.
        </p>
      </div>

      {stats && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-0 border border-border bg-white">
          <div className="p-5 border-r border-border">
            <div className="label-overline">Total</div>
            <div className="font-display text-3xl font-bold tracking-tighter">{stats.total}</div>
          </div>
          <div className="p-5 border-r border-border">
            <div className="label-overline">Sucessos</div>
            <div className="font-display text-3xl font-bold tracking-tighter text-emerald-600">{stats.success}</div>
          </div>
          <div className="p-5 border-r border-border">
            <div className="label-overline">Aguardando sync</div>
            <div className="font-display text-3xl font-bold tracking-tighter text-amber-600">{stats.not_found}</div>
          </div>
          <div className="p-5">
            <div className="label-overline">Erros</div>
            <div className="font-display text-3xl font-bold tracking-tighter text-rose-600">{stats.errors}</div>
          </div>
        </div>
      )}

      <div className="border border-border bg-white p-6 space-y-4">
        <div className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-[#EE7B22]" />
          <span className="label-overline">Enriquecer manualmente (Bling)</span>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <input
            data-testid="manual-sku"
            placeholder="SKU (ex: KA-1179)"
            value={manual.sku}
            onChange={(e) => setManual((m) => ({ ...m, sku: e.target.value }))}
            className="text-sm border border-border rounded-sm px-3 py-2 focus:outline-none focus:ring-2 focus:ring-[#EE7B22]"
          />
          <input
            data-testid="manual-title"
            placeholder="Título limpo do produto"
            value={manual.title}
            onChange={(e) => setManual((m) => ({ ...m, title: e.target.value }))}
            className="md:col-span-2 text-sm border border-border rounded-sm px-3 py-2 focus:outline-none focus:ring-2 focus:ring-[#EE7B22]"
          />
        </div>
        <textarea
          data-testid="manual-desc"
          placeholder="Descrição original (opcional)"
          rows={3}
          value={manual.description}
          onChange={(e) => setManual((m) => ({ ...m, description: e.target.value }))}
          className="w-full text-sm border border-border rounded-sm px-3 py-2 focus:outline-none focus:ring-2 focus:ring-[#EE7B22] font-mono"
        />
        <button
          data-testid="run-manual"
          onClick={runManual}
          disabled={running}
          className="bg-[#EE7B22] text-white text-sm font-medium px-5 py-2.5 rounded-sm hover:bg-[#C9651A] disabled:opacity-50 inline-flex items-center gap-2"
        >
          <Sparkles className="h-4 w-4" />
          {running ? "Enriquecendo…" : "Enriquecer no Bling"}
        </button>
      </div>

      <div className="border border-border bg-white">
        <div className="px-6 py-3 border-b border-border flex items-center justify-between">
          <span className="label-overline">Histórico ({logs.length})</span>
          <button
            data-testid="refresh-enrichment"
            onClick={load}
            className="text-xs text-zinc-600 hover:text-[#EE7B22] inline-flex items-center gap-1.5"
          >
            <RefreshCw className="h-3.5 w-3.5" /> Atualizar
          </button>
        </div>
        <div className="divide-y divide-border" data-testid="enrichment-logs">
          {logs.length === 0 ? (
            <div className="px-6 py-12 text-sm text-muted-foreground text-center">
              Nenhum enriquecimento ainda. Rode o robô em modo REAL — após cada cadastro no JohnDrop, o produto será enriquecido aqui.
            </div>
          ) : (
            logs.map((l) => {
              const meta = STATUS_META[l.status] || STATUS_META.error;
              const Icon = meta.icon;
              const isOpen = expanded === l.id;
              return (
                <div key={l.id} className="px-6 py-4 text-sm">
                  <button
                    className="w-full text-left flex items-start justify-between gap-4"
                    onClick={() => setExpanded(isOpen ? null : l.id)}
                  >
                    <div className="flex items-start gap-3 min-w-0 flex-1">
                      <span className={`mt-0.5 inline-flex items-center gap-1.5 px-2 py-0.5 text-xs font-semibold border rounded-sm uppercase ${meta.color}`}>
                        <Icon className="h-3 w-3" /> {meta.label}
                      </span>
                      <div className="min-w-0">
                        <div className="font-mono text-sm font-semibold">{l.sku}</div>
                        <div className="text-xs text-muted-foreground truncate">{l.message}</div>
                      </div>
                    </div>
                    <div className="font-mono text-xs text-muted-foreground flex-shrink-0">
                      {new Date(l.created_at).toLocaleString("pt-BR")}
                    </div>
                  </button>

                  {isOpen && l.status === "success" && (
                    <div className="mt-4 ml-20 space-y-4 text-xs">
                      <div>
                        <div className="label-overline mb-1">Descrição Curta</div>
                        <div className="bg-zinc-50 border border-border p-3 rounded-sm font-mono whitespace-pre-wrap"
                             dangerouslySetInnerHTML={{ __html: l.short_description || "" }} />
                      </div>
                      <div>
                        <div className="label-overline mb-1">8 Bullets</div>
                        <div className="bg-zinc-50 border border-border p-3 rounded-sm space-y-1 font-mono">
                          {(l.bullets || []).map((b, i) => (
                            <div key={i} dangerouslySetInnerHTML={{ __html: b }} />
                          ))}
                        </div>
                      </div>
                      <div className="flex gap-4">
                        <div>
                          <span className="label-overline">Bling product ID:</span>{" "}
                          <span className="font-mono">{l.product_id}</span>
                        </div>
                        <div>
                          <span className="label-overline">Categoria ID:</span>{" "}
                          <span className="font-mono">{l.category_id || "—"}</span>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}
