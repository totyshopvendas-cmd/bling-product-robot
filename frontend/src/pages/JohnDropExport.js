import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import {
  FileSpreadsheet, Play, Loader2, Download, Square, CheckCircle2,
  AlertTriangle, XCircle, Package,
} from "lucide-react";
import { api } from "@/lib/api";
import { logger } from "@/lib/logger";

const POLL_MS = 3000;

const PHASE_LABEL = {
  idle: "Aguardando",
  login: "Fazendo login na JonhDrop...",
  list: "Lendo a lista de Meus Produtos...",
  details: "Abrindo a página de cada produto...",
  writing: "Gerando a planilha...",
  done: "Concluído",
  stopped: "Interrompido (planilha parcial gerada)",
  error: "Erro",
};

function fmtEta(sec) {
  if (sec == null) return "";
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)}h${String(m % 60).padStart(2, "0")}`;
}

function fmtSize(bytes) {
  if (!bytes) return "";
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export default function JohnDropExportPage() {
  const [status, setStatus] = useState(null);
  const [detailed, setDetailed] = useState(true);
  const [starting, setStarting] = useState(false);
  const [downloading, setDownloading] = useState(null);
  const pollRef = useRef(null);
  const wasRunning = useRef(false);

  const loadStatus = useCallback(async () => {
    try {
      const { data } = await api.get("/johndrop-export/status");
      setStatus(data);
      if (wasRunning.current && !data?.running) {
        if (data?.file) toast.success(`Planilha pronta: ${data.file}`);
        else if (data?.error) toast.error(data.error);
      }
      wasRunning.current = Boolean(data?.running);
    } catch (err) {
      logger.error("johndrop-export status:", err);
    }
  }, []);

  useEffect(() => {
    loadStatus();
    pollRef.current = setInterval(loadStatus, POLL_MS);
    return () => pollRef.current && clearInterval(pollRef.current);
  }, [loadStatus]);

  const running = Boolean(status?.running);

  const start = async () => {
    setStarting(true);
    try {
      const { data } = await api.post("/johndrop-export/run", { detailed });
      if (data?.ok) {
        toast.success("Exportação iniciada");
        wasRunning.current = true;
        await loadStatus();
      } else {
        toast.warning(data?.message || "Exportação já em andamento");
      }
    } catch (err) {
      logger.error("johndrop-export run:", err);
      toast.error("Falha ao iniciar exportação");
    } finally {
      setStarting(false);
    }
  };

  const stop = async () => {
    try {
      await api.post("/johndrop-export/stop");
      toast.info("Interrompendo — a planilha parcial será gerada");
    } catch (err) {
      logger.error("johndrop-export stop:", err);
    }
  };

  const download = async (name) => {
    setDownloading(name || "latest");
    try {
      const res = await api.get("/johndrop-export/download", {
        params: name ? { name } : {},
        responseType: "blob",
        timeout: 120000,
      });
      const url = URL.createObjectURL(res.data);
      const a = document.createElement("a");
      a.href = url;
      a.download = name || status?.file || "jonhdrop_produtos.xlsx";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      logger.error("johndrop-export download:", err);
      toast.error("Não foi possível baixar a planilha");
    } finally {
      setDownloading(null);
    }
  };

  const total = status?.total || 0;
  const done = status?.done || 0;
  const pct = status?.phase === "details" && total ? Math.round((done / total) * 100) : null;
  const files = status?.files || [];

  return (
    <div className="space-y-6" data-testid="johndrop-export-page">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-display font-semibold tracking-tight flex items-center gap-2">
            <FileSpreadsheet className="h-6 w-6 text-[#EE7B22]" /> Exportar Planilha JonhDrop
          </h1>
          <p className="text-sm text-muted-foreground mt-1 max-w-3xl">
            O robô entra na JonhDrop com o login salvo em <strong>Configurações</strong>, lê todos
            os produtos de <strong>Meus Produtos</strong> e gera um arquivo Excel (.xlsx).
            Somente leitura: nada é alterado na JonhDrop nem no Bling.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {running ? (
            <button
              data-testid="stop-export"
              onClick={stop}
              disabled={status?.stop_requested}
              className="inline-flex items-center gap-2 border border-rose-300 text-rose-700 hover:bg-rose-50 px-4 py-2 rounded-sm text-sm font-medium disabled:opacity-50"
            >
              <Square className="h-4 w-4" /> {status?.stop_requested ? "Parando..." : "Parar e salvar parcial"}
            </button>
          ) : (
            <button
              data-testid="run-export"
              onClick={start}
              disabled={starting}
              className="inline-flex items-center gap-2 bg-[#EE7B22] hover:bg-[#d96d1c] text-white px-4 py-2 rounded-sm text-sm font-medium disabled:opacity-50 transition-colors"
            >
              {starting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
              Gerar planilha
            </button>
          )}
        </div>
      </div>

      {/* Modo */}
      <div className="grid md:grid-cols-2 gap-3">
        {[
          {
            id: true,
            title: "Completo (recomendado)",
            desc: "Abre a página de cada produto: descrição, variações (cores/tamanhos), todas as imagens, peso, medidas, NCM, categoria. Leva ~3–5 s por produto.",
          },
          {
            id: false,
            title: "Básico (rápido)",
            desc: "Só a lista: ID, nome, SKU, canal, preço, estoque, data e imagem principal. Leva poucos minutos.",
          },
        ].map((opt) => (
          <button
            key={String(opt.id)}
            data-testid={`mode-${opt.id ? "full" : "basic"}`}
            disabled={running}
            onClick={() => setDetailed(opt.id)}
            className={`text-left border px-4 py-3 bg-white transition-colors disabled:opacity-60 ${
              detailed === opt.id ? "border-[#EE7B22] ring-1 ring-[#EE7B22]" : "border-border hover:bg-zinc-50"
            }`}
          >
            <div className="text-sm font-medium">{opt.title}</div>
            <div className="text-xs text-muted-foreground mt-1">{opt.desc}</div>
          </button>
        ))}
      </div>

      {/* Progresso */}
      {status && status.phase !== "idle" && (
        <div className="border border-border bg-white px-4 py-4 space-y-3" data-testid="export-progress">
          <div className="flex items-center gap-2 text-sm">
            {running ? (
              <Loader2 className="h-4 w-4 animate-spin text-[#EE7B22]" />
            ) : status.phase === "error" ? (
              <XCircle className="h-4 w-4 text-rose-600" />
            ) : status.phase === "stopped" ? (
              <AlertTriangle className="h-4 w-4 text-amber-600" />
            ) : (
              <CheckCircle2 className="h-4 w-4 text-emerald-600" />
            )}
            <span className="font-medium">{PHASE_LABEL[status.phase] || status.phase}</span>
            {status.phase === "list" && (
              <span className="text-muted-foreground">
                — página {status.list_pages}, {total} produtos
              </span>
            )}
            {status.phase === "details" && status.eta_seconds != null && (
              <span className="text-muted-foreground">— faltam ~{fmtEta(status.eta_seconds)}</span>
            )}
          </div>

          {pct != null && (
            <div>
              <div className="h-2 bg-zinc-100 overflow-hidden">
                <div className="h-full bg-[#EE7B22] transition-all" style={{ width: `${pct}%` }} />
              </div>
              <div className="flex justify-between text-xs text-muted-foreground mt-1">
                <span>{done} de {total} produtos ({pct}%)</span>
                <span className="truncate max-w-[60%] text-right">{status.current}</span>
              </div>
            </div>
          )}

          <div className="grid grid-cols-3 gap-3 text-sm">
            <Stat icon={<Package className="h-4 w-4" />} label="Produtos" value={total} />
            <Stat icon={<CheckCircle2 className="h-4 w-4 text-emerald-600" />} label="Detalhes OK" value={status.detail_ok || 0} />
            <Stat icon={<XCircle className="h-4 w-4 text-rose-600" />} label="Sem detalhe" value={status.detail_failed || 0} />
          </div>

          {status.error && (
            <div className="text-sm text-rose-700 bg-rose-50 border border-rose-200 px-3 py-2">{status.error}</div>
          )}
          {(status.warnings || []).map((w, i) => (
            <div key={i} className="text-xs text-amber-800 bg-amber-50 border border-amber-200 px-3 py-2">{w}</div>
          ))}

          {!running && status.file && (
            <button
              data-testid="download-latest"
              onClick={() => download(status.file)}
              disabled={Boolean(downloading)}
              className="inline-flex items-center gap-2 bg-emerald-600 hover:bg-emerald-700 text-white px-4 py-2 rounded-sm text-sm font-medium disabled:opacity-50"
            >
              {downloading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
              Baixar {status.file}
            </button>
          )}
        </div>
      )}

      {/* Histórico */}
      <div className="border border-border bg-white">
        <div className="px-4 py-2 border-b border-border text-sm font-medium">Planilhas geradas</div>
        {files.length === 0 ? (
          <div className="px-4 py-10 text-center text-sm text-muted-foreground">
            Nenhuma planilha ainda. Clique em "Gerar planilha".
          </div>
        ) : (
          <table className="w-full text-sm" data-testid="export-files">
            <tbody>
              {files.map((f) => (
                <tr key={f.name} className="border-t border-border first:border-t-0">
                  <td className="px-4 py-2 font-mono text-xs">{f.name}</td>
                  <td className="px-4 py-2 text-xs text-muted-foreground">
                    {new Date(f.modified).toLocaleString("pt-BR")}
                  </td>
                  <td className="px-4 py-2 text-xs text-muted-foreground">{fmtSize(f.size)}</td>
                  <td className="px-4 py-2 text-right">
                    <button
                      onClick={() => download(f.name)}
                      disabled={Boolean(downloading)}
                      className="inline-flex items-center gap-1 text-xs px-3 py-1.5 border border-border hover:bg-zinc-50 disabled:opacity-50"
                    >
                      {downloading === f.name ? <Loader2 className="h-3 w-3 animate-spin" /> : <Download className="h-3 w-3" />}
                      Baixar
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="text-xs text-muted-foreground">
        Abas da planilha: <strong>Produtos</strong> (1 linha por SKU) · <strong>Variações</strong> ·{" "}
        <strong>Imagens</strong> (todas as URLs) · <strong>Todos os campos</strong> (tudo o que aparece na
        página do produto) · <strong>Resumo</strong>.
      </div>
    </div>
  );
}

function Stat({ icon, label, value }) {
  return (
    <div className="border border-border px-3 py-2">
      <div className="flex items-center gap-2 text-xs text-zinc-500">{icon}<span>{label}</span></div>
      <div className="text-xl font-mono font-semibold mt-0.5">{value}</div>
    </div>
  );
}
