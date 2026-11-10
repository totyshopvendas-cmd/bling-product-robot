import { useEffect, useState, useCallback, useRef } from "react";
import { api, endpoints } from "@/lib/api";
import { logger } from "@/lib/logger";
import {
  Loader2, Play, Square, RefreshCw, Pause, AlertTriangle, CheckCircle2,
  Trash2, Save, Clock, List, ChevronLeft, ChevronRight,
} from "lucide-react";
import { toast } from "sonner";

const JOB_POLL_MS = 2500;
const PAGE_SIZE = 50;

const STATE_META = {
  idle: { label: "Ocioso", color: "bg-zinc-100 text-zinc-700 border-zinc-300", dot: "bg-zinc-400" },
  running: { label: "Em execução", color: "bg-emerald-100 text-emerald-700 border-emerald-400", dot: "bg-emerald-500" },
  paused: { label: "Pausado", color: "bg-amber-100 text-amber-700 border-amber-400", dot: "bg-amber-500" },
  done: { label: "Concluído", color: "bg-emerald-100 text-emerald-700 border-emerald-400", dot: "bg-emerald-600" },
  error: { label: "Erro", color: "bg-rose-100 text-rose-700 border-rose-400", dot: "bg-rose-500" },
  stopped: { label: "Interrompido", color: "bg-amber-100 text-amber-700 border-amber-400", dot: "bg-amber-600" },
};

export default function ReviewProductsPage() {
  const [job, setJob] = useState(null);
  const [estimate, setEstimate] = useState(null);
  const [showEstimate, setShowEstimate] = useState(true);
  const [itemsPage, setItemsPage] = useState(1);
  const [items, setItems] = useState([]);
  const [hasMore, setHasMore] = useState(false);
  const [filtro, setFiltro] = useState("all");
  const [busca, setBusca] = useState("");
  const [buscaInput, setBuscaInput] = useState("");
  const pollRef = useRef(null);

  const loadJob = useCallback(async () => {
    try {
      const { data } = await endpoints.reviewStatus();
      setJob(data);
      let list = (data.items || []).slice();
      if (filtro !== "all") list = list.filter((i) => i.status === filtro);
      if (busca) {
        const b = busca.toLowerCase();
        list = list.filter((i) => (i.sku || "").toLowerCase().includes(b) || (i.name || "").toLowerCase().includes(b));
      }
      setItems(list);
      setHasMore(false);
    } catch (err) {
      logger.error("review status:", err);
    }
  }, [filtro, busca]);

  useEffect(() => {
    loadJob();
    pollRef.current = setInterval(loadJob, JOB_POLL_MS);
    return () => clearInterval(pollRef.current);
  }, [loadJob]);

  const running = job?.state === "running";
  const paused = job?.state === "paused";
  const pct = job && job.total > 0 ? Math.round((job.completed / job.total) * 100) : 0;

  const runEstimate = async () => {
    try {
      const { data } = await endpoints.reviewEstimate(200);
      setEstimate(data);
      toast.success("Estimativa calculada");
    } catch (err) {
      toast.error("Falha ao estimar custo");
    }
  };

  const startReview = async () => {
    try {
      const { data } = await endpoints.reviewStart(null, 200);
      setShowEstimate(false);
      setJob((prev) => ({ ...prev, ...data }));
      toast.success(`Revisão iniciada: ${data.total} produtos`);
      loadJob();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Falha ao iniciar revisão");
    }
  };

  const pauseResume = async () => {
    if (paused) {
      await endpoints.reviewResume();
    } else if (running) {
      await endpoints.reviewPause();
    }
    loadJob();
  };

  const stopReview = async () => {
    await endpoints.reviewStop();
    toast.info("Parada solicitada");
    loadJob();
  };

  const markDelete = async (item) => {
    try {
      await endpoints.reviewMarkDelete({
        product_id: item.product_id,
        sku: item.sku,
        name: item.name,
        reason: "estrutura de variação incorreta",
      });
      toast.message(`Marcado para exclusão: ${item.sku}`);
      loadJob();
    } catch (err) {
      toast.error("Falha ao marcar para exclusão");
    }
  };

  const approveDeletes = async () => {
    const pendentes = job?.pending_delete || [];
    if (pendentes.length === 0) {
      toast.error("Nenhum produto na lista de exclusão");
      return;
    }
    if (!window.confirm(`Tem certeza que quer excluir ${pendentes.length} produto(s)?\nIsso exclui no Bling e pode quebrar anúncios nos marketplaces.`)) {
      return;
    }
    try {
      await endpoints.reviewApproveDeletes();
      toast.success("Exclusões aprovadas. Exclua no fornecedor (Meus Produtos) e confirme abaixo.");
    } catch (err) {
      toast.error("Falha ao aprovar exclusões");
    }
  };

  const doDelete = async (item) => {
    if (!window.confirm(`Excluir ${item.sku} no Bling? Isso pode quebrar anúncios nos marketplaces.`)) {
      return;
    }
    try {
      await endpoints.blingDeleteProduct(item.product_id);
      toast.success(`Excluído no Bling: ${item.sku}`);
      loadJob();
    } catch (err) {
      toast.error("Falha ao excluir no Bling");
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <div className="label-overline mb-1">Bling ERP</div>
        <h1 className="font-display text-3xl font-bold tracking-tighter">Revisar Produtos Cadastrados</h1>
        <p className="text-sm text-muted-foreground mt-1 max-w-3xl">
          Reaplica, em TODOS os produtos já cadastrados no Bling, a <strong>marca</strong> (item 4) e os{" "}
          <strong>atributos de marketplace</strong> — modelo, material, cor, voltagem, dimensões e
          palavras-chave (item 5). Produtos com estrutura de variação errada vão para uma lista de
          exclusão que só é executada após sua aprovação (item 6).
        </p>
      </div>

      {/* Estimativa de custo (sempre antes de começar) */}
      {showEstimate && (
        <div className="border border-border bg-amber-50 p-5 rounded-sm space-y-3">
          <div className="flex items-center gap-2">
            <Clock className="h-5 w-5 text-amber-600" />
            <span className="font-semibold text-amber-800">Estimativa de custo de IA (antes de começar)</span>
          </div>
          <div className="text-sm">
            {estimate ? (
              <div className="space-y-1">
                <div><strong>{estimate.produtos}</strong> produtos serão revisados</div>
                <div>≈ <strong>{estimate.chamadas_ia_estimadas}</strong> chamadas de IA</div>
                <div className="text-xs text-zinc-500">{estimate.nota}</div>
              </div>
            ) : (
              <div className="text-xs text-zinc-500">Clique em "Calcular estimativa" para saber quantos produtos e chamadas de IA serão usados.</div>
            )}
          </div>
          <div className="flex gap-2">
            <button
              data-testid="calc-estimate"
              onClick={runEstimate}
              disabled={!!estimate}
              className="text-sm px-4 py-2 border border-border rounded-sm hover:bg-zinc-100 inline-flex items-center gap-2"
            >
              <RefreshCw className="h-3.5 w-3.5" /> Calcular estimativa
            </button>
            {estimate && (
              <button
                data-testid="start-review"
                onClick={startReview}
                className="text-sm px-4 py-2 bg-[#EE7B22] text-white rounded-sm hover:bg-[#C9651A] inline-flex items-center gap-2"
              >
                <Play className="h-3.5 w-3.5" /> Iniciar revisão
              </button>
            )}
          </div>
        </div>
      )}

      {/* Barra de progresso */}
      {job && job.state !== "idle" && (
        <div className={`border ${running || paused ? "border-[#EE7B22]" : "border-border"} bg-white p-5`}>
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              {paused ? (
                <Pause className="h-4 w-4 text-amber-600" />
              ) : running ? (
                <Loader2 className="h-4 w-4 text-[#EE7B22] animate-spin" />
              ) : job.state === "done" ? (
                <CheckCircle2 className="h-4 w-4 text-emerald-600" />
              ) : (
                <AlertTriangle className="h-4 w-4 text-rose-600" />
              )}
              <span className="label-overline">Revisão {job.state} — {job.completed}/{job.total} ({pct}%)</span>
            </div>
            {running && (
              <button
                onClick={pauseResume}
                className="text-xs inline-flex items-center gap-1.5 border border-border px-3 py-1.5 hover:bg-zinc-50"
              >
                <Pause className="h-3 w-3" /> Pausar
              </button>
            )}
            {paused && (
              <button
                onClick={pauseResume}
                className="text-xs inline-flex items-center gap-1.5 border border-border px-3 py-1.5 hover:bg-zinc-50 bg-emerald-50 text-emerald-700 border-emerald-300"
              >
                <Play className="h-3 w-3" /> Retomar
              </button>
            )}
            <button
              onClick={stopReview}
              className="text-xs inline-flex items-center gap-1.5 border border-rose-300 px-3 py-1.5 hover:bg-rose-50 text-rose-700"
            >
              <Square className="h-3 w-3" /> Parar
            </button>
          </div>
          <div className="w-full h-2 bg-zinc-100 rounded-sm overflow-hidden mb-3">
            <div className="h-full bg-[#EE7B22] transition-all" style={{ width: `${pct}%` }} />
          </div>
          <div className="flex flex-wrap gap-4 text-xs text-zinc-700">
            <span><strong className="text-emerald-700">{job.success}</strong> ok</span>
            <span><strong className="text-rose-700">{job.errors}</strong> erros</span>
            <span><strong className="text-zinc-500">{job.skipped}</strong> pulados</span>
            {running && job.current_sku && (
              <span className="text-[#EE7B22] font-mono">→ {job.current_sku}</span>
            )}
          </div>
          {job.current_name && (
            <div className="mt-2 text-xs text-muted-foreground truncate max-w-[400px]">
              {job.current_name}
            </div>
          )}
        </div>
      )}

      {/* Lista de exclusão pendente (item 6 — aprovação prévia) */}
      {job && job.pending_delete && job.pending_delete.length > 0 && (
        <div className="border border-rose-300 bg-rose-50 p-5 rounded-sm space-y-3">
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-5 w-5 text-rose-600" />
            <span className="font-semibold text-rose-800">Aprovação de exclusão (item 6)</span>
          </div>
          <p className="text-sm text-rose-700">
            Estes produtos têm estrutura de variação incorreta. Antes de excluir no Bling, você precisa
            aprovar. Após a aprovação, exclua também no fornecedor (Meus Produtos) e só então confirme a
            exclusão no Bling — excluir quebra anúncios nos marketplaces.
          </p>
          <ul className="text-xs text-rose-700 space-y-1 max-h-40 overflow-y-auto">
            {job.pending_delete.map((d) => (
              <li key={d.sku || d.product_id} className="flex justify-between items-start">
                <span className="font-mono">{d.sku}</span>
                <span className="truncate max-w-[200px]">{d.name} — {d.reason}</span>
              </li>
            ))}
          </ul>
          <div className="flex gap-2">
            <button
              data-testid="approve-deletes"
              onClick={approveDeletes}
              className="text-sm px-4 py-2 bg-rose-600 text-white rounded-sm hover:bg-rose-700 inline-flex items-center gap-2"
            >
              <Trash2 className="h-3.5 w-3.5" /> Aprovar exclusões
            </button>
            <button
              onClick={() => endpoints.reviewClearDeleteList().then(() => loadJob())}
              className="text-sm px-4 py-2 border border-border rounded-sm hover:bg-zinc-100 inline-flex items-center gap-2"
            >
              Limpar lista
            </button>
          </div>
        </div>
      )}

      {/* Filtros + lista de resultados */}
      <div className="border border-border bg-white p-4 flex flex-wrap items-end gap-3">
        <div className="flex flex-col gap-1">
          <span className="label-overline">Filtro</span>
          <div className="flex gap-1">
            {["all", "success", "error"].map((f) => (
              <button
                key={f}
                onClick={() => { setFiltro(f); setItemsPage(1); }}
                className={`text-xs px-3 py-1.5 border ${
                  filtro === f ? "bg-[#EE7B22] text-white border-[#EE7B22]" : "border-border bg-white hover:bg-zinc-50"
                }`}
              >
                {f === "all" ? "Todos" : f === "success" ? "Ok" : "Erros"}
              </button>
            ))}
          </div>
        </div>
        <div className="flex flex-col gap-1 flex-1 min-w-[200px]">
          <span className="label-overline">Buscar</span>
          <div className="flex gap-2">
            <input
              value={buscaInput}
              onChange={(e) => setBuscaInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") { setBusca(buscaInput); setItemsPage(1); } }}
              placeholder="SKU ou nome"
              className="text-sm border border-border rounded-sm px-3 py-2 flex-1 focus:outline-none focus:ring-2 focus:ring-[#EE7B22]"
            />
            <button
              onClick={() => { setBusca(buscaInput); setItemsPage(1); }}
              className="text-xs inline-flex items-center gap-1.5 border border-border px-3 hover:bg-zinc-50"
            >
              <List className="h-3.5 w-3.5" /> Buscar
            </button>
          </div>
        </div>
      </div>

      <div className="border border-border bg-white">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-zinc-50 border-b border-border text-zinc-600">
              <tr>
                <th className="px-3 py-2 text-left">SKU</th>
                <th className="px-3 py-2 text-left">Nome</th>
                <th className="px-3 py-2 text-left">Status</th>
                <th className="px-3 py-2 text-left">Ação</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {items.length === 0 ? (
                <tr><td colSpan={4} className="px-3 py-8 text-center text-muted-foreground">
                  Nenhum produto na revisão. Inicie a revisão para ver os resultados.
                </td></tr>
              ) : (
                items.map((it) => (
                  <tr key={it.product_id || it.sku}>
                    <td className="px-3 py-2 font-mono text-xs">{it.sku}</td>
                    <td className="px-3 py-2 truncate max-w-[300px]">{it.name}</td>
                    <td className="px-3 py-2">
                      {it.status === "success" ? (
                        <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 border border-emerald-300 bg-emerald-50 text-emerald-800">
                          <CheckCircle2 className="h-3 w-3" /> Ok
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 border border-rose-300 bg-rose-50 text-rose-800">
                          <AlertTriangle className="h-3 w-3" /> Erro
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      {it.status === "error" && (
                        <button
                          onClick={() => markDelete(it)}
                          className="text-xs text-rose-600 hover:underline"
                        >
                          Marcar para excluir
                        </button>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
