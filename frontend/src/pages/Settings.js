import { useEffect, useState, useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import { endpoints } from "@/lib/api";
import { logger } from "@/lib/logger";
import {
  CheckCircle2, Link2, KeyRound, Save, Lock, Copy, ExternalLink,
  Unplug, RefreshCw, AlertTriangle,
} from "lucide-react";
import { toast } from "sonner";

export default function SettingsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [cfg, setCfg] = useState(null);
  const [jd, setJd] = useState({ configured: false, username: "" });
  const [creds, setCreds] = useState({ username: "", password: "" });
  const [blingApp, setBlingApp] = useState({ client_id: "", client_secret: "" });
  const [saving, setSaving] = useState(false);
  const [savingBling, setSavingBling] = useState(false);
  const [testing, setTesting] = useState(false);
  const [lastError, setLastError] = useState("");
  const [oauthUrl, setOauthUrl] = useState("");
  const [openUrl, setOpenUrl] = useState("");

  useEffect(() => {
    const blingConn = searchParams.get("bling");
    const blingErr = searchParams.get("bling_error");
    if (blingConn === "connected") {
      toast.success("Bling conectado com sucesso");
      setLastError("");
      setSearchParams({});
    } else if (blingErr) {
      setLastError(blingErr);
      toast.error(blingErr);
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
      if (b.data?.client_id) {
        setBlingApp((c) => ({ ...c, client_id: b.data.client_id }));
      }
      setJd(j.data);
      if (j.data.username) setCreds((c) => ({ ...c, username: j.data.username }));
    } catch (err) {
      logger.error("Failed to load settings:", err);
    }
  }, []);

  const prepareOAuth = useCallback(async () => {
    try {
      const { data } = await endpoints.blingAuthorizeUrl(window.location.origin);
      const shortUrl = data?.open_url || data?.url;
      if (!shortUrl) return null;
      setOpenUrl(shortUrl);
      setOauthUrl(data.url || shortUrl);
      return { shortUrl, authUrl: data.url || shortUrl };
    } catch (err) {
      logger.error("oauth prepare:", err);
      return null;
    }
  }, []);

  useEffect(() => { load(); prepareOAuth(); }, [load, prepareOAuth]);

  const downloadLoginFile = (authUrl) => {
    if (!authUrl) return;
    const html = `<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><title>Login Bling</title>
<script>location.replace(${JSON.stringify(authUrl)});</script>
</head><body><p><a href=${JSON.stringify(authUrl)}>Abrir login do Bling</a></p></body></html>`;
    const blob = new Blob([html], { type: "text/html;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "conectar-bling.html";
    document.body.appendChild(a);
    a.click();
    a.remove();
  };

  const copyText = async (text, okMsg) => {
    if (!text) return false;
    try {
      await navigator.clipboard.writeText(text);
      toast.success(okMsg);
      return true;
    } catch {
      try {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.setAttribute("readonly", "");
        ta.style.position = "fixed";
        ta.style.left = "-9999px";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        ta.remove();
        toast.success(okMsg);
        return true;
      } catch {
        toast.error("Selecione o texto e copie com Ctrl+C");
        return false;
      }
    }
  };

  const copyUri = async () => {
    await copyText(cfg?.redirect_uri, "Link de redirecionamento copiado");
  };

  const saveBlingApp = async (e) => {
    e.preventDefault();
    if (!blingApp.client_id.trim()) {
      toast.error("Informe o Client ID do aplicativo Bling");
      return;
    }
    if (!blingApp.client_secret.trim() && !cfg?.has_secret) {
      toast.error("Informe o Client Secret");
      return;
    }
    setSavingBling(true);
    try {
      await endpoints.saveBlingApp(blingApp.client_id.trim(), blingApp.client_secret);
      toast.success("Credenciais do aplicativo Bling salvas neste servidor");
      setBlingApp((c) => ({ ...c, client_secret: "" }));
      await load();
    } catch (err) {
      toast.error(err.response?.data?.detail || err.message);
    } finally {
      setSavingBling(false);
    }
  };

  const startOAuthPoll = () => {
    const started = Date.now();
    const timer = setInterval(async () => {
      try {
        const res = await endpoints.blingOAuthConfig(window.location.origin);
          if (res.data?.connected) {
            clearInterval(timer);
            toast.success("Bling conectado com sucesso");
            setOauthUrl("");
            setOpenUrl("");
            load();
          }
      } catch {
        /* keep polling */
      }
      if (Date.now() - started > 5 * 60 * 1000) clearInterval(timer);
    }, 3000);
  };

  const connectBling = async () => {
    try {
      setLastError("");
      if (oauthUrl) {
        downloadLoginFile(oauthUrl);
        copyText(
          openUrl || oauthUrl,
          "Olhe embaixo do Chrome: clique em conectar-bling.html. Ou cole o endereço numa nova guia (+).",
        );
        startOAuthPoll();
        return;
      }
      const prepared = await prepareOAuth();
      const authUrl = prepared?.authUrl || "";
      const shortUrl = prepared?.shortUrl || "";
      if (!authUrl) throw new Error("URL OAuth vazia");
      downloadLoginFile(authUrl);
      await copyText(
        shortUrl,
        "Olhe embaixo do Chrome: clique em conectar-bling.html. Ou cole o endereço numa nova guia (+).",
      );
      startOAuthPoll();
    } catch (e) {
      const detail = e.response?.data?.detail || e.message;
      toast.error("Erro ao gerar URL: " + detail);
    }
  };

  const disconnectBling = async () => {
    await endpoints.blingDisconnect();
    toast.info("Bling desconectado");
    load();
  };

  const testBling = async () => {
    setTesting(true);
    try {
      const { data } = await endpoints.blingPing();
      toast.success(`Conexão OK — API respondeu (${data.items ?? 0} produto(s) na amostra)`);
    } catch (e) {
      toast.error(e.response?.data?.detail || e.message);
    } finally {
      setTesting(false);
    }
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

      <div className="border border-border bg-white p-6 space-y-4">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-start gap-3">
            <div className="h-10 w-10 rounded-sm bg-[#0066FF] grid place-items-center text-white font-display font-bold">
              B
            </div>
            <div>
              <div className="font-display text-lg font-bold tracking-tight">Bling ERP — API v3</div>
              <div className="text-xs text-muted-foreground">OAuth 2.0 + JWT (enable-jwt)</div>
            </div>
          </div>
          {cfg?.connected ? (
            <span className="text-xs px-3 py-1 bg-emerald-100 text-emerald-700 border border-emerald-300 rounded-sm uppercase font-semibold inline-flex items-center gap-1.5">
              <CheckCircle2 className="h-3.5 w-3.5" /> Conectado
            </span>
          ) : (
            <span className="text-xs px-3 py-1 bg-zinc-100 text-zinc-700 border border-zinc-300 rounded-sm uppercase font-semibold">
              Desconectado
            </span>
          )}
        </div>

        {lastError && (
          <div
            data-testid="bling-error-banner"
            className="flex items-start gap-2 text-sm text-rose-800 bg-rose-50 border border-rose-200 rounded-sm p-3"
          >
            <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
            <div>
              <div className="font-semibold mb-1">Não foi possível conectar ao Bling</div>
              <p>{lastError}</p>
            </div>
          </div>
        )}

        {cfg?.issues?.length > 0 && (
          <div className="flex items-start gap-2 text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded-sm p-3">
            <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
            <ul className="list-disc pl-4 space-y-1">
              {cfg.issues.map((issue) => <li key={issue}>{issue}</li>)}
            </ul>
          </div>
        )}

        <ol className="text-sm text-zinc-700 list-decimal pl-5 space-y-1 bg-zinc-50 border border-border rounded-sm p-4">
          <li>No Bling, menu esquerdo → <strong>Dados básicos</strong>. Cole o <strong>Link de redirecionamento</strong> desta tela e salve.</li>
          <li>Clique em <strong>Gerar link de login</strong> — a Arena <strong>não abre aba sozinha</strong>.</li>
          <li>No topo do Chrome clique no <strong>+</strong> (Nova guia), cole o endereço laranja e aperte Enter. Autorize no Bling e volte aqui.</li>
        </ol>

        {cfg?.connected && (
          <div className="space-y-2">
            <div className="text-xs">
              <span className="label-overline mr-2">Expira em</span>
              <span className="font-mono">
                {cfg.expires_at && new Date(cfg.expires_at).toLocaleString("pt-BR")}
              </span>
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                data-testid="test-bling-btn"
                onClick={testBling}
                disabled={testing}
                className="text-sm border border-border px-4 py-2 rounded-sm hover:bg-zinc-50 inline-flex items-center gap-2 disabled:opacity-50"
              >
                <RefreshCw className={`h-4 w-4 ${testing ? "animate-spin" : ""}`} />
                {testing ? "Testando…" : "Testar conexão"}
              </button>
              <button
                data-testid="disconnect-bling-btn"
                onClick={disconnectBling}
                className="text-sm border border-rose-300 text-rose-600 px-4 py-2 rounded-sm hover:bg-rose-50 inline-flex items-center gap-2"
              >
                <Unplug className="h-4 w-4" /> Desconectar
              </button>
            </div>
          </div>
        )}

        <form onSubmit={saveBlingApp} className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2">
          <div>
            <label className="label-overline">Client ID</label>
            <input
              data-testid="bling-client-id-input"
              type="text"
              value={blingApp.client_id}
              onChange={(e) => setBlingApp((c) => ({ ...c, client_id: e.target.value }))}
              placeholder="Client ID do aplicativo Bling"
              className="w-full text-sm border border-border rounded-sm px-3 py-2.5 mt-1 font-mono focus:outline-none focus:ring-2 focus:ring-[#EE7B22]"
            />
          </div>
          <div>
            <label className="label-overline">Client Secret</label>
            <input
              data-testid="bling-client-secret-input"
              type="password"
              value={blingApp.client_secret}
              onChange={(e) => setBlingApp((c) => ({ ...c, client_secret: e.target.value }))}
              placeholder={cfg?.has_secret ? "•••••••• (preencha para alterar)" : "Client Secret"}
              className="w-full text-sm border border-border rounded-sm px-3 py-2.5 mt-1 font-mono focus:outline-none focus:ring-2 focus:ring-[#EE7B22]"
            />
          </div>
          <div className="md:col-span-2">
            <button
              data-testid="save-bling-app-btn"
              type="submit"
              disabled={savingBling}
              className="text-sm border border-border px-4 py-2 rounded-sm hover:bg-zinc-50 inline-flex items-center gap-2 disabled:opacity-50"
            >
              <Save className="h-4 w-4" />
              {savingBling ? "Salvando…" : "Salvar aplicativo Bling"}
            </button>
          </div>
        </form>

        <div className="border border-dashed border-border rounded-sm p-4 space-y-3 bg-zinc-50">
          <div className="label-overline">Link de redirecionamento (obrigatório no Bling)</div>
          <p className="text-xs text-muted-foreground">
            No Bling: Central de Extensões → Área do Integrador → seu aplicativo →
            {" "}<strong>Link de redirecionamento</strong>. Cole exatamente este valor e salve
            antes de clicar em Conectar.
          </p>
          <div className="flex flex-col sm:flex-row gap-2">
            <code
              data-testid="bling-redirect-uri"
              className="flex-1 text-xs bg-white border border-border rounded-sm px-3 py-2 break-all"
            >
              {cfg?.redirect_uri || "carregando…"}
            </code>
            <button
              type="button"
              data-testid="copy-redirect-uri-btn"
              onClick={copyUri}
              className="text-sm border border-border px-3 py-2 rounded-sm hover:bg-white inline-flex items-center justify-center gap-2"
            >
              <Copy className="h-4 w-4" /> Copiar
            </button>
          </div>
          <a
            href="https://www.bling.com.br/central.extensoes.php"
            target="_blank"
            rel="noreferrer"
            className="text-xs text-[#002FA7] inline-flex items-center gap-1 hover:underline"
          >
            Abrir Central de Extensões do Bling <ExternalLink className="h-3 w-3" />
          </a>
        </div>

        {!cfg?.connected && (
          <div className="space-y-3">
            <button
              data-testid="connect-bling-btn"
              onClick={connectBling}
              className="bg-[#EE7B22] text-white text-sm font-medium px-5 py-2.5 rounded-sm hover:bg-[#C9651A] inline-flex items-center gap-2"
            >
              <Link2 className="h-4 w-4" /> Baixar login do Bling
            </button>
            {openUrl && (
              <div
                data-testid="bling-oauth-fallback"
                className="border-2 border-[#EE7B22] bg-amber-50 rounded-sm p-4 space-y-3"
              >
                <div className="font-semibold text-amber-950">
                  A Arena não abre aba. Use o arquivo baixado ou cole este endereço:
                </div>
                <ol className="text-sm text-zinc-800 list-decimal pl-5 space-y-1">
                  <li>Embaixo do Chrome, clique em <strong>conectar-bling.html</strong>.</li>
                  <li>Ou clique no <strong>+</strong> no topo do Chrome, cole (Ctrl+V) e Enter.</li>
                  <li>Autorize no Bling e volte nesta tela.</li>
                </ol>
                <input
                  data-testid="bling-oauth-url"
                  readOnly
                  value={openUrl}
                  onClick={(e) => {
                    e.currentTarget.select();
                    copyText(openUrl, "Endereço copiado");
                  }}
                  className="w-full text-sm font-mono bg-white border-2 border-[#EE7B22] rounded-sm px-3 py-3"
                />
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    data-testid="copy-oauth-url-btn"
                    onClick={() => copyText(openUrl, "Endereço copiado")}
                    className="text-sm bg-[#EE7B22] text-white px-4 py-2 rounded-sm hover:bg-[#C9651A] inline-flex items-center gap-2"
                  >
                    <Copy className="h-4 w-4" /> Copiar endereço
                  </button>
                  {openUrl && (
                    <a
                      data-testid="bling-oauth-download-link"
                      href={`${openUrl}${openUrl.includes("?") ? "&" : "?"}dl=1`}
                      download="conectar-bling.html"
                      className="text-sm border border-border bg-white px-4 py-2 rounded-sm hover:bg-zinc-50 inline-flex items-center gap-2"
                    >
                      Baixar de novo
                    </a>
                  )}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

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
              placeholder={jd.configured ? "•••••••• (preencha para alterar)" : "Senha JohnDrop"}
              className="w-full text-sm border border-border rounded-sm px-3 py-2.5 mt-1 focus:outline-none focus:ring-2 focus:ring-[#EE7B22]"
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
          className="bg-[#EE7B22] text-white text-sm font-medium px-5 py-2.5 rounded-sm hover:bg-[#C9651A] disabled:opacity-50 inline-flex items-center gap-2"
        >
          <Save className="h-4 w-4" />
          {saving ? "Salvando…" : "Salvar Credenciais"}
        </button>
      </form>

      <div className="border border-border bg-white p-6">
        <div className="flex items-center gap-2 mb-3">
          <KeyRound className="h-4 w-4 text-zinc-500" />
          <span className="label-overline">Diagnóstico OAuth</span>
        </div>
        <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2 text-xs font-mono">
          <dt className="text-muted-foreground">Authorize</dt>
          <dd className="truncate break-all">{cfg?.authorize_url}</dd>
          <dt className="text-muted-foreground">Token</dt>
          <dd className="truncate break-all">{cfg?.token_url}</dd>
          <dt className="text-muted-foreground">API</dt>
          <dd className="truncate break-all">{cfg?.api_base_url}</dd>
          <dt className="text-muted-foreground">Origem das chaves</dt>
          <dd>{cfg?.source === "database" ? "banco local" : cfg?.source === "env" ? "arquivo .env" : "não definido"}</dd>
        </dl>
      </div>
    </div>
  );
}
