import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { logger } from "@/lib/logger";
import { toast } from "sonner";
import {
  Sparkles, Search, Loader2, Send, Image as ImageIcon,
  Edit3, RefreshCw, CheckCircle2, Facebook, Instagram, Clock, Repeat,
  CheckSquare, Square, Zap,
} from "lucide-react";

const PAGE_SIZE = 24;

export default function CreateAdPage() {
  const [busca, setBusca] = useState("");
  const [pagina, setPagina] = useState(1);
  const [loadingList, setLoadingList] = useState(false);
  const [products, setProducts] = useState([]);
  const [selected, setSelected] = useState(null);

  const [audience, setAudience] = useState("público amplo, compradores online");
  const [extraBrief, setExtraBrief] = useState("");
  const [generating, setGenerating] = useState(false);
  const [draft, setDraft] = useState(null);
  const [editedCaption, setEditedCaption] = useState("");
  const [publishing, setPublishing] = useState(false);
  const [publishResult, setPublishResult] = useState(null);
  const [scheduling, setScheduling] = useState(false);
  const [channels, setChannels] = useState({ instagram: true, facebook: true, pinterest: false, youtube: false });
  const [republishing, setRepublishing] = useState(null);
  const [drafts, setDrafts] = useState([]);

  // Batch generation
  const [batchMode, setBatchMode] = useState(false);
  const [batchSelected, setBatchSelected] = useState(new Set());
  const [batchRunning, setBatchRunning] = useState(false);
  const [batchStatus, setBatchStatus] = useState(null);

  const loadProducts = async () => {
    setLoadingList(true);
    try {
      const { data } = await api.get("/social/ad/products", {
        params: { busca, pagina, limite: PAGE_SIZE },
      });
      setProducts(data.items || []);
    } catch (e) {
      logger.error("load products", e);
      toast.error("Falha ao carregar produtos");
    } finally {
      setLoadingList(false);
    }
  };

  const loadDrafts = async () => {
    try {
      const { data } = await api.get("/social/ad/drafts", { params: { limit: 20 } });
      setDrafts(data.items || []);
    } catch (e) {
      logger.error("load drafts", e);
    }
  };

  useEffect(() => { loadProducts(); loadDrafts(); }, []);

  // Poll batch status while running
  useEffect(() => {
    if (!batchRunning) return;
    let cancelled = false;
    const tick = async () => {
      if (cancelled) return;
      try {
        const { data } = await api.get("/social/ad/batch/status");
        setBatchStatus(data);
        if (!data.running) {
          setBatchRunning(false);
          loadDrafts();
          toast.success(`Lote concluído: ${data.generated} gerados, ${data.scheduled} agendados`);
        }
      } catch (_) { /* ignore polling errors */ }
    };
    const t = setInterval(tick, 2500);
    tick();
    return () => { cancelled = true; clearInterval(t); };
  }, [batchRunning]);

  const toggleBatchProduct = (pid) => {
    setBatchSelected((prev) => {
      const next = new Set(prev);
      if (next.has(pid)) next.delete(pid);
      else next.add(pid);
      return next;
    });
  };

  const startBatch = async () => {
    if (batchSelected.size === 0) {
      toast.error("Selecione ao menos 1 produto");
      return;
    }
    if (batchSelected.size > 30) {
      toast.error("Máximo 30 produtos por lote");
      return;
    }
    setBatchRunning(true);
    setBatchStatus(null);
    try {
      await api.post("/social/ad/batch/generate", {
        product_ids: Array.from(batchSelected),
        audience,
        extra_brief: extraBrief,
        auto_schedule: true,
        days_ahead: 2,
      });
      toast.success(`Lote iniciado: ${batchSelected.size} produtos`);
      setBatchSelected(new Set());
    } catch (e) {
      setBatchRunning(false);
      toast.error(e?.response?.data?.detail || "Erro ao iniciar lote");
    }
  };

  const handleGenerate = async () => {
    if (!selected) return;
    setGenerating(true);
    setDraft(null);
    setPublishResult(null);
    try {
      const { data } = await api.post("/social/ad/generate", {
        product_id: selected.id,
        audience,
        extra_brief: extraBrief,
      });
      setDraft(data);
      setEditedCaption(data.caption || "");
      toast.success("Anúncio gerado");
      loadDrafts();
    } catch (e) {
      logger.error("generate ad", e);
      toast.error(e?.response?.data?.detail || "Falha ao gerar anúncio");
    } finally {
      setGenerating(false);
    }
  };

  const handleRegenerate = () => { setDraft(null); handleGenerate(); };

  const republishDraft = async (id) => {
    setRepublishing(id);
    try {
      const { data } = await api.post(`/social/ad/republish/${id}`);
      if (data.ok) toast.success("Republicado com sucesso!");
      else toast.error("Republicação falhou em todos os canais");
      loadDrafts();
    } catch (e) {
      logger.error("republish", e);
      toast.error(e?.response?.data?.detail || "Erro ao republicar");
    } finally {
      setRepublishing(null);
    }
  };

  const handleSchedule = async () => {
    if (!draft) return;
    setScheduling(true);
    try {
      const { data } = await api.post("/social/ad/schedule", { draft_id: draft.draft_id });
      toast.success(`Agendado para ${new Date(data.publish_at).toLocaleString("pt-BR")}`);
      loadDrafts();
    } catch (e) {
      logger.error("schedule ad", e);
      toast.error(e?.response?.data?.detail || "Falha ao agendar");
    } finally {
      setScheduling(false);
    }
  };

  const handlePublish = async () => {
    if (!draft) return;
    setPublishing(true);
    setPublishResult(null);
    try {
      const { data } = await api.post("/social/ad/publish", {
        draft_id: draft.draft_id,
        caption: editedCaption,
        publish_instagram: channels.instagram,
        publish_facebook: channels.facebook,
        publish_pinterest: channels.pinterest,
      });

      // YouTube is a separate flow (different content type — video) — fire in parallel after Meta/Pinterest
      let ytRes = null;
      if (channels.youtube) {
        try {
          const yt = await api.post("/social/youtube/publish", {
            draft_id: draft.draft_id,
            privacy_status: "public",
          });
          ytRes = yt.data;
        } catch (e) {
          ytRes = { ok: false, error: e?.response?.data?.detail || "erro YouTube" };
        }
      }
      setPublishResult({ ...data, youtube: ytRes });
      if (data.ok || ytRes?.ok) toast.success("Anúncio publicado");
      else toast.error("Publicação falhou em todos os canais");
      loadDrafts();
    } catch (e) {
      logger.error("publish ad", e);
      toast.error(e?.response?.data?.detail || "Erro ao publicar");
    } finally {
      setPublishing(false);
    }
  };

  return (
    <div className="space-y-6" data-testid="create-ad-page">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-display font-bold">Criar Anúncio</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Selecione um produto enriquecido → gere imagem (Nano Banana) e copy (Claude) → publique no Instagram e Facebook.
          </p>
        </div>
      </header>

      {/* Step 1: Pick product */}
      {!selected && (
        <section className="rounded-sm border border-border bg-white p-5" data-testid="step-pick-product">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-base font-semibold">1. Escolha um produto</h2>
            <div className="flex items-center gap-2">
              <button
                onClick={() => { setBatchMode(!batchMode); setBatchSelected(new Set()); }}
                data-testid="toggle-batch-mode"
                className={`px-3 py-2 text-xs rounded-sm flex items-center gap-1.5 ${
                  batchMode ? "bg-[#EE7B22] text-white" : "bg-zinc-100 hover:bg-zinc-200"
                }`}
              >
                <Zap className="h-3.5 w-3.5" />
                {batchMode ? `Lote: ${batchSelected.size}` : "Modo Lote"}
              </button>
              <div className="relative">
                <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                <input
                  type="text"
                  data-testid="product-search-input"
                  value={busca}
                  onChange={(e) => setBusca(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && loadProducts()}
                  placeholder="Buscar por nome ou SKU"
                  className="pl-8 pr-3 py-2 text-sm border border-border rounded-sm w-72"
                />
              </div>
              <button
                onClick={loadProducts}
                data-testid="reload-products-btn"
                className="px-3 py-2 text-sm bg-zinc-100 hover:bg-zinc-200 rounded-sm flex items-center gap-2"
              >
                {loadingList ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                Buscar
              </button>
            </div>
          </div>

          {batchMode && (
            <div className="mb-4 p-3 rounded-sm bg-amber-50 border border-amber-200 text-sm space-y-2" data-testid="batch-control">
              <div className="font-semibold">Geração em Lote</div>
              <p className="text-xs text-zinc-700">
                Selecione até 30 produtos abaixo. A IA gera anúncios em paralelo e agenda automaticamente nos próximos picos (12h/18h/21h) ao longo de 2 dias.
              </p>
              <div className="flex items-center gap-2 pt-1">
                <input
                  type="text"
                  data-testid="batch-audience"
                  value={audience}
                  onChange={(e) => setAudience(e.target.value)}
                  placeholder="Público-alvo (ex: público amplo)"
                  className="text-xs px-2 py-1 border border-border rounded-sm flex-1"
                />
                <button
                  onClick={startBatch}
                  disabled={batchRunning || batchSelected.size === 0}
                  data-testid="start-batch-btn"
                  className="px-3 py-1.5 bg-[#EE7B22] text-white text-xs font-medium rounded-sm hover:bg-[#d56a18] disabled:opacity-40 flex items-center gap-1.5"
                >
                  {batchRunning ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Zap className="h-3.5 w-3.5" />}
                  {batchRunning ? "Em execução…" : `Gerar e Agendar ${batchSelected.size}`}
                </button>
              </div>
              {batchRunning && batchStatus && (
                <div className="text-xs space-y-1 pt-2 border-t border-amber-200">
                  <div>Gerados: <strong>{batchStatus.generated}/{batchStatus.total}</strong></div>
                  <div>Agendados: <strong>{batchStatus.scheduled}</strong></div>
                  {batchStatus.failed > 0 && <div className="text-rose-700">Falhas: {batchStatus.failed}</div>}
                  <div className="h-1.5 bg-amber-200 rounded-sm overflow-hidden mt-1">
                    <div className="h-full bg-[#EE7B22]" style={{ width: `${(batchStatus.generated / Math.max(batchStatus.total, 1)) * 100}%` }} />
                  </div>
                </div>
              )}
            </div>
          )}

          {loadingList ? (
            <div className="py-16 text-center text-muted-foreground">
              <Loader2 className="h-6 w-6 mx-auto animate-spin mb-2" />
              Carregando produtos enriquecidos…
            </div>
          ) : products.length === 0 ? (
            <div className="py-16 text-center text-muted-foreground" data-testid="no-products">
              Nenhum produto enriquecido encontrado. Acesse &quot;Enriquecimento Bling&quot; para enriquecer produtos primeiro.
            </div>
          ) : (
            <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
              {products.map((p) => {
                const inBatch = batchSelected.has(p.id);
                const onClick = batchMode
                  ? () => toggleBatchProduct(p.id)
                  : () => setSelected(p);
                return (
                  <button
                    key={p.id}
                    data-testid={`product-card-${p.id}`}
                    onClick={onClick}
                    className={`relative text-left border rounded-sm overflow-hidden transition ${
                      batchMode && inBatch ? "border-[#EE7B22] ring-2 ring-[#EE7B22]"
                      : "border-border hover:border-[#EE7B22] hover:shadow-md"
                    }`}
                  >
                    {batchMode && (
                      <div className="absolute top-1.5 left-1.5 z-10 bg-white rounded-sm p-0.5 shadow">
                        {inBatch ? <CheckSquare className="h-4 w-4 text-[#EE7B22]" /> : <Square className="h-4 w-4 text-zinc-400" />}
                      </div>
                    )}
                    <div className="aspect-square bg-zinc-100 flex items-center justify-center">
                      {p.image_url ? (
                        <img src={p.image_url} alt={p.nome} className="w-full h-full object-cover" />
                      ) : (
                        <ImageIcon className="h-8 w-8 text-zinc-400" />
                      )}
                    </div>
                    <div className="p-2">
                      <div className="text-xs font-medium line-clamp-2 leading-snug">{p.nome}</div>
                      <div className="text-xs text-muted-foreground mt-1">{p.codigo}</div>
                      <div className="text-xs font-semibold text-[#EE7B22] mt-0.5">R$ {Number(p.preco).toFixed(2)}</div>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </section>
      )}

      {/* Step 2: Brief */}
      {selected && !draft && (
        <section className="rounded-sm border border-border bg-white p-5" data-testid="step-brief">
          <div className="flex items-start justify-between mb-4">
            <h2 className="text-base font-semibold">2. Briefing</h2>
            <button
              onClick={() => setSelected(null)}
              data-testid="reset-selection-btn"
              className="text-xs text-muted-foreground hover:text-zinc-900"
            >
              Trocar produto
            </button>
          </div>

          <div className="grid md:grid-cols-3 gap-5">
            <div className="md:col-span-1">
              <div className="border border-border rounded-sm overflow-hidden">
                <div className="aspect-square bg-zinc-100">
                  {selected.image_url ? (
                    <img src={selected.image_url} alt={selected.nome} className="w-full h-full object-cover" />
                  ) : (
                    <div className="w-full h-full flex items-center justify-center"><ImageIcon className="h-8 w-8 text-zinc-400" /></div>
                  )}
                </div>
                <div className="p-3">
                  <div className="text-sm font-medium">{selected.nome}</div>
                  <div className="text-xs text-muted-foreground mt-1">{selected.codigo}</div>
                  <div className="text-sm font-semibold text-[#EE7B22] mt-1">R$ {Number(selected.preco).toFixed(2)}</div>
                </div>
              </div>
            </div>

            <div className="md:col-span-2 space-y-4">
              <div>
                <label className="block text-xs font-medium mb-1.5">Público-alvo</label>
                <input
                  type="text"
                  data-testid="audience-input"
                  value={audience}
                  onChange={(e) => setAudience(e.target.value)}
                  placeholder="Ex: jovens 18-25 fãs de gadgets"
                  className="w-full px-3 py-2 text-sm border border-border rounded-sm"
                />
              </div>
              <div>
                <label className="block text-xs font-medium mb-1.5">Briefing adicional (opcional)</label>
                <textarea
                  data-testid="brief-textarea"
                  value={extraBrief}
                  onChange={(e) => setExtraBrief(e.target.value)}
                  rows={3}
                  placeholder="Ex: enfatizar promoção de Natal, frete grátis, etc."
                  className="w-full px-3 py-2 text-sm border border-border rounded-sm resize-none"
                />
              </div>
              <button
                onClick={handleGenerate}
                disabled={generating}
                data-testid="generate-ad-btn"
                className="px-5 py-2.5 bg-[#EE7B22] text-white text-sm font-medium rounded-sm hover:bg-[#d56a18] disabled:opacity-50 flex items-center gap-2"
              >
                {generating ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
                {generating ? "Gerando…" : "Gerar Anúncio com IA"}
              </button>
              {generating && (
                <div className="text-xs text-muted-foreground" data-testid="generating-hint">
                  Isso leva ~15-30s. Gemini Nano Banana cria a imagem 1:1 e Claude escreve o copy.
                </div>
              )}
            </div>
          </div>
        </section>
      )}

      {/* Step 3: Preview & Publish */}
      {draft && (
        <section className="rounded-sm border border-border bg-white p-5" data-testid="step-preview">
          <div className="flex items-start justify-between mb-4">
            <h2 className="text-base font-semibold">3. Preview & Publicar</h2>
            <button
              onClick={handleRegenerate}
              disabled={generating}
              data-testid="regenerate-btn"
              className="text-xs px-3 py-1.5 border border-border rounded-sm hover:bg-zinc-50 flex items-center gap-1.5"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${generating ? "animate-spin" : ""}`} />
              Gerar novamente
            </button>
          </div>

          <div className="grid md:grid-cols-2 gap-6">
            <div>
              <div className="aspect-square border border-border rounded-sm overflow-hidden bg-zinc-100">
                <img src={draft.image_url} alt="Anúncio" className="w-full h-full object-cover" data-testid="ad-preview-image" />
              </div>
              <div className="text-xs text-muted-foreground mt-2">
                Headline: <span className="font-medium text-zinc-900">{draft.headline}</span>
              </div>
            </div>

            <div className="space-y-3">
              <div>
                <label className="text-xs font-medium flex items-center gap-1.5 mb-1.5">
                  <Edit3 className="h-3.5 w-3.5" /> Caption (edite antes de publicar)
                </label>
                <textarea
                  data-testid="caption-textarea"
                  value={editedCaption}
                  onChange={(e) => setEditedCaption(e.target.value)}
                  rows={9}
                  className="w-full px-3 py-2 text-sm border border-border rounded-sm font-mono"
                />
                <div className="text-xs text-muted-foreground mt-1">{editedCaption.length} caracteres</div>
              </div>

              <div className="rounded-sm bg-zinc-50 px-3 py-2.5 text-xs space-y-1.5" data-testid="channel-picker">
                <div className="font-medium text-zinc-700 mb-1">Canais</div>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    data-testid="ch-instagram"
                    checked={channels.instagram}
                    onChange={(e) => setChannels({ ...channels, instagram: e.target.checked })}
                  />
                  <Instagram className="h-3.5 w-3.5 text-pink-600" /> Instagram
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    data-testid="ch-facebook"
                    checked={channels.facebook}
                    onChange={(e) => setChannels({ ...channels, facebook: e.target.checked })}
                  />
                  <Facebook className="h-3.5 w-3.5 text-blue-600" /> Facebook
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    data-testid="ch-pinterest"
                    checked={channels.pinterest}
                    onChange={(e) => setChannels({ ...channels, pinterest: e.target.checked })}
                  />
                  <span className="inline-block h-3.5 w-3.5 rounded-full bg-red-600 text-white text-[8px] flex items-center justify-center font-bold">P</span> Pinterest
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    data-testid="ch-youtube"
                    checked={channels.youtube}
                    onChange={(e) => setChannels({ ...channels, youtube: e.target.checked })}
                  />
                  <span className="inline-block h-3.5 w-3.5 rounded-sm bg-red-600 text-white text-[8px] flex items-center justify-center font-bold">▶</span> YouTube Shorts
                </label>
              </div>

              <button
                onClick={handlePublish}
                disabled={publishing || !editedCaption}
                data-testid="publish-btn"
                className="w-full py-2.5 bg-[#EE7B22] text-white text-sm font-medium rounded-sm hover:bg-[#d56a18] disabled:opacity-50 flex items-center justify-center gap-2"
              >
                {publishing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                {publishing ? "Publicando…" : "Publicar agora"}
              </button>

              <button
                onClick={handleSchedule}
                disabled={scheduling || !editedCaption}
                data-testid="schedule-btn"
                title="Agenda para o próximo horário de pico (12h / 18h / 21h)"
                className="w-full py-2.5 border border-[#EE7B22] text-[#EE7B22] text-sm font-medium rounded-sm hover:bg-orange-50 disabled:opacity-50 flex items-center justify-center gap-2"
              >
                {scheduling ? <Loader2 className="h-4 w-4 animate-spin" /> : <Clock className="h-4 w-4" />}
                {scheduling ? "Agendando…" : "Agendar para próximo pico"}
              </button>

              {publishResult && (
                <div className="border border-border rounded-sm p-3 text-sm space-y-1.5" data-testid="publish-result">
                  <div className="flex items-center gap-2">
                    <Instagram className="h-3.5 w-3.5 text-pink-600" />
                    <span className="font-medium">Instagram:</span>
                    {publishResult.instagram?.ok ? (
                      <span className="text-emerald-700 flex items-center gap-1"><CheckCircle2 className="h-3.5 w-3.5" /> publicado</span>
                    ) : (
                      <span className="text-rose-600 text-xs">{publishResult.instagram?.error || "—"}</span>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    <Facebook className="h-3.5 w-3.5 text-blue-600" />
                    <span className="font-medium">Facebook:</span>
                    {publishResult.facebook?.ok ? (
                      <span className="text-emerald-700 flex items-center gap-1"><CheckCircle2 className="h-3.5 w-3.5" /> publicado</span>
                    ) : (
                      <span className="text-rose-600 text-xs">{publishResult.facebook?.error || "—"}</span>
                    )}
                  </div>
                  {publishResult.pinterest && (
                    <div className="flex items-center gap-2">
                      <span className="inline-block h-3.5 w-3.5 rounded-full bg-red-600 text-white text-[8px] flex items-center justify-center font-bold">P</span>
                      <span className="font-medium">Pinterest:</span>
                      {publishResult.pinterest?.ok ? (
                        <a href={publishResult.pinterest.url} target="_blank" rel="noreferrer" className="text-emerald-700 flex items-center gap-1 hover:underline">
                          <CheckCircle2 className="h-3.5 w-3.5" /> publicado
                        </a>
                      ) : (
                        <span className="text-rose-600 text-xs">{publishResult.pinterest?.error || "—"}</span>
                      )}
                    </div>
                  )}
                  {publishResult.youtube && (
                    <div className="flex items-center gap-2">
                      <span className="inline-block h-3.5 w-3.5 rounded-sm bg-red-600 text-white text-[8px] flex items-center justify-center font-bold">▶</span>
                      <span className="font-medium">YouTube:</span>
                      {publishResult.youtube?.ok ? (
                        <a href={publishResult.youtube.url} target="_blank" rel="noreferrer" className="text-emerald-700 flex items-center gap-1 hover:underline">
                          <CheckCircle2 className="h-3.5 w-3.5" /> publicado
                        </a>
                      ) : (
                        <span className="text-rose-600 text-xs">{publishResult.youtube?.error || "—"}</span>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </section>
      )}

      {/* Drafts history */}
      {drafts.length > 0 && (
        <section className="rounded-sm border border-border bg-white p-5" data-testid="drafts-section">
          <h2 className="text-base font-semibold mb-3">Histórico</h2>
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
            {drafts.map((d) => (
              <div
                key={d.id}
                data-testid={`draft-${d.id}`}
                className="border border-border rounded-sm overflow-hidden"
              >
                <div className="aspect-square bg-zinc-100">
                  {d.image_url && <img src={d.image_url} alt={d.headline} className="w-full h-full object-cover" />}
                </div>
                <div className="p-2">
                  <div className="text-xs font-medium line-clamp-1">{d.headline || d.product_name}</div>
                  <div className="text-xs mt-1 flex items-center justify-between">
                    {d.status === "published" ? (
                      <span className="text-emerald-700">Publicado</span>
                    ) : d.status === "failed" ? (
                      <span className="text-rose-600">Falhou</span>
                    ) : (
                      <span className="text-zinc-500">Rascunho</span>
                    )}
                    {(d.status === "failed" || d.status === "draft") && (
                      <button
                        onClick={() => republishDraft(d.id)}
                        disabled={republishing === d.id}
                        data-testid={`republish-${d.id}`}
                        title="Tentar publicar de novo"
                        className="text-blue-600 hover:text-blue-800 inline-flex items-center gap-1"
                      >
                        {republishing === d.id ? <Loader2 className="h-3 w-3 animate-spin" /> : <Repeat className="h-3 w-3" />}
                        {republishing === d.id ? "..." : "Republicar"}
                      </button>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
