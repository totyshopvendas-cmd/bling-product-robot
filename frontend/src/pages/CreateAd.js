import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { logger } from "@/lib/logger";
import { toast } from "sonner";
import {
  Sparkles, Search, Loader2, Send, Image as ImageIcon,
  Edit3, RefreshCw, CheckCircle2, Facebook, Instagram,
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
  const [drafts, setDrafts] = useState([]);

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

  useEffect(() => { loadProducts(); loadDrafts(); }, []); // eslint-disable-line

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

  const handlePublish = async () => {
    if (!draft) return;
    setPublishing(true);
    setPublishResult(null);
    try {
      const { data } = await api.post("/social/ad/publish", {
        draft_id: draft.draft_id,
        caption: editedCaption,
        publish_instagram: true,
        publish_facebook: true,
      });
      setPublishResult(data);
      if (data.ok) toast.success("Anúncio publicado");
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
              {products.map((p) => (
                <button
                  key={p.id}
                  data-testid={`product-card-${p.id}`}
                  onClick={() => setSelected(p)}
                  className="text-left border border-border rounded-sm overflow-hidden hover:border-[#EE7B22] hover:shadow-md transition"
                >
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
              ))}
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

              <div className="rounded-sm bg-zinc-50 px-3 py-2 text-xs">
                Publicar em:
                <span className="inline-flex items-center gap-1 ml-2 px-2 py-0.5 bg-pink-100 text-pink-700 rounded-sm">
                  <Instagram className="h-3 w-3" /> Instagram
                </span>
                <span className="inline-flex items-center gap-1 ml-1.5 px-2 py-0.5 bg-blue-100 text-blue-700 rounded-sm">
                  <Facebook className="h-3 w-3" /> Facebook
                </span>
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
                  <div className="text-xs mt-1">
                    {d.status === "published" ? (
                      <span className="text-emerald-700">Publicado</span>
                    ) : d.status === "failed" ? (
                      <span className="text-rose-600">Falhou</span>
                    ) : (
                      <span className="text-zinc-500">Rascunho</span>
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
