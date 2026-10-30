import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { logger } from "@/lib/logger";
import { ShieldAlert, SearchCheck, Sparkles, RefreshCw, CheckCircle2, AlertTriangle, Loader2 } from "lucide-react";
import { toast } from "sonner";

export default function BlingAuditPage() {
  const [audit, setAudit] = useState(null);
  const [selected, setSelected] = useState(new Set());
  const [loading, setLoading] = useState(true);
  const [fixing, setFixing] = useState(false);

  const load = async () => {
    try {
      setLoading(true);
      const { data } = await api.get("/bling/audit", { params: { limit: 500 } });
      setAudit(data || {});
      setSelected(new Set((data?.items || []).filter((it) => it.needs_review).map((it) => it.id)));
    } catch (err) {
      logger.error("Failed to load Bling audit:", err);
      toast.error(err?.response?.data?.detail || err?.message || "Falha ao carregar auditoria");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const toggleOne = (id) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const fixBrands = async () => {
    const ids = Array.from(selected);
    if (!ids.length) {
      toast.error("Selecione pelo menos 1 produto para correção segura");
      return;
    }
    try {
      setFixing(true);
      const { data } = await api.post("/bling/audit/fix-brands", { product_ids: ids });
      toast.success(`Marcas revisadas: ${data.updated || 0} / ${ids.length}`);
      await load();
    } catch (err) {
      toast.error(err?.response?.data?.detail || err.message || "Falha ao corrigir marcas");
    } finally {
      setFixing(false);
    }
  };

  const stats = audit || {};
  const items = stats.items || [];

  return (
    <div className="space-y-6">
      <div>
        <div className="label-overline mb-1">Bling ERP</div>
        <h1 className="font-display text-3xl font-bold tracking-tighter">Auditoria Bling</h1>
        <p className="text-sm text-muted-foreground mt-1 max-w-3xl">
          Auditoria somente leitura com revisão humana. O painel identifica produtos sem marca, sem descrição gerada, sem categoria ou com campos insuficientes para marketplace, mas nunca remove registros automaticamente.
        </p>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        <div className="border border-border bg-white p-4">
          <div className="label-overline">Varridos</div>
          <div className="font-display text-3xl font-bold tracking-tighter">{stats.total || 0}</div>
        </div>
        <div className="border border-border bg-white p-4">
          <div className="label-overline">Sem marca</div>
          <div className="font-display text-3xl font-bold tracking-tighter text-amber-600">{stats.marcas || 0}</div>
        </div>
        <div className="border border-border bg-white p-4">
          <div className="label-overline">Sem IA</div>
          <div className="font-display text-3xl font-bold tracking-tighter text-orange-600">{stats.ia_campo || 0}</div>
        </div>
        <div className="border border-border bg-white p-4">
          <div className="label-overline">Marketplace</div>
          <div className="font-display text-3xl font-bold tracking-tighter text-rose-600">{stats.marketplace || 0}</div>
        </div>
        <div className="border border-border bg-white p-4">
          <div className="label-overline">Revisão</div>
          <div className="font-display text-3xl font-bold tracking-tighter text-emerald-600">{stats.review_exclusion || 0}</div>
        </div>
      </div>

      <div className="border border-border bg-white p-4 flex flex-wrap items-center gap-3">
        <button
          onClick={fixBrands}
          disabled={fixing || selected.size === 0}
          className="bg-[#EE7B22] text-white text-sm font-medium px-4 py-2 rounded-sm hover:bg-[#C9651A] disabled:opacity-40 inline-flex items-center gap-2"
        >
          <Sparkles className="h-4 w-4" />
          {fixing ? "Corrigindo..." : `Corrigir marcas seguras (${selected.size})`}
        </button>
        <button
          onClick={load}
          className="text-sm border border-border px-4 py-2 rounded-sm hover:bg-zinc-50 inline-flex items-center gap-2"
        >
          <RefreshCw className="h-4 w-4" /> Atualizar
        </button>
      </div>

      <div className="border border-border bg-white">
        <div className="px-4 py-3 border-b border-border flex items-center justify-between">
          <div className="inline-flex items-center gap-2 text-sm font-medium">
            <SearchCheck className="h-4 w-4 text-[#EE7B22]" /> Produtos em revisão
          </div>
          <div className="text-xs text-muted-foreground">{items.filter((i) => i.needs_review).length} itens</div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-zinc-50 border-b border-border text-zinc-600">
              <tr>
                <th className="px-3 py-2 text-left w-10"><input type="checkbox" checked={items.filter((i) => i.needs_review).length > 0 && items.filter((i) => i.needs_review).every((i) => selected.has(i.id))} onChange={() => { const list = items.filter((i) => i.needs_review).map((i) => i.id); const allSelected = list.length > 0 && list.every((id) => selected.has(id)); const next = new Set(selected); if (allSelected) list.forEach((id) => next.delete(id)); else list.forEach((id) => next.add(id)); setSelected(next); }} /></th>
                <th className="px-3 py-2 text-left">SKU</th>
                <th className="px-3 py-2 text-left">Nome</th>
                <th className="px-3 py-2 text-left">Marca</th>
                <th className="px-3 py-2 text-left">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {loading ? (
                <tr><td colSpan={5} className="px-3 py-8 text-center text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin inline mr-2" /> Carregando auditoria…</td></tr>
              ) : items.length === 0 ? (
                <tr><td colSpan={5} className="px-3 py-8 text-center text-muted-foreground">Nenhum produto encontrado.</td></tr>
              ) : (
                items.map((item) => (
                  <tr key={item.id} className={selected.has(item.id) ? "bg-orange-50" : ""}>
                    <td className="px-3 py-2">
                      <input type="checkbox" checked={selected.has(item.id)} onChange={() => toggleOne(item.id)} disabled={!item.needs_review} title={!item.needs_review ? "Produto saudável" : "Produto em revisão"} />
                    </td>
                    <td className="px-3 py-2 font-mono text-xs">{item.codigo || "—"}</td>
                    <td className="px-3 py-2 max-w-[420px] truncate" title={item.nome}>{item.nome || "—"}</td>
                    <td className="px-3 py-2 text-xs">{item.marca || "—"}</td>
                    <td className="px-3 py-2">
                      {item.needs_review ? (
                        <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 border border-amber-300 bg-amber-50 text-amber-800">
                          <AlertTriangle className="h-3 w-3" /> Revisar
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 border border-emerald-300 bg-emerald-50 text-emerald-800">
                          <CheckCircle2 className="h-3 w-3" /> OK
                        </span>
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
