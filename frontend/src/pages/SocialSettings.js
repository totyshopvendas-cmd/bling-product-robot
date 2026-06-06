import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { logger } from "@/lib/logger";
import { Save, CheckCircle2, AlertTriangle, Loader2, Eye, EyeOff, Shield, ExternalLink } from "lucide-react";
import { toast } from "sonner";

export default function SocialSettingsPage() {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [configured, setConfigured] = useState(false);
  const [info, setInfo] = useState(null);
  const [form, setForm] = useState({
    app_id: "",
    app_secret: "",
    page_access_token: "",
  });
  const [showSecret, setShowSecret] = useState(false);
  const [showToken, setShowToken] = useState(false);
  const [testResult, setTestResult] = useState(null);

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/social/meta/credentials");
      setConfigured(Boolean(data.configured));
      setInfo(data);
      if (data.configured) {
        setForm({ app_id: data.app_id || "", app_secret: "", page_access_token: "" });
      }
    } catch (e) {
      logger.error("load meta creds:", e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const save = async () => {
    if (!form.app_id || !form.app_secret || !form.page_access_token) {
      toast.error("Preencha App ID, Chave Secreta e Token");
      return;
    }
    setSaving(true);
    try {
      await api.post("/social/meta/credentials", form);
      toast.success("Credenciais salvas (criptografadas)");
      setForm({ ...form, app_secret: "", page_access_token: "" });
      await load();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Erro ao salvar");
    } finally {
      setSaving(false);
    }
  };

  const test = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const { data } = await api.post("/social/meta/test");
      setTestResult(data);
      if (data.ok) {
        toast.success(`Conectado em ${data.page_name}!`);
        await load();
      } else {
        toast.error(data.error || "Falha na conexão");
      }
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Falha ao testar");
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="space-y-6 max-w-3xl">
      <div>
        <div className="label-overline mb-1">Integrações</div>
        <h1 className="font-display text-3xl font-bold tracking-tighter">
          Redes Sociais — Meta (Instagram + Facebook)
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Conecte sua conta Meta Business para postar produtos automaticamente
          no <strong>@totyshop4</strong> (Instagram) e <strong>página TotyShop</strong> (Facebook).
        </p>
      </div>

      {/* Security notice */}
      <div className="border border-amber-300 bg-amber-50 p-4 flex gap-3">
        <Shield className="h-5 w-5 text-amber-700 flex-shrink-0 mt-0.5" />
        <div className="text-sm text-amber-900">
          <strong>Segurança:</strong> a Chave Secreta e o Token são gravados criptografados no banco.
          Nunca aparecem em logs nem voltam pela API depois de salvos — apenas marcadores <code>••••••••</code>.
        </div>
      </div>

      {/* Status atual */}
      {!loading && configured && (
        <div
          data-testid="meta-status-card"
          className="border border-emerald-300 bg-emerald-50 p-4 space-y-2"
        >
          <div className="flex items-center gap-2 font-semibold text-emerald-900">
            <CheckCircle2 className="h-5 w-5" /> Credenciais configuradas
          </div>
          <div className="text-xs text-emerald-900 space-y-1 font-mono">
            <div>App ID: {info.app_id}</div>
            <div>Página Facebook: {info.facebook_page_id || "— (clique em Testar para detectar)"}</div>
            <div>Instagram Business: {info.instagram_business_id || "— (clique em Testar para detectar)"}</div>
          </div>
        </div>
      )}

      {/* Form */}
      <div className="border border-border bg-white p-5 space-y-4">
        <h2 className="font-semibold">Credenciais Meta</h2>

        <div className="flex flex-col gap-1">
          <label className="label-overline">App ID</label>
          <input
            data-testid="meta-app-id"
            value={form.app_id}
            onChange={(e) => setForm({ ...form, app_id: e.target.value })}
            placeholder="869902865549451"
            className="text-sm border border-border rounded-sm px-3 py-2 font-mono focus:outline-none focus:ring-2 focus:ring-[#EE7B22]"
          />
          <span className="text-xs text-muted-foreground">
            ID público que aparece no topo do painel do app no developers.facebook.com
          </span>
        </div>

        <div className="flex flex-col gap-1">
          <label className="label-overline">Chave Secreta do App</label>
          <div className="flex gap-2">
            <input
              data-testid="meta-app-secret"
              type={showSecret ? "text" : "password"}
              value={form.app_secret}
              onChange={(e) => setForm({ ...form, app_secret: e.target.value })}
              placeholder={configured ? "••••••••  (deixe vazio para manter)" : "Cole a Chave Secreta aqui"}
              className="text-sm border border-border rounded-sm px-3 py-2 font-mono flex-1 focus:outline-none focus:ring-2 focus:ring-[#EE7B22]"
            />
            <button
              type="button"
              onClick={() => setShowSecret(!showSecret)}
              className="border border-border px-3 hover:bg-zinc-50"
              aria-label="Mostrar/ocultar"
            >
              {showSecret ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
          </div>
          <span className="text-xs text-muted-foreground">
            Configurações → Básico → &quot;Chave Secreta do App&quot;
          </span>
        </div>

        <div className="flex flex-col gap-1">
          <label className="label-overline">Token de Acesso da Página</label>
          <div className="flex gap-2">
            <input
              data-testid="meta-page-token"
              type={showToken ? "text" : "password"}
              value={form.page_access_token}
              onChange={(e) => setForm({ ...form, page_access_token: e.target.value })}
              placeholder={configured ? "••••••••  (deixe vazio para manter)" : "Token EAA... gerado no Graph API Explorer"}
              className="text-sm border border-border rounded-sm px-3 py-2 font-mono flex-1 focus:outline-none focus:ring-2 focus:ring-[#EE7B22]"
            />
            <button
              type="button"
              onClick={() => setShowToken(!showToken)}
              className="border border-border px-3 hover:bg-zinc-50"
              aria-label="Mostrar/ocultar"
            >
              {showToken ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
          </div>
          <span className="text-xs text-muted-foreground">
            Gere em{" "}
            <a
              href="https://developers.facebook.com/tools/explorer/"
              target="_blank" rel="noreferrer"
              className="text-[#EE7B22] inline-flex items-center gap-1 hover:underline"
            >
              Graph API Explorer <ExternalLink className="h-3 w-3" />
            </a>{" "}
            com permissões: pages_show_list, pages_manage_posts, pages_read_engagement,
            instagram_basic, instagram_content_publish, business_management
          </span>
        </div>

        <div className="flex gap-2 pt-2">
          <button
            data-testid="save-meta-creds"
            onClick={save}
            disabled={saving}
            className="bg-[#EE7B22] text-white text-sm font-medium px-4 py-2 rounded-sm hover:bg-[#C9651A] disabled:opacity-40 inline-flex items-center gap-2"
          >
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            Salvar credenciais
          </button>
          <button
            data-testid="test-meta-conn"
            onClick={test}
            disabled={testing || !configured}
            className="bg-zinc-900 text-white text-sm font-medium px-4 py-2 rounded-sm hover:bg-zinc-700 disabled:opacity-40 inline-flex items-center gap-2"
          >
            {testing ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
            Testar conexão
          </button>
        </div>
      </div>

      {/* Test result */}
      {testResult && (
        <div
          data-testid="test-result-card"
          className={`border p-4 ${
            testResult.ok ? "border-emerald-300 bg-emerald-50 text-emerald-900"
                         : "border-rose-300 bg-rose-50 text-rose-900"
          }`}
        >
          <div className="flex items-center gap-2 font-semibold mb-2">
            {testResult.ok ? <CheckCircle2 className="h-5 w-5" /> : <AlertTriangle className="h-5 w-5" />}
            {testResult.ok ? "Conexão validada" : "Falha na conexão"}
          </div>
          {testResult.ok ? (
            <div className="text-sm space-y-1">
              <div>Página: <strong>{testResult.page_name}</strong> (id={testResult.page_id})</div>
              <div>
                Instagram Business:{" "}
                {testResult.instagram_linked
                  ? <strong>vinculado (id={testResult.instagram_business_id})</strong>
                  : <span className="text-amber-700">não vinculado — vincule no Meta Business Suite</span>}
              </div>
            </div>
          ) : (
            <div className="text-sm font-mono">{testResult.error}</div>
          )}
        </div>
      )}
    </div>
  );
}
