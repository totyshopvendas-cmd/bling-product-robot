import { useRef, useState } from "react";
import { toast } from "sonner";
import { DatabaseBackup, Download, Upload, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { logger } from "@/lib/logger";

export default function BackupSettings() {
  const [busy, setBusy] = useState(null);
  const fileRef = useRef(null);

  const download = async () => {
    setBusy("backup");
    try {
      const res = await api.get("/admin/backup", { responseType: "blob", timeout: 300000 });
      const cd = res.headers["content-disposition"] || "";
      const m = cd.match(/filename="([^"]+)"/);
      const url = URL.createObjectURL(res.data);
      const a = document.createElement("a");
      a.href = url;
      a.download = m ? m[1] : "totyshop_backup.json";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast.success("Backup baixado");
    } catch (err) {
      logger.error("backup:", err);
      toast.error("Falha ao gerar backup");
    } finally {
      setBusy(null);
    }
  };

  const restore = async (file) => {
    if (!file) return;
    if (!window.confirm("Restaurar vai SUBSTITUIR os dados atuais pelos do arquivo. Continuar?")) return;
    setBusy("restore");
    try {
      const fd = new FormData();
      fd.append("file", file);
      const { data } = await api.post("/admin/restore", fd, { timeout: 300000 });
      const total = Object.values(data.restored || {}).reduce((a, b) => a + b, 0);
      toast.success(`Restaurado: ${total} registros em ${Object.keys(data.restored || {}).length} tabelas`);
    } catch (err) {
      logger.error("restore:", err);
      toast.error(err?.response?.data?.detail || "Falha ao restaurar");
    } finally {
      setBusy(null);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  return (
    <div className="border border-border bg-white p-6 space-y-4" data-testid="backup-settings">
      <div>
        <h2 className="text-lg font-display font-semibold flex items-center gap-2">
          <DatabaseBackup className="h-5 w-5 text-[#EE7B22]" /> Backup dos dados
        </h2>
        <p className="text-sm text-muted-foreground mt-1 max-w-2xl">
          Baixa um arquivo com tudo o que o sistema guardou: conexão Bling, textos originais da JohnDrop,
          tabela de preços, categorias, agenda de anúncios etc. Use para guardar uma cópia
          ou para levar os dados do seu PC para um servidor.
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={download}
          disabled={Boolean(busy)}
          className="bg-zinc-900 text-white text-sm font-medium px-4 py-2 rounded-sm hover:bg-zinc-800 disabled:opacity-50 inline-flex items-center gap-2"
        >
          {busy === "backup" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
          Baixar backup
        </button>
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          disabled={Boolean(busy)}
          className="border border-border text-sm font-medium px-4 py-2 rounded-sm hover:bg-zinc-50 disabled:opacity-50 inline-flex items-center gap-2"
        >
          {busy === "restore" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
          Restaurar de um arquivo
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="application/json,.json"
          className="hidden"
          onChange={(e) => restore(e.target.files?.[0])}
        />
      </div>
    </div>
  );
}
