import { useEffect, useState, useCallback } from "react";
import { api, endpoints } from "@/lib/api";
import { logger } from "@/lib/logger";
import { Play, Square, RefreshCw, Bot, AlertTriangle, CheckCircle2, Download, Loader2, FileSpreadsheet, FileText, Stethoscope } from "lucide-react";
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

export default function DropperRobotPage() {
  const [status, setStatus] = useState(null);
  const [maxProducts, setMaxProducts] = useState(5);
  const [dryRun, setDryRun] = useState(true);
  const [logs, setLogs] = useState([]);
  const [chromium, setChromium] = useState(null);
  const [installing, setInstalling] = useState(false);
  const [sheetInfo, setSheetInfo] = useState(null);
  const [downloading, setDownloading] = useState(null);
  const [diag, setDiag] = useState(null);
  const [diagLoading, setDiagLoading] = useState(false);

  const [ready, setReady] = useState(null);

  const tick = useCallback(async () => {
    try {
      const [{ data: s }, { data: l }, { data: c }, { data: sh }] = await Promise.all([
        endpoints.dropperStatus(),
        endpoints.dropperLogs(50),
        api.get("/system/chromium-status"),
        api.get("/dropper/sheet"),
      ]);
      setStatus(s);
      setLogs(l);
      setChromium(c);
      setSheetInfo(sh);
    } catch (err) {
      logger.error("dropper status:", err);
      setChromium((prev) => prev || { installed: false });
    }
  }, []);

  useEffect(() => {
    tick();
    const timer = setInterval(tick, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [tick]);

  const installChromium = async () => {
    setInstalling(true);
    try {
      const { data } = await api.post("/system/install-chromium");
      if (data.already_installed) {
        toast.success("Chromium já está pronto");
      } else {
        toast.message("Tentando baixar o Chromium no servidor…");
      }
      const startTs = Date.now();
      const poll = setInterval(async () => {
        try {
          const { data: c } = await api.get("/system/chromium-status");
          setChromium(c);
          if (c.installed) {
            clearInterval(poll);
            setInstalling(false);
            toast.success("Chromium instalado!");
            return;
          }
          if (Date.now() - startTs > 45000) {
            clearInterval(poll);
            setInstalling(false);
            toast.error("Não foi possível baixar o Chromium nesta prévia (rede bloqueada).");
          }
        } catch (err) {
          logger.error("poll chromium:", err);
        }
      }, 4000);
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Falha");
      setInstalling(false);
    }
  };

  const runDiagnostico = async () => {
    setDiagLoading(true);
    try {
      const { data } = await api.get("/dropper/diagnostico", { timeout: 180000 });
      setDiag(data.report);
      toast.success("Diagnóstico concluído");
    } catch (e) {
      logger.error("dropper diagnostico:", e);
      toast.error(e?.response?.data?.detail || "Falha no diagnóstico");
    } finally {
      setDiagLoading(false);
    }
  };

  const downloadSheet = async (name) => {
    setDownloading(name || "latest");
    try {
      const res = await endpoints.dropperSheetDownload(name);
      const url = URL.createObjectURL(res.data);
      const a = document.createElement("a");
      a.href = url;
      a.download = name || sheetInfo?.file || "planilha_bling.csv";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      logger.error("dropper sheet download:", e);
      toast.error("Não foi possível baixar o arquivo");
    } finally {
      setDownloading(null);
    }
  };

  const start = async () => {
    if (chromium && !chromium.installed) {
      toast.error("Chromium não está pronto — clique em 'Instalar Chromium' primeiro");
      return;
    }
    try {
      const limite = parseInt(maxProducts, 10);
      await endpoints.dropperStart(Number.isFinite(limite) ? limite : 5, dryRun);
      toast.success("Robô Dropper iniciado");
      tick();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Falha ao iniciar");
    }
  };

  const stop = async () => {
    await endpoints.dropperStop();
    toast.info("Parada solicitada");
    tick();
  };

  const meta = STATE_META[status?.state] || STATE_META.idle;
  const chromiumReady = chromium?.installed;

  return (
    <div className="space-y-6">
      <div>
        <div className="label-overline mb-1">Automação Dropper</div>
        <h1 className="font-display text-3xl font-bold tracking-tighter">Robô Dropper → Planilha Bling</h1>
        <p className="text-sm text-muted-foreground mt-1 max-w-3xl">
          O robô entra na <strong>Dropper (dropper.dropping.com.br)</strong>, abre cada produto do catálogo
          e <strong>lê tudo que está lá</strong>: título, descrição, imagens, código, custo, NCM, EAN,
          marca, categoria, unidade e estoque. Depois busca o preço de venda na tabela, gera as descrições
          e monta a <strong>planilha de importação do Bling</strong> — no formato de 59 colunas, pronta
          para importar.
        </p>
        <p className="text-xs text-zinc-600 mt-2 max-w-3xl">
          O robô <strong>não cadastra nada no Dropper</strong> e <strong>não usa a API do Bling</strong>.
          Nada é inventado: campo que não existe no produto fica vazio e aparece no relatório de revisão.
        </p>
        <p className="text-xs text-amber-700 mt-2 max-w-3xl">
          ℹ️ Em <strong>MODO TESTE</strong> a planilha é gerada com o nome{" "}
          <code>planilha_bling_TESTE_...</code> — mesmo conteúdo, só marcada como teste, sem nada
          enviado para o Dropper. Desmarque para gerar o arquivo "oficial". Usuário e senha da Dropper
          são digitados em <strong>Configurações</strong> (nunca neste chat).
        </p>
      </div>

      <div className="border border-border bg-white p-4 text-sm space-y-1">
        <div className="label-overline mb-2">Antes de gerar a planilha</div>
        <p>{ready?.dropper ? "✓" : "✗"} Dropper (e-mail/senha em Configurações)</p>
        <p>{ready?.pricing_rows > 0 ? "✓" : "✗"} Tabela de preços ({ready?.pricing_rows?.toLocaleString?.("pt-BR") || 0} linhas)</p>
        <p>{ready?.chromium ? "✓" : "✗"} Chromium do robô</p>
        <p className="text-xs text-muted-foreground pt-1">
          Bling conectado não é necessário nesta etapa (a planilha é importada manualmente no Bling).
        </p>
      </div>

      {chromium && (
        <div
          data-testid="chromium-status-banner"
          className={`border p-4 flex items-start justify-between gap-4 ${
            chromiumReady
              ? "bg-emerald-50 border-emerald-300 text-emerald-900"
              : "bg-amber-50 border-amber-300 text-amber-950"
          }`}
        >
          <div className="flex items-start gap-3">
            {chromiumReady ? (
              <CheckCircle2 className="h-5 w-5 flex-shrink-0 mt-0.5" />
            ) : installing ? (
              <Loader2 className="h-5 w-5 flex-shrink-0 mt-0.5 animate-spin" />
            ) : (
              <AlertTriangle className="h-5 w-5 flex-shrink-0 mt-0.5" />
            )}
            <div className="text-sm space-y-1">
              <div className="font-semibold">
                {chromiumReady
                  ? "Chromium do servidor pronto"
                  : installing
                  ? "Instalando Chromium no servidor…"
                  : "Chromium do servidor não instalado"}
              </div>
              <p className="text-xs leading-relaxed">
                {chromiumReady
                  ? chromium.path
                  : "Necessário para o robô ler as páginas do Dropper. Roda invisível no servidor — não abre janela no seu PC."}
              </p>
            </div>
          </div>
          {!chromiumReady && (
            <button
              data-testid="install-chromium-btn"
              onClick={installChromium}
              disabled={installing}
              className="text-sm font-medium px-4 py-2 rounded-sm bg-[#EE7B22] text-white hover:bg-[#C9651A] disabled:opacity-60 inline-flex items-center gap-2 shrink-0"
            >
              <Download className="h-4 w-4" />
              {installing ? "Instalando…" : "Instalar Chromium"}
            </button>
          )}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2 border border-border bg-white p-6 space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <div className="label-overline mb-1">Estado atual</div>
              <span
                data-testid="dropper-state-badge"
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
              <div className="label-overline">Produtos lidos</div>
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
            <label className="text-xs text-muted-foreground">Máx. produtos por execução (0 = todos)</label>
            <input
              data-testid="max-products-input"
              type="number"
              min={0}
              max={2000}
              value={maxProducts}
              onChange={(e) => setMaxProducts(e.target.value)}
              className="w-full text-sm border border-border rounded-sm px-3 py-2 mt-1 focus:outline-none focus:ring-2 focus:ring-[#EE7B22]"
            />
          </div>
          <label
            className={`flex items-start gap-2 text-sm cursor-pointer p-3 rounded-sm border ${
              dryRun ? "bg-amber-50 border-amber-300" : "bg-emerald-50 border-emerald-300"
            }`}
          >
            <input
              data-testid="dry-run-toggle"
              type="checkbox"
              checked={dryRun}
              onChange={(e) => setDryRun(e.target.checked)}
              className="h-4 w-4 mt-0.5"
            />
            <div className="flex-1">
              <div className="font-semibold">{dryRun ? "🟡 MODO TESTE (arquivo marcado como teste)" : "🟢 GERAR ARQUIVO OFICIAL"}</div>
              <div className="text-xs mt-1 text-zinc-700">
                {dryRun
                  ? "Lê os produtos e gera a planilha com o nome ..._TESTE_..., sem enviar nada para o Dropper."
                  : "Lê os produtos e gera a planilha oficial para importar no Bling. Nada é enviado para o Dropper."}
              </div>
            </div>
          </label>

          <div className="flex flex-col gap-2 pt-2">
            <button
              data-testid="start-dropper-btn"
              onClick={start}
              disabled={status?.state === "running"}
              className={`w-full text-white text-sm font-medium px-4 py-2.5 rounded-sm disabled:opacity-50 inline-flex items-center justify-center gap-2 ${
                dryRun ? "bg-[#EE7B22] hover:bg-[#C9651A]" : "bg-emerald-600 hover:bg-emerald-700"
              }`}
            >
              <Play className="h-4 w-4" />
              {dryRun ? "Ler Dropper (MODO TESTE)" : "Ler Dropper e gerar planilha"}
            </button>
            <button
              data-testid="stop-dropper-btn"
              onClick={stop}
              disabled={status?.state !== "running"}
              className="w-full border border-border text-sm font-medium px-4 py-2.5 rounded-sm hover:bg-zinc-50 disabled:opacity-50 inline-flex items-center justify-center gap-2"
            >
              <Square className="h-4 w-4" /> Parar
            </button>
            <button
              data-testid="diagnostico-btn"
              onClick={runDiagnostico}
              disabled={diagLoading || status?.state === "running"}
              className="w-full border border-border text-sm font-medium px-4 py-2.5 rounded-sm hover:bg-zinc-50 disabled:opacity-50 inline-flex items-center justify-center gap-2"
            >
              {diagLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Stethoscope className="h-4 w-4" />}
              {diagLoading ? "Lendo o site…" : "Diagnóstico (o robô consegue ler?)"}
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

      {diag && (
        <div className="border border-border bg-white" data-testid="diagnostico-box">
          <div className="px-6 py-3 border-b border-border flex items-center justify-between">
            <span className="label-overline">Resultado do diagnóstico</span>
            <div className="flex items-center gap-3">
              <button
                onClick={() => {
                  navigator.clipboard?.writeText(diag);
                  toast.success("Copiado — cole aqui no chat");
                }}
                className="text-xs text-muted-foreground hover:text-foreground"
              >
                Copiar
              </button>
              <button onClick={() => setDiag(null)} className="text-xs text-muted-foreground hover:text-foreground">
                Fechar
              </button>
            </div>
          </div>
          <pre className="px-6 py-4 text-[11px] leading-relaxed overflow-x-auto max-h-[420px] overflow-y-auto whitespace-pre-wrap font-mono">
            {diag}
          </pre>
        </div>
      )}

      <div className="border border-border bg-white" data-testid="sheet-box">
        <div className="px-6 py-3 border-b border-border flex items-center justify-between">
          <span className="label-overline">Planilha gerada</span>
          {sheetInfo?.modified && (
            <span className="text-xs text-muted-foreground">
              {new Date(sheetInfo.modified).toLocaleString("pt-BR")}
            </span>
          )}
        </div>
        {!sheetInfo?.file ? (
          <div className="px-6 py-10 text-sm text-muted-foreground text-center">
            Nenhuma planilha gerada ainda. Clique em <strong>Ler Dropper</strong> para criar a primeira.
          </div>
        ) : (
          <div className="px-6 py-4 space-y-3">
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <FileSpreadsheet className="h-4 w-4 text-emerald-600 shrink-0" />
                  <span className="font-mono text-sm truncate" data-testid="sheet-file">
                    {sheetInfo.file}
                  </span>
                  {sheetInfo.is_test && (
                    <span className="text-[10px] uppercase font-semibold px-1.5 py-0.5 border border-amber-400 bg-amber-50 text-amber-700 rounded-sm">
                      teste
                    </span>
                  )}
                </div>
                <p className="text-xs text-muted-foreground mt-1">
                  {sheetInfo.products > 0 && `${sheetInfo.products} produtos · `}
                  {sheetInfo.rows > 0 && `${sheetInfo.rows} linhas · `}
                  {(sheetInfo.size / 1024).toFixed(1)} KB
                </p>
              </div>
              <button
                data-testid="download-sheet-btn"
                onClick={() => downloadSheet(sheetInfo.file)}
                disabled={downloading === sheetInfo.file}
                className="text-sm font-medium px-4 py-2 rounded-sm bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-60 inline-flex items-center gap-2 shrink-0"
              >
                {downloading === sheetInfo.file ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Download className="h-4 w-4" />
                )}
                Baixar CSV (Bling)
              </button>
            </div>
            {sheetInfo.report && (
              <button
                data-testid="download-report-btn"
                onClick={() => downloadSheet(sheetInfo.report)}
                className="text-xs text-muted-foreground hover:text-foreground inline-flex items-center gap-1.5"
              >
                <FileText className="h-3.5 w-3.5" /> Baixar relatório de revisão (.txt)
              </button>
            )}
            <p className="text-[11px] text-muted-foreground leading-relaxed border-t border-border pt-2">
              No Bling: <strong>Produtos → Importar</strong> → escolha este arquivo. O separador é
              ponto e vírgula e o arquivo já vai com acentuação correta (UTF-8).
            </p>
          </div>
        )}
      </div>

      <div className="border border-border bg-white">
        <div className="px-6 py-3 border-b border-border flex items-center justify-between">
          <span className="label-overline">Logs ao vivo (50 últimos)</span>
          <span className="text-xs text-muted-foreground">{logs.length} entradas</span>
        </div>
        <div className="max-h-[420px] overflow-y-auto divide-y divide-border" data-testid="logs-stream">
          {logs.length === 0 ? (
            <div className="px-6 py-12 text-sm text-muted-foreground text-center">
              Nenhum log ainda. Inicie o robô Dropper para ver atividade.
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
