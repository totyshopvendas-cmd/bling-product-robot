import { useEffect, useState, useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import { endpoints } from "@/lib/api";
import { logger } from "@/lib/logger";
import { CheckCircle2, Link2, Save, Unplug, AlertTriangle } from "lucide-react";
import { toast } from "sonner";

export default function SettingsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [cfg, setCfg] = useState(null);
  const [jd, setJd] = useState({ configured: false, username: "" });
  const [creds, setCreds] = useState({ username: "", password: "" });
  const [blingSecret, setBlingSecret] = useState("");
  const [saving, setSaving] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [lastError, setLastError] = useState("");

  useEffect(() => {
    const blingConn = searchParams.get("bling");
    const blingErr = searchParams.get("bling_error");
    if (blingConn === "connected") {
      toast.success("Bling conectado");
      setLastError("");
      setSearchParams({});
    } else if (blingErr) {
      setLastError(blingErr);
      toast.error("Não foi possível conectar o Bling");
      setSearchParams({});
    }
  }, [searchParams, setSearchParams]);

  const load = useCallback(async () => {
    try {
      const origin = window.location.origin;
      const [b, j] = await Promise.all([
        endpoints.blingOAuthConfig(origin),
        endpoints.getJohnDropStatus(),
      ]);
      setCfg(b.data);
      setJd(j.data);
      if (j.data.username) setCreds((c) => ({ ...c, username: j.data.username }));
    } catch (err) {
      logger.error("Failed to load settings:", err);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const connectBling = async () => {
    setConnecting(true);
    setLastError("");
    try {
      if (blingSecret.trim()) {
        await endpoints.saveBlingApp(cfg?.client_id || "", blingSecret.trim());
      }
      const { data } = await endpoints.blingAuthorizeUrl(window.location.origin);
      const authUrl = data?.url;
      if (!authUrl) throw new Error("Não foi possível abrir o Bling");
      toast.info("Abrindo o Bling… autorize e você volta para cá");
      window.location.assign(authUrl);
    } catch (e) {
      const detail = e.response?.data?.detail || e.message;
      setLastError(typeof detail === "string" ? detail : "Falha ao conectar");
      toast.error("Falha ao conectar o Bling");
      setConnecting(false);
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
      toast.error("Preencha usuário e senha da JohnDrop");
      return;
    }
    setSaving(true);
    try {
      await endpoints.setJohnDropCreds(creds.username, creds.password);
      toast.success("JohnDrop salva — o robô já pode entrar");
      load();
    } finally {
      setSaving(false);
    }
  };

  const keysOk = Boolean(cfg?.configured);
  const needSecret = lastError?.toLowerCase().includes("secret") || lastError?.toLowerCase().includes("client id");

  return (
    <div className="space-y-6">
      <div>
        <div className="label-overline mb-1">Integrações</div>
        <h1 className="font-display text-3xl font-bold tracking-tighter">Configurações</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Um clique no Bling. A JohnDrop usa o usuário e a senha salvos aqui.
        </p>
      </div>

      <div className="border border-border bg-white p-6 space-y-4">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-start gap-3">
            <div className="h-10 w-10 rounded-sm bg-[#0066FF] grid place-items-center text-white font-display font-bold">B</div>
            <div>
              <div className="font-display text-lg font-bold tracking-tight">Bling</div>
              <div className="text-xs text-muted-foreground">Fica conectado depois da primeira autorização</div>
            </div>
          </div>
          {cfg == null ? (
            <span className="text-xs px-3 py-1 bg-zinc-100 text-zinc-700 border border-zinc-300 rounded-sm uppercase font-semibold">
              Carregando…
            </span>
          ) : cfg.connected ? (
            <span className="text-xs px-3 py-1 bg-emerald-100 text-emerald-700 border border-emerald-300 rounded-sm uppercase font-semibold inline-flex items-center gap-1.5">
              <CheckCircle2 className="h-3.5 w-3.5" /> Conectado
            </span>
          ) : (
            <span className="text-xs px-3 py-1 bg-zinc-100 text-zinc-700 border border-zinc-300 rounded-sm uppercase font-semibold">
              Desconectado
            </span>
          )}
        </div>

        {cfg?.connected && (
          <div className="bg-emerald-50 border border-emerald-200 rounded-sm p-4 text-sm text-emerald-900">
            Bling conectado. Pode ligar o TotyShop de novo que a conexão permanece.
          </div>
        )}

        {lastError && (
          <div className="flex items-start gap-2 text-sm text-rose-800 bg-rose-50 border border-rose-200 rounded-sm p-3">
            <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
            <p>{lastError}</p>
          </div>
        )}

        {cfg?.connected ? (
          <button
            data-testid="disconnect-bling-btn"
            onClick={disconnectBling}
            className="text-sm border border-rose-300 text-rose-600 px-4 py-2 rounded-sm hover:bg-rose-50 inline-flex items-center gap-2"
          >
            <Unplug className="h-4 w-4" /> Desconectar
          </button>
        ) : (
          <div className="space-y-3">
            {needSecret && (
              <div>
                <label className="label-overline">Client Secret do Bling</label>
                <input
                  type="password"
                  value={blingSecret}
                  onChange={(e) => setBlingSecret(e.target.value)}
                  placeholder="Cole o Client Secret uma vez"
                  className="w-full text-sm border border-border rounded-sm px-3 py-2.5 mt-1 font-mono focus:outline-none focus:ring-2 focus:ring-[#EE7B22]"
                />
              </div>
            )}
            <button
              data-testid="connect-bling-btn"
              onClick={connectBling}
              disabled={connecting || !keysOk}
              className="bg-[#EE7B22] text-white text-sm font-medium px-5 py-2.5 rounded-sm hover:bg-[#C9651A] disabled:opacity-50 inline-flex items-center gap-2"
            >
              <Link2 className="h-4 w-4" />
              {connecting ? "Abrindo o Bling…" : "Conectar Bling"}
            </button>
            {!keysOk && (
              <p className="text-xs text-rose-700">
                Falta Client ID ou Secret no arquivo backend\.env. Copie as duas linhas do Bling para lá, salve e rode o .bat de novo.
              </p>
            )}
          </div>
        )}
      </div>

      <form onSubmit={saveJd} className="border border-border bg-white p-6 space-y-4">
        <div className="flex items-start justify-between">
          <div className="flex items-start gap-3">
            <div className="h-10 w-10 rounded-sm bg-[#7C3AED] grid place-items-center text-white font-display font-bold">J</div>
            <div>
              <div className="font-display text-lg font-bold tracking-tight">JohnDrop</div>
              <div className="text-xs text-muted-foreground">O robô entra com este usuário (não usa a aba do Chrome)</div>
            </div>
          </div>
          {jd?.configured && (
            <span className="text-xs px-3 py-1 bg-emerald-100 text-emerald-700 border border-emerald-300 rounded-sm uppercase font-semibold inline-flex items-center gap-1.5">
              <CheckCircle2 className="h-3.5 w-3.5" /> Pronto
            </span>
          )}
        </div>

        {jd?.configured && (
          <div className="bg-emerald-50 border border-emerald-200 rounded-sm p-4 text-sm text-emerald-900">
            JohnDrop salva. O robô usa {jd.username}.
          </div>
        )}

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="label-overline">Usuário (e-mail)</label>
            <input
              data-testid="jd-username-input"
              type="email"
              name="johndrop-username"
              autoComplete="off"
              value={creds.username}
              onChange={(e) => setCreds((c) => ({ ...c, username: e.target.value }))}
              placeholder="email@exemplo.com"
              className="w-full text-sm border border-border rounded-sm px-3 py-2.5 mt-1 focus:outline-none focus:ring-2 focus:ring-[#EE7B22]"
            />
          </div>
          <div>
            <label className="label-overline">Senha</label>
            <input
              data-testid="jd-password-input"
              type="password"
              value={creds.password}
              onChange={(e) => setCreds((c) => ({ ...c, password: e.target.value }))}
              placeholder={jd.configured ? "••••••••" : "Senha JohnDrop"}
              className="w-full text-sm border border-border rounded-sm px-3 py-2.5 mt-1 focus:outline-none focus:ring-2 focus:ring-[#EE7B22]"
            />
          </div>
        </div>

        <button
          data-testid="save-jd-btn"
          type="submit"
          disabled={saving}
          className="bg-[#EE7B22] text-white text-sm font-medium px-5 py-2.5 rounded-sm hover:bg-[#C9651A] disabled:opacity-50 inline-flex items-center gap-2"
        >
          <Save className="h-4 w-4" />
          {saving ? "Salvando…" : "Salvar JohnDrop"}
        </button>
      </form>
    </div>
  );
}
