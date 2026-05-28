import { useEffect, useState, useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import { endpoints } from "@/lib/api";
import { CheckCircle2, Link2, KeyRound, Save, Lock } from "lucide-react";
import { toast } from "sonner";

export default function SettingsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [bling, setBling] = useState(null);
  const [jd, setJd] = useState({ configured: false, username: "" });
  const [creds, setCreds] = useState({ username: "", password: "" });
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const blingConn = searchParams.get("bling");
    const blingErr = searchParams.get("bling_error");
    if (blingConn === "connected") {
      toast.success("Bling conectado com sucesso");
      setSearchParams({});
    } else if (blingErr) {
      toast.error("Erro Bling: " + blingErr);
      setSearchParams({});
    }
  }, [searchParams, setSearchParams]);

  const load = useCallback(async () => {
    try {
      const [b, j] = await Promise.all([
        endpoints.blingStatus(),
        endpoints.getJohnDropStatus(),
      ]);
      setBling(b.data);
      setJd(j.data);
      if (j.data.username) setCreds((c) => ({ ...c, username: j.data.username }));
    } catch (err) {
      console.error("Failed to load settings:", err);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const connectBling = async () => {
    try {
      const { data } = await endpoints.blingAuthorizeUrl();
      window.location.href = data.url;
    } catch (e) {
      toast.error("Erro ao gerar URL: " + e.message);
    }
  };

  const disconnectBling = async () => {
    await endpoints.blingDisconnect();
    toast.info("Bling desconectado");
    load();
  };

  const saveJd = async (e) => {
    e.preventDefault();
    if (!creds.username || !creds.password) {
      toast.error("Preencha usuário e senha");
      return;
    }
    setSaving(true);
    try {
      await endpoints.setJohnDropCreds(creds.username, creds.password);
      toast.success("Credenciais JohnDrop salvas");
      load();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <div className="label-overline mb-1">Integrações</div>
        <h1 className="font-display text-3xl font-bold tracking-tighter">Configurações</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Conecte sua conta Bling e cadastre suas credenciais do fornecedor JohnDrop.
        </p>
      </div>

      {/* Bling */}
      <div className="border border-border bg-white p-6 space-y-4">
        <div className="flex items-start justify-between">
          <div className="flex items-start gap-3">
            <div className="h-10 w-10 rounded-sm bg-[#0066FF] grid place-items-center text-white font-display font-bold">
              B
            </div>
            <div>
              <div className="font-display text-lg font-bold tracking-tight">Bling ERP — API v3</div>
              <div className="text-xs text-muted-foreground">OAuth 2.0 com refresh automático</div>
            </div>
          </div>
          {bling?.connected ? (
            <span className="text-xs px-3 py-1 bg-emerald-100 text-emerald-700 border border-emerald-300 rounded-sm uppercase font-semibold inline-flex items-center gap-1.5">
              <CheckCircle2 className="h-3.5 w-3.5" /> Conectado
            </span>
          ) : (
            <span className="text-xs px-3 py-1 bg-zinc-100 text-zinc-700 border border-zinc-300 rounded-sm uppercase font-semibold">
              Desconectado
            </span>
          )}
        </div>

        {bling?.connected ? (
          <div className="space-y-2">
            <div className="text-xs">
              <span className="label-overline mr-2">Expira em</span>
              <span className="font-mono">{bling.expires_at && new Date(bling.expires_at).toLocaleString("pt-BR")}</span>
            </div>
            <button
              data-testid="disconnect-bling-btn"
              onClick={disconnectBling}
              className="text-sm border border-rose-300 text-rose-600 px-4 py-2 rounded-sm hover:bg-rose-50"
            >
              Desconectar
            </button>
          </div>
        ) : (
          <button
            data-testid="connect-bling-btn"
            onClick={connectBling}
            className="bg-[#002FA7] text-white text-sm font-medium px-5 py-2.5 rounded-sm hover:bg-[#00227A] inline-flex items-center gap-2"
          >
            <Link2 className="h-4 w-4" /> Conectar Bling
          </button>
        )}
      </div>

      {/* JohnDrop credentials */}
      <form onSubmit={saveJd} className="border border-border bg-white p-6 space-y-4">
        <div className="flex items-start justify-between">
          <div className="flex items-start gap-3">
            <div className="h-10 w-10 rounded-sm bg-[#7C3AED] grid place-items-center text-white font-display font-bold">
              J
            </div>
            <div>
              <div className="font-display text-lg font-bold tracking-tight">Fornecedor JohnDrop</div>
              <div className="text-xs text-muted-foreground">app.jonhdrop.com.br</div>
            </div>
          </div>
          {jd?.configured && (
            <span className="text-xs px-3 py-1 bg-emerald-100 text-emerald-700 border border-emerald-300 rounded-sm uppercase font-semibold inline-flex items-center gap-1.5">
              <CheckCircle2 className="h-3.5 w-3.5" /> Configurado
            </span>
          )}
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="label-overline">Usuário (e-mail)</label>
            <input
              data-testid="jd-username-input"
              type="email"
              value={creds.username}
              onChange={(e) => setCreds((c) => ({ ...c, username: e.target.value }))}
              placeholder="email@exemplo.com"
              className="w-full text-sm border border-border rounded-sm px-3 py-2.5 mt-1 focus:outline-none focus:ring-2 focus:ring-[#002FA7]"
            />
          </div>
          <div>
            <label className="label-overline">Senha</label>
            <input
              data-testid="jd-password-input"
              type="password"
              value={creds.password}
              onChange={(e) => setCreds((c) => ({ ...c, password: e.target.value }))}
              placeholder={jd.configured ? "•••••••• (preencha para alterar)" : "Senha JohnDrop"}
              className="w-full text-sm border border-border rounded-sm px-3 py-2.5 mt-1 focus:outline-none focus:ring-2 focus:ring-[#002FA7]"
            />
          </div>
        </div>

        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Lock className="h-3.5 w-3.5" />
          Credenciais são armazenadas apenas no MongoDB privado da aplicação.
        </div>

        <button
          data-testid="save-jd-btn"
          type="submit"
          disabled={saving}
          className="bg-[#002FA7] text-white text-sm font-medium px-5 py-2.5 rounded-sm hover:bg-[#00227A] disabled:opacity-50 inline-flex items-center gap-2"
        >
          <Save className="h-4 w-4" />
          {saving ? "Salvando…" : "Salvar Credenciais"}
        </button>
      </form>

      {/* App info */}
      <div className="border border-border bg-white p-6">
        <div className="flex items-center gap-2 mb-3">
          <KeyRound className="h-4 w-4 text-zinc-500" />
          <span className="label-overline">Configuração Bling OAuth</span>
        </div>
        <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2 text-xs font-mono">
          <dt className="text-muted-foreground">Client ID</dt>
          <dd className="truncate">05b3f679e6cfc180fa62bcf254932e182aa39ce7</dd>
          <dt className="text-muted-foreground">Redirect URI</dt>
          <dd className="truncate break-all">{process.env.REACT_APP_BACKEND_URL}/api/bling/callback</dd>
          <dt className="text-muted-foreground">Scope</dt>
          <dd>produtos / categorias</dd>
        </dl>
        <p className="text-xs text-muted-foreground mt-3">
          Cadastre exatamente este Redirect URI no painel Bling antes de conectar.
        </p>
      </div>
    </div>
  );
}
