import { useEffect, useState, useCallback } from "react";
import { api, endpoints } from "@/lib/api";
import { logger } from "@/lib/logger";
import { Play, Square, RefreshCw, Package, AlertTriangle, CheckCircle2, Download, Loader2 } from "lucide-react";
import { toast } from "sonner";

const STATE_META = {
  idle: { label: "Ocioso", color: "bg-zinc-100 text-zinc-700 border-zinc-300" },
  running: { label: "Em execução", color: "bg-emerald-100 text-emerald-700 border-emerald-400" },
  paused: { label: "Pausado", color: "bg-amber-100 text-amber-700 border-amber-400" },
  error: { label: "Erro", color: "bg-rose-100 text-rose-700 border-rose-400" },
};
const LOG_DOT = { success: "bg-emerald-500", error: "bg-rose-500", warning: "bg-amber-500", info: "bg-zinc-400" };

export default function DropperRobot() {
  const [status, setStatus] = useState(null);
  const [logs, setLogs] = useState([]);
  const [chromium, setChromium] = useState(null);
  const [ready, setReady] = useState(null);
  const [maxProducts, setMaxProducts] = useState(5);
  const [dryRun, setDryRun] = useState(true);
  const [installing, setInstalling] = useState(false);

  const tick = useCallback(async () => {
    try { setStatus((await endpoints.dropperStatus()).data); } catch (e) { logger.error("dropper status", e); }
    try { setLogs((await endpoints.dropperLogs(50)).data); } catch (e) { logger.error("dropper logs", e); }
    try { setChromium((await api.get("/system/chromium-status")).data); } catch (e) { logger.error("chromium", e); }
    try {
      const [d, p] = await Promise.all([endpoints.getDropperStatus(), endpoints.pricingStats()]);
      setReady({ dropper: d.data.configured, pricing_rows: p.data.count || 0 });
    } catch (e) { logger.error("dropper ready", e); }
  }, []);

  useEffect(() => { tick(); const t = setInterval(tick, 2500); return () => clearInterval(t); }, [tick]);

  const installChromium = async () => {
    setInstalling(true);
    try {
      await api.post("/system/install-chromium");
      toast.info("Instalação do Chromium iniciada");
      const started = Date.now();
      const timer = setInterval(async () => {
        try {
          const c = (await api.get("/system/chromium-status")).data;
          setChromium(c);
          if (c.installed || Date.now() - started > 50000) { clearInterval(timer); setInstalling(false); }
        } catch (_) { /* polling tolera indisponibilidade temporária */ }
      }, 3000);
    } catch (e) { setInstalling(false); toast.error(e?.response?.data?.detail || "Falha ao instalar Chromium"); }
  };

  const start = async () => {
    if (!dryRun && chromium && !chromium.installed) return toast.error("Instale o Chromium antes do modo real");
    try { await endpoints.dropperStart(parseInt(maxProducts, 10) || 5, dryRun); toast.success("Robô Dropper iniciado"); tick(); }
    catch (e) { toast.error(e?.response?.data?.detail || "Falha ao iniciar"); }
  };
  const stop = async () => { try { await endpoints.dropperStop(); toast.info("Parada solicitada"); tick(); } catch (e) { toast.error("Falha ao parar"); } };
  const meta = STATE_META[status?.state] || STATE_META.idle;
  const chromiumReady = chromium?.installed;

  return (
    <div className="space-y-6" data-testid="dropper-robot-page">
      <div>
        <div className="label-overline mb-1">Automação Dropper</div>
        <h1 className="font-display text-3xl font-bold tracking-tighter">Robô Dropper</h1>
        <p className="text-sm text-muted-foreground mt-1 max-w-3xl">Entra no Dropper, prepara título, SKU, custo, preço de venda e cadastra os produtos na integração configurada. Depois do cadastro, o produto fica aguardando a sincronização nativa Dropper → Bling.</p>
        <p className="text-xs text-amber-700 mt-2">Por segurança, começa em <strong>Modo Teste</strong>. O modo real só envia o cadastro ao Dropper.</p>
      </div>

      {ready && <div className="border border-border bg-white p-4 text-sm space-y-1"><div className="label-overline mb-2">Antes de cadastrar de verdade</div><p>{ready.dropper ? "✓" : "✗"} Dropper (e-mail/senha em Configurações)</p><p>{ready.pricing_rows > 0 ? "✓" : "✗"} Tabela de preços ({Number(ready.pricing_rows).toLocaleString("pt-BR")} linhas)</p><p>{chromiumReady ? "✓" : "✗"} Chromium do robô</p><p>✓ Cadastro isolado — sem escrita direta na API do Bling</p></div>}

      {chromium && <div className={`border p-4 flex items-start justify-between gap-4 ${chromiumReady ? "bg-emerald-50 border-emerald-300 text-emerald-900" : "bg-amber-50 border-amber-300 text-amber-950"}`}>
        <div className="flex items-start gap-3">{chromiumReady ? <CheckCircle2 className="h-5 w-5 mt-0.5" /> : installing ? <Loader2 className="h-5 w-5 mt-0.5 animate-spin" /> : <AlertTriangle className="h-5 w-5 mt-0.5" />}<div className="text-sm"><div className="font-semibold">{chromiumReady ? "Chromium do servidor pronto" : installing ? "Instalando Chromium…" : "Chromium não instalado"}</div><p className="text-xs mt-1">{chromiumReady ? chromium.path : "Necessário apenas para o modo real do Dropper."}</p></div></div>
        {!chromiumReady && <button onClick={installChromium} disabled={installing} className="text-sm font-medium px-4 py-2 rounded-sm bg-[#EE7B22] text-white disabled:opacity-60 inline-flex items-center gap-2"><Download className="h-4 w-4" />{installing ? "Instalando…" : "Instalar Chromium"}</button>}
      </div>}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4"><div className="lg:col-span-2 border border-border bg-white p-6 space-y-4"><div className="flex items-center justify-between"><div><div className="label-overline mb-1">Estado atual</div><span className={`inline-flex px-3 py-1 text-xs font-semibold border rounded-sm uppercase ${meta.color}`}>{status?.state === "running" && <span className="h-2 w-2 rounded-full bg-emerald-500 status-pulse mr-2 my-auto" />}{meta.label}</span></div><Package className="h-8 w-8 text-zinc-300" strokeWidth={1.5} /></div>{status?.current_product && <div className="bg-zinc-50 border border-border p-3 text-sm"><div className="label-overline mb-1">Processando</div><div className="font-mono truncate">{status.current_product}</div></div>}<div className="grid grid-cols-3 border border-border divide-x divide-border"><div className="p-4"><div className="label-overline">Processados</div><div className="font-display text-2xl font-bold">{status?.processed || 0}</div></div><div className="p-4"><div className="label-overline">Sucessos</div><div className="font-display text-2xl font-bold text-emerald-600">{status?.success || 0}</div></div><div className="p-4"><div className="label-overline">Falhas</div><div className="font-display text-2xl font-bold text-rose-600">{status?.failed || 0}</div></div></div>{status?.message && <div className="bg-rose-50 border border-rose-200 text-rose-700 text-sm p-3 flex gap-2"><AlertTriangle className="h-4 w-4 mt-0.5" />{status.message}</div>}</div>
        <div className="border border-border bg-white p-6 space-y-4"><div className="label-overline">Controles</div><div><label className="text-xs text-muted-foreground">Máx. produtos por execução</label><input type="number" min={1} max={100} value={maxProducts} onChange={(e) => setMaxProducts(e.target.value)} className="w-full text-sm border border-border rounded-sm px-3 py-2 mt-1" /></div><label className={`flex items-start gap-2 text-sm cursor-pointer p-3 rounded-sm border ${dryRun ? "bg-amber-50 border-amber-300" : "bg-emerald-50 border-emerald-300"}`}><input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} className="h-4 w-4 mt-0.5" /><div><div className="font-semibold">{dryRun ? "🟡 MODO TESTE" : "🟢 MODO REAL"}</div><div className="text-xs mt-1">{dryRun ? "Simula sem cadastrar no Dropper." : "Cadastra de verdade no Dropper."}</div></div></label><div className="flex flex-col gap-2 pt-2"><button onClick={start} disabled={status?.state === "running"} className={`w-full text-white text-sm font-medium px-4 py-2.5 rounded-sm disabled:opacity-50 inline-flex items-center justify-center gap-2 ${dryRun ? "bg-[#EE7B22]" : "bg-emerald-600"}`}><Play className="h-4 w-4" />{dryRun ? "Iniciar Robô (Teste)" : "Iniciar Robô (REAL)"}</button><button onClick={stop} disabled={status?.state !== "running"} className="w-full border border-border text-sm font-medium px-4 py-2.5 rounded-sm disabled:opacity-50 inline-flex items-center justify-center gap-2"><Square className="h-4 w-4" />Parar</button><button onClick={tick} className="w-full text-xs text-muted-foreground inline-flex items-center justify-center gap-1.5 py-1"><RefreshCw className="h-3.5 w-3.5" />Atualizar</button></div></div></div>

      <div className="border border-border bg-white"><div className="px-6 py-3 border-b border-border flex items-center justify-between"><span className="label-overline">Logs do Dropper (50 últimos)</span><span className="text-xs text-muted-foreground">{logs.length} entradas</span></div><div className="max-h-[420px] overflow-y-auto divide-y divide-border">{logs.length === 0 ? <div className="px-6 py-12 text-sm text-muted-foreground text-center">Nenhum log ainda.</div> : logs.map((log) => <div key={log.id} className="px-6 py-3 flex items-start gap-4 text-sm"><span className={`mt-1 h-2 w-2 rounded-full flex-shrink-0 ${LOG_DOT[log.level] || LOG_DOT.info}`} /><div><div className="font-mono text-xs text-muted-foreground">{new Date(log.created_at).toLocaleTimeString("pt-BR")}</div><div>{log.message}</div></div></div>)}</div></div>
    </div>
  );
}
