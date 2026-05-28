import { useState } from "react";
import { endpoints } from "@/lib/api";
import { Sparkles, Copy, Check } from "lucide-react";
import { toast } from "sonner";

const COPY_FEEDBACK_MS = 1500;
const EXAMPLES = [
  "Caneta Touch Screen Stylus Universal Para Tablet e Celular XLS B125 / A-P18",
  "(KA-1369 (4X AAA)) Carregador de Pilhas com LED + 04 Pilhas AAA Recarregáveis Kapbom KA-1369",
  "(KA-S079) Câmera Segurança Babá Eletrônica Wi-fi 360° Kapbom KA-S079",
  "(EL-1931) Caneta Peeling Ultrassônico E Ionização Portátil Anti Cravos E Acne Eletromex EL-1931",
];

export default function TitleCleanerPage() {
  const [raw, setRaw] = useState(EXAMPLES[0]);
  const [sku, setSku] = useState("");
  const [useLlm, setUseLlm] = useState(false);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);

  const onClean = async () => {
    if (!raw.trim()) {
      toast.error("Informe um título");
      return;
    }
    setLoading(true);
    try {
      const { data } = await endpoints.cleanTitle(raw, sku || undefined, useLlm);
      setResult(data);
    } catch (e) {
      toast.error("Erro ao limpar título: " + (e?.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  };

  const copyResult = async () => {
    if (!result) return;
    await navigator.clipboard.writeText(result.cleaned);
    setCopied(true);
    toast.success("Título copiado");
    setTimeout(() => setCopied(false), COPY_FEEDBACK_MS);
  };

  return (
    <div className="space-y-6">
      <div>
        <div className="label-overline mb-1">Engine de Limpeza</div>
        <h1 className="font-display text-3xl font-bold tracking-tighter">Limpeza de Título</h1>
        <p className="text-sm text-muted-foreground mt-1 max-w-3xl">
          Remove marcas (XLS, Kapbom, Inova, Altomex, Eletromex…), códigos EAN, caracteres especiais e
          adjetivos desnecessários. Garante código no final e máximo de 60 caracteres.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-0 border border-border bg-white">
        <div className="p-6 border-b lg:border-b-0 lg:border-r border-border">
          <div className="label-overline mb-2">Título Cru (fornecedor)</div>
          <textarea
            data-testid="raw-title-input"
            value={raw}
            onChange={(e) => setRaw(e.target.value)}
            rows={5}
            className="w-full text-sm border border-border rounded-sm px-3 py-2 focus:outline-none focus:ring-2 focus:ring-[#EE7B22] font-mono"
          />
          <div className="mt-3 grid grid-cols-2 gap-3">
            <div>
              <div className="label-overline mb-1">SKU (opcional)</div>
              <input
                data-testid="sku-input"
                type="text"
                value={sku}
                onChange={(e) => setSku(e.target.value)}
                placeholder="ex: KA-1369"
                className="w-full text-sm border border-border rounded-sm px-3 py-2 focus:outline-none focus:ring-2 focus:ring-[#EE7B22]"
              />
            </div>
            <label className="flex items-end gap-2 text-sm pb-2 cursor-pointer">
              <input
                data-testid="use-llm-checkbox"
                type="checkbox"
                checked={useLlm}
                onChange={(e) => setUseLlm(e.target.checked)}
                className="h-4 w-4"
              />
              <span>Usar IA como fallback</span>
            </label>
          </div>

          <div className="mt-4 flex items-center gap-3">
            <button
              data-testid="clean-btn"
              onClick={onClean}
              disabled={loading}
              className="bg-[#EE7B22] text-white text-sm font-medium px-4 py-2.5 rounded-sm hover:bg-[#C9651A] disabled:opacity-50 inline-flex items-center gap-2"
            >
              <Sparkles className="h-4 w-4" />
              {loading ? "Processando…" : "Limpar Título"}
            </button>
            <div className="text-xs text-muted-foreground">
              {raw.length} caracteres no original
            </div>
          </div>

          <div className="mt-6">
            <div className="label-overline mb-2">Exemplos rápidos</div>
            <div className="space-y-1.5">
              {EXAMPLES.map((ex) => (
                <button
                  key={ex}
                  data-testid={`example-${ex.slice(0, 15)}`}
                  onClick={() => setRaw(ex)}
                  className="block w-full text-left text-xs px-3 py-2 border border-border rounded-sm hover:border-[#EE7B22] hover:bg-zinc-50 truncate font-mono"
                  title={ex}
                >
                  {ex}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="p-6 bg-zinc-50">
          <div className="flex items-center justify-between mb-2">
            <span className="label-overline">Título Limpo</span>
            {result && (
              <button
                data-testid="copy-btn"
                onClick={copyResult}
                className="text-xs flex items-center gap-1 text-zinc-600 hover:text-[#EE7B22]"
              >
                {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                {copied ? "Copiado" : "Copiar"}
              </button>
            )}
          </div>

          {!result ? (
            <div className="border border-dashed border-border h-32 grid place-items-center text-sm text-muted-foreground">
              Clique em "Limpar Título" para ver o resultado
            </div>
          ) : (
            <div className="space-y-4">
              <div
                data-testid="cleaned-result"
                className="bg-white border border-border p-4 font-mono text-sm break-words"
              >
                {result.cleaned}
              </div>

              <div className="grid grid-cols-3 gap-3 text-xs">
                <div className="bg-white border border-border p-3">
                  <div className="label-overline mb-1">Tamanho</div>
                  <div className="font-display text-xl font-bold">
                    <span className={result.length <= 60 ? "text-emerald-600" : "text-rose-600"}>
                      {result.length}
                    </span>
                    <span className="text-muted-foreground text-sm">/60</span>
                  </div>
                </div>
                <div className="bg-white border border-border p-3">
                  <div className="label-overline mb-1">Código final</div>
                  <div className="font-mono text-sm">{result.code_used || "—"}</div>
                </div>
                <div className="bg-white border border-border p-3">
                  <div className="label-overline mb-1">Método</div>
                  <div className="text-sm uppercase font-semibold">{result.method}</div>
                </div>
              </div>

              {result.removed_terms?.length > 0 && (
                <div>
                  <div className="label-overline mb-2">Removidos</div>
                  <div className="flex flex-wrap gap-1.5">
                    {result.removed_terms.map((t) => (
                      <span
                        key={t}
                        className="text-xs px-2 py-1 bg-rose-50 text-rose-700 rounded-sm border border-rose-200 font-mono"
                      >
                        {t}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
