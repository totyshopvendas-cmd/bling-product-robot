import { useEffect, useRef, useState, useCallback } from "react";
import { endpoints } from "@/lib/api";
import { logger } from "@/lib/logger";
import { Upload, Search, Database } from "lucide-react";
import { toast } from "sonner";

export default function PricingPage() {
  const [stats, setStats] = useState(null);
  const [lookup, setLookup] = useState({ cost: "21.99", result: null, loading: false });
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef(null);

  const load = useCallback(async () => {
    try {
      const { data } = await endpoints.pricingStats();
      setStats(data);
    } catch (err) {
      logger.error("Failed to load pricing stats:", err);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const onUpload = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      const { data } = await endpoints.uploadPricing(file);
      toast.success(`${data.imported.toLocaleString("pt-BR")} linhas importadas`);
      if (data.errors?.length) {
        toast.warning(`${data.errors.length} avisos`);
      }
      await load();
    } catch (e) {
      toast.error("Falha no upload: " + (e?.response?.data?.detail || e.message));
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  };

  const onLookup = async (e) => {
    e?.preventDefault?.();
    setLookup(l => ({ ...l, loading: true }));
    try {
      const { data } = await endpoints.lookupPrice(parseFloat(lookup.cost));
      setLookup(l => ({ ...l, result: data, loading: false }));
    } catch (err) {
      toast.error(err.message);
      setLookup(l => ({ ...l, loading: false }));
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <div className="label-overline mb-1">Precificação</div>
        <h1 className="font-display text-3xl font-bold tracking-tighter">Tabela de Preços</h1>
        <p className="text-sm text-muted-foreground mt-1 max-w-3xl">
          Importe o CSV de precificação. O robô consulta o custo do produto JohnDrop e copia o "Preço de Venda" (formato inteiro, sem pontuação).
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="border border-border bg-white p-6">
          <div className="flex items-center gap-2 mb-3">
            <Database className="h-4 w-4 text-zinc-500" />
            <span className="label-overline">Status</span>
          </div>
          <div className="font-display text-4xl font-bold tracking-tighter">
            {stats?.count?.toLocaleString("pt-BR") || 0}
          </div>
          <div className="text-xs text-muted-foreground">linhas carregadas</div>
        </div>

        <div className="border border-border bg-white p-6 lg:col-span-2">
          <div className="label-overline mb-2">Importar tabela</div>
          <p className="text-xs text-muted-foreground mb-3">
            Aceita <strong>Excel (.xlsx)</strong> ou CSV. Colunas: Custo do Catálogo, Preço da Loja, Preço de Venda.
            Envie pelo painel em <span className="font-mono">127.0.0.1:8000</span> (não pela Arena).
          </p>
          <input
            ref={fileRef}
            data-testid="pricing-upload"
            type="file"
            accept=".xlsx,.xlsm,.xls,.csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            onChange={onUpload}
            className="hidden"
          />
          <button
            data-testid="pricing-upload-btn"
            onClick={() => fileRef.current?.click()}
            disabled={uploading}
            className="bg-[#EE7B22] text-white text-sm font-medium px-4 py-2.5 rounded-sm hover:bg-[#C9651A] disabled:opacity-50 inline-flex items-center gap-2"
          >
            <Upload className="h-4 w-4" />
            {uploading ? "Importando…" : "Selecionar Excel ou CSV"}
          </button>
        </div>
      </div>

      <form
        onSubmit={onLookup}
        className="border border-border bg-white p-6 space-y-4"
      >
        <div className="flex items-center gap-2">
          <Search className="h-4 w-4 text-zinc-500" />
          <span className="label-overline">Consulta de Preço</span>
        </div>
        <div className="flex flex-col sm:flex-row gap-3">
          <input
            data-testid="lookup-cost-input"
            type="number"
            step="0.01"
            value={lookup.cost}
            onChange={(e) => setLookup(l => ({ ...l, cost: e.target.value }))}
            placeholder="Custo do catálogo (ex: 21.99)"
            className="flex-1 text-sm border border-border rounded-sm px-3 py-2.5 focus:outline-none focus:ring-2 focus:ring-[#EE7B22]"
          />
          <button
            data-testid="lookup-btn"
            type="submit"
            className="bg-[#EE7B22] text-white text-sm font-medium px-5 py-2.5 rounded-sm hover:bg-[#C9651A]"
          >
            Consultar
          </button>
        </div>

        {lookup.result && (
          <div
            data-testid="lookup-result"
            className={`border ${lookup.result.found ? "border-emerald-500" : "border-rose-500"} p-4 grid grid-cols-2 sm:grid-cols-4 gap-4`}
          >
            <div>
              <div className="label-overline">Custo</div>
              <div className="font-mono text-sm mt-1">R$ {lookup.result.cost.toFixed(2).replace(".", ",")}</div>
            </div>
            <div>
              <div className="label-overline">Preço Loja</div>
              <div className="font-mono text-sm mt-1">R$ {lookup.result.store_price_brl || "—"}</div>
            </div>
            <div>
              <div className="label-overline">Preço Venda</div>
              <div className="font-display text-2xl font-bold tracking-tighter">{lookup.result.sale_price_int || "—"}</div>
            </div>
            <div>
              <div className="label-overline">Status</div>
              <div className={`text-sm font-semibold mt-1 ${lookup.result.found ? "text-emerald-600" : "text-rose-600"}`}>
                {lookup.result.found ? "Encontrado" : "Não encontrado"}
              </div>
            </div>
          </div>
        )}
      </form>

      {stats?.first?.length > 0 && (
        <div className="border border-border bg-white">
          <div className="px-6 py-4 border-b border-border">
            <div className="label-overline">Amostra (primeiras / últimas linhas)</div>
          </div>
          <table className="w-full text-sm">
            <thead className="border-b border-border">
              <tr className="text-left">
                <th className="px-6 py-3 font-medium label-overline">Custo (R$)</th>
                <th className="px-6 py-3 font-medium label-overline">Preço Loja</th>
                <th className="px-6 py-3 font-medium label-overline">Preço Venda</th>
              </tr>
            </thead>
            <tbody>
              {[...stats.first, ...stats.last].map((r) => (
                <tr key={r.cost_cents} className="border-b border-border last:border-b-0">
                  <td className="px-6 py-2.5 font-mono">{(r.cost_cents / 100).toFixed(2).replace(".", ",")}</td>
                  <td className="px-6 py-2.5 font-mono">R$ {r.store_price_brl}</td>
                  <td className="px-6 py-2.5 font-mono font-semibold">{r.sale_price_int}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
