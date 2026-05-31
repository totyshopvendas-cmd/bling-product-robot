import { useEffect, useState, useCallback, useRef } from "react";
import { api } from "@/lib/api";
import { logger } from "@/lib/logger";
import {
  Layers, RefreshCw, Sparkles, Square, Search, CheckCircle2,
  AlertTriangle, Loader2, ChevronLeft, ChevronRight,
} from "lucide-react";
import { toast } from "sonner";

const PAGE_SIZE = 50;
const JOB_POLL_MS = 2500;

const FILTROS = [
  { value: "not_enriched", label: "Não enriquecidos" },
  { value: "enriched", label: "Já enriquecidos" },
  { value: "all", label: "Todos" },
];

export default function BlingBulkEnrichPage() {
  const [filtro, setFiltro] = useState("not_enriched");
  const [busca, setBusca] = useState("");
  const [buscaInput, setBuscaInput] = useState("");
  const [pagina, setPagina] = useState(1);
  const [items, setItems] = useState([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState(new Set());
  const [job, setJob] = useState(null);
  const [enrichAllOpen, setEnrichAllOpen] = useState(false);
  const [maxItems, setMaxItems] = useState(200);
  const pollRef = useRef(null);

  const loadProducts = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/bling/products-with-status", {
        params: { pagina, limite: PAGE_SIZE, filtro, busca },
      });
      setItems(data.items || []);
      setHasMore(Boolean(data.has_more));
      setSelected(new Set());
    } catch (err) {
      logger.error("list bling products:", err);
      toast.error("Falha ao listar produtos do Bling");
    } finally {
      setLoading(false);
    }
  }, [pagina, filtro, busca]);

  useEffect(() => { loadProducts(); }, [loadProducts]);

  const fetchJob = useCallback(async () => {
    try {
      const { data } = await api.get("/bling/bulk-job");
      setJob(data);
      return data;
    } catch (err) {
      logger.error("job poll:", err);
      return null;
    }
  }, []);

  useEffect(() => {
    fetchJob();
    pollRef.current = setInterval(fetchJob, JOB_POLL_MS);
    return () => clearInterval(pollRef.current);
  }, [fetchJob]);

  const toggleOne = (id) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const toggleAllPage = () => {
    setSelected((prev) => {
      const allIds = items.map((i) => i.id);
      const allSelected = allIds.every((id) => prev.has(id));
      const next = new Set(prev);
      if (allSelected) allIds.forEach((id) => next.delete(id));
      else allIds.forEach((id) => next.add(id));
      return next;
    });
  };

  const startSelected = async () => {
    const ids = Array.from(selected);
    if (ids.length === 0) {
      toast.error("Selecione ao menos 1 produto");
      return;
    }
    try {
      const { data } = await api.post("/bling/enrich-bulk", { product_ids: ids });
      toast.success(`Lote iniciado: ${data.total} produtos`);
      fetchJob();
    } catch (err) {
      toast.error(err?.response?.data?.detail || err.message);
    }
  };

  const startEnrichAll = async () => {
    try {
      const { data } = await api.post("/bling/enrich-bulk", {
        enrich_all_not_enriched: true,
        max_items: maxItems,
      });
      toast.success(`Varredura concluída: ${data.total} produtos na fila`);
      setEnrichAllOpen(false);
      fetchJob();
    } catch (err) {
      toast.error(err?.response?.data?.detail || err.message);
    }
  };

  const stopJob = async () => {
    try {
      await api.post("/bling/bulk-job/stop");
      toast.message("Sinal de parada enviado");
      fetchJob();
    } catch (err) {
      toast.error(err.message);
    }
  };

  const running = job?.state === "running";
  const pct = job && job.total > 0 ? Math.round((job.completed / job.total) * 100) : 0;

  return (
    <div className="space-y-6">
      <div>
        <div className="label-overline mb-1">Bling ERP</div>
        <h1 className="font-display text-3xl font-bold tracking-tighter">
          Enriquecimento em Lote
        </h1>
        <p className="text-sm text-muted-foreground mt-1 max-w-3xl">
          Lista todos os produtos do Bling e marca quais ainda <strong>não foram enriquecidos</strong>
          {" "}(sem descrição curta + complementar + marca Generica). Selecione um lote ou rode
          todos automaticamente.
        </p>
      </div>

      {/* Job progress */}
      {job && job.state !== "idle" && (
        <div
          data-testid="bulk-job-card"
          className={`border ${running ? "border-[#EE7B22]" : "border-border"} bg-white p-5`}
        >
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              {running ? (
                <Loader2 className="h-4 w-4 text-[#EE7B22] animate-spin" />
              ) : job.state === "done" ? (
                <CheckCircle2 className="h-4 w-4 text-emerald-600" />
              ) : (
                <AlertTriangle className="h-4 w-4 text-amber-600" />
              )}
              <span className="label-overline">
                Job {job.state}{" "}— {job.completed}/{job.total} ({pct}%)
              </span>
            </div>
            {running && (
              <button
                data-testid="stop-bulk-job"
                onClick={stopJob}
                className="text-xs inline-flex items-center gap-1.5 border border-border px-3 py-1.5 hover:bg-zinc-50"
              >
                <Square className="h-3.5 w-3.5" /> Parar
              </button>
            )}
          </div>
          <div className="w-full h-2 bg-zinc-100 rounded-sm overflow-hidden mb-3">
            <div
              className="h-full bg-[#EE7B22] transition-all"
              style={{ width: `${pct}%` }}
            />
          </div>
          <div className="flex flex-wrap gap-4 text-xs text-zinc-700">
            <span><strong className="text-emerald-700">{job.success}</strong> sucesso</span>
            <span><strong className="text-rose-700">{job.errors}</strong> erros</span>
            <span><strong className="text-zinc-500">{job.skipped}</strong> pulados</span>
            {running && job.current_sku && (
              <span className="text-[#EE7B22] font-mono">→ {job.current_sku}</span>
            )}
          </div>
        </div>
      )}

      {/* Filters */}
      <div className="border border-border bg-white p-4 flex flex-wrap items-end gap-3">
        <div className="flex flex-col gap-1">
          <span className="label-overline">Filtro</span>
          <div className="flex gap-1">
            {FILTROS.map((f) => (
              <button
                key={f.value}
                data-testid={`filter-${f.value}`}
                onClick={() => { setFiltro(f.value); setPagina(1); }}
                className={`text-xs px-3 py-1.5 border ${
                  filtro === f.value
                    ? "bg-[#EE7B22] text-white border-[#EE7B22]"
                    : "border-border bg-white hover:bg-zinc-50"
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>

        <div className="flex flex-col gap-1 flex-1 min-w-[200px]">
          <span className="label-overline">Buscar</span>
          <div className="flex gap-2">
            <input
              data-testid="bulk-search"
              value={buscaInput}
              onChange={(e) => setBuscaInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") { setBusca(buscaInput); setPagina(1); }
              }}
              placeholder="Nome ou SKU"
              className="text-sm border border-border rounded-sm px-3 py-2 flex-1 focus:outline-none focus:ring-2 focus:ring-[#EE7B22]"
            />
            <button
              data-testid="bulk-search-btn"
              onClick={() => { setBusca(buscaInput); setPagina(1); }}
              className="text-xs inline-flex items-center gap-1.5 border border-border px-3 hover:bg-zinc-50"
            >
              <Search className="h-3.5 w-3.5" /> Buscar
            </button>
          </div>
        </div>

        <button
          data-testid="bulk-refresh"
          onClick={loadProducts}
          className="text-xs inline-flex items-center gap-1.5 border border-border px-3 py-2 hover:bg-zinc-50"
        >
          <RefreshCw className="h-3.5 w-3.5" /> Atualizar
        </button>
      </div>

      {/* Actions */}
      <div className="flex flex-wrap items-center gap-3">
        <button
          data-testid="enrich-selected"
          onClick={startSelected}
          disabled={running || selected.size === 0}
          className="bg-[#EE7B22] text-white text-sm font-medium px-4 py-2 rounded-sm hover:bg-[#C9651A] disabled:opacity-40 inline-flex items-center gap-2"
        >
          <Sparkles className="h-4 w-4" />
          Enriquecer selecionados ({selected.size})
        </button>

        <button
          data-testid="enrich-all-open"
          onClick={() => setEnrichAllOpen(true)}
          disabled={running}
          className="bg-zinc-900 text-white text-sm font-medium px-4 py-2 rounded-sm hover:bg-zinc-700 disabled:opacity-40 inline-flex items-center gap-2"
        >
          <Layers className="h-4 w-4" />
          Enriquecer todos não enriquecidos
        </button>

        <span className="text-xs text-muted-foreground">
          {items.length} produtos nesta página
          {selected.size > 0 ? ` · ${selected.size} selecionados` : ""}
        </span>
      </div>

      {/* Table */}
      <div className="border border-border bg-white">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-zinc-50 border-b border-border text-zinc-600">
              <tr>
                <th className="px-3 py-2 text-left w-10">
                  <input
                    data-testid="select-all-page"
                    type="checkbox"
                    checked={items.length > 0 && items.every((i) => selected.has(i.id))}
                    onChange={toggleAllPage}
                  />
                </th>
                <th className="px-3 py-2 text-left">SKU</th>
                <th className="px-3 py-2 text-left">Nome</th>
                <th className="px-3 py-2 text-left">Marca</th>
                <th className="px-3 py-2 text-left">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border" data-testid="bulk-products-table">
              {loading ? (
                <tr><td colSpan={5} className="px-3 py-8 text-center text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin inline mr-2" /> Carregando…
                </td></tr>
              ) : items.length === 0 ? (
                <tr><td colSpan={5} className="px-3 py-8 text-center text-muted-foreground">
                  Nenhum produto encontrado neste filtro.
                </td></tr>
              ) : (
                items.map((p) => (
                  <tr key={p.id} className={selected.has(p.id) ? "bg-orange-50" : ""}>
                    <td className="px-3 py-2">
                      <input
                        data-testid={`select-${p.id}`}
                        type="checkbox"
                        checked={selected.has(p.id)}
                        onChange={() => toggleOne(p.id)}
                      />
                    </td>
                    <td className="px-3 py-2 font-mono text-xs">{p.codigo || "—"}</td>
                    <td className="px-3 py-2 truncate max-w-[400px]" title={p.nome}>{p.nome}</td>
                    <td className="px-3 py-2 text-xs">{p.marca || "—"}</td>
                    <td className="px-3 py-2">
                      {p.enriched ? (
                        <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 border border-emerald-300 bg-emerald-50 text-emerald-800">
                          <CheckCircle2 className="h-3 w-3" /> Enriquecido
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 border border-amber-300 bg-amber-50 text-amber-800">
                          <AlertTriangle className="h-3 w-3" /> Pendente
                        </span>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        <div className="px-4 py-3 border-t border-border flex items-center justify-between">
          <button
            data-testid="prev-page"
            onClick={() => setPagina((p) => Math.max(1, p - 1))}
            disabled={pagina === 1 || loading}
            className="text-xs inline-flex items-center gap-1.5 border border-border px-3 py-1.5 hover:bg-zinc-50 disabled:opacity-40"
          >
            <ChevronLeft className="h-3.5 w-3.5" /> Anterior
          </button>
          <span className="text-xs text-muted-foreground">Página {pagina}</span>
          <button
            data-testid="next-page"
            onClick={() => setPagina((p) => p + 1)}
            disabled={!hasMore || loading}
            className="text-xs inline-flex items-center gap-1.5 border border-border px-3 py-1.5 hover:bg-zinc-50 disabled:opacity-40"
          >
            Próxima <ChevronRight className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {/* Modal: enrich all */}
      {enrichAllOpen && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white border border-border rounded-sm max-w-md w-full p-6 space-y-4">
            <h2 className="font-display text-xl font-bold">Enriquecer TODOS não enriquecidos</h2>
            <p className="text-sm text-zinc-600">
              O sistema vai varrer seu catálogo Bling, encontrar os produtos pendentes e
              colocá-los na fila. Cada produto consome ~3 chamadas à IA (descrição curta,
              8 bullets, categoria).
            </p>
            <div className="flex flex-col gap-1">
              <span className="label-overline">Limite máximo de produtos</span>
              <input
                data-testid="bulk-max-items"
                type="number"
                min={1}
                max={1000}
                value={maxItems}
                onChange={(e) => setMaxItems(Number(e.target.value) || 200)}
                className="text-sm border border-border rounded-sm px-3 py-2 focus:outline-none focus:ring-2 focus:ring-[#EE7B22]"
              />
              <span className="text-xs text-muted-foreground">
                Recomendado: comece com 50 e aumente conforme necessário.
              </span>
            </div>
            <div className="flex gap-2 justify-end">
              <button
                onClick={() => setEnrichAllOpen(false)}
                className="text-sm px-4 py-2 border border-border hover:bg-zinc-50"
              >
                Cancelar
              </button>
              <button
                data-testid="confirm-enrich-all"
                onClick={startEnrichAll}
                className="text-sm px-4 py-2 bg-[#EE7B22] text-white hover:bg-[#C9651A]"
              >
                Iniciar
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
