import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { logger } from "@/lib/logger";
import { Save, CheckCircle2, AlertTriangle, Loader2, Eye, EyeOff, Shield, ExternalLink, List as ListIcon } from "lucide-react";
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
  const [exchanging, setExchanging] = useState(false);
  const [loadingPages, setLoadingPages] = useState(false);
  const [pageSelectorOpen, setPageSelectorOpen] = useState(false);
  const [availablePages, setAvailablePages] = useState([]);

  const openPageSelector = async () => {
    setLoadingPages(true);
    setPageSelectorOpen(true);
    try {
      const { data } = await api.get("/social/meta/pages");
      if (data.ok === false) {
        toast.error(data.error || "Falha ao listar páginas");
        setAvailablePages([]);
      } else {
        setAvailablePages(data.pages || []);
      }
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Erro ao listar páginas");
      setAvailablePages([]);
    } finally {
      setLoadingPages(false);
    }
  };

  const selectPage = async (pageId) => {
    try {
      const { data } = await api.post("/social/meta/select-page", { facebook_page_id: pageId });
      toast.success(`Página selecionada: ${data.page_name}${data.instagram_linked ? " (IG vinculado)" : ""}`);
      setPageSelectorOpen(false);
      await load();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Erro ao selecionar página");
    }
  };

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

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { data } = await api.get("/social/meta/credentials");
        if (cancelled) return;
        setConfigured(Boolean(data.configured));
        setInfo(data);
        if (data.configured) setForm((f) => ({ ...f, app_id: data.app_id || "" }));
      } catch (e) {
        logger.error("load meta creds:", e);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const save = async () => {
    const missing = !form.app_id || !form.app_secret || !form.page_access_token;
    if (missing) {
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

  const exchangeToken = async () => {
    setExchanging(true);
    setTestResult(null);
    try {
      const { data } = await api.post("/social/meta/exchange-token");
      toast.success("Token convertido para vitalício!");
      setTestResult({ ok: true, ...data });
      await load();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Falha ao converter token");
    } finally {
      setExchanging(false);
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
            <div>Página Facebook: {info.facebook_page_id || "— (clique em Escolher Página)"}</div>
            <div>Instagram Business: {info.instagram_business_id || (
              <span className="text-amber-700">
                — não vinculado. Vincule sua conta Instagram Business à página em business.facebook.com → Configurações → Contas do Instagram.
              </span>
            )}</div>
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

        <div className="flex gap-2 pt-2 flex-wrap">
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
          <button
            data-testid="exchange-meta-token"
            onClick={exchangeToken}
            disabled={exchanging || !configured}
            title="Converte seu token de 1 hora em um Page Access Token que NUNCA expira"
            className="bg-emerald-700 text-white text-sm font-medium px-4 py-2 rounded-sm hover:bg-emerald-800 disabled:opacity-40 inline-flex items-center gap-2"
          >
            {exchanging ? <Loader2 className="h-4 w-4 animate-spin" /> : <Shield className="h-4 w-4" />}
            Tornar Token Vitalício
          </button>
          <button
            data-testid="list-meta-pages"
            onClick={openPageSelector}
            disabled={loadingPages || !configured}
            title="Lista todas as páginas que seu token tem acesso e permite escolher qual usar"
            className="bg-blue-600 text-white text-sm font-medium px-4 py-2 rounded-sm hover:bg-blue-700 disabled:opacity-40 inline-flex items-center gap-2"
          >
            {loadingPages ? <Loader2 className="h-4 w-4 animate-spin" /> : <ListIcon className="h-4 w-4" />}
            Escolher Página
          </button>
        </div>

        {pageSelectorOpen && (
          <div className="border border-blue-200 bg-blue-50 p-3 space-y-2" data-testid="page-selector">
            <div className="text-sm font-semibold">Páginas disponíveis:</div>
            {availablePages.length === 0 ? (
              <div className="text-sm text-rose-700">
                Nenhuma página encontrada — token expirado ou sem permissões. Renove o token primeiro.
              </div>
            ) : (
              availablePages.map((p) => (
                <button
                  key={p.id}
                  onClick={() => selectPage(p.id)}
                  data-testid={`pick-page-${p.id}`}
                  className={`w-full text-left p-2 border text-sm rounded-sm transition ${
                    p.selected ? "bg-emerald-100 border-emerald-300" : "bg-white border-border hover:bg-blue-100"
                  }`}
                >
                  <div className="font-medium">{p.name} {p.selected && <span className="text-emerald-700 text-xs">(atual)</span>}</div>
                  <div className="text-xs text-muted-foreground font-mono">ID: {p.id}</div>
                  <div className="text-xs">
                    Instagram: {p.has_instagram
                      ? <span className="text-emerald-700">vinculado (id={p.instagram_business_id})</span>
                      : <span className="text-amber-700">não vinculado</span>}
                  </div>
                </button>
              ))
            )}
          </div>
        )}

        <div className="text-xs text-zinc-600 bg-zinc-50 border border-zinc-200 p-3 rounded-sm leading-relaxed">
          <strong>💡 Dica:</strong> Tokens gerados no Graph API Explorer duram apenas <strong>1 hora</strong>.
          Depois de salvar, clique em <strong>Tornar Token Vitalício</strong> para que o sistema troque
          automaticamente por um Page Access Token que <strong>nunca expira</strong> (usando seu App ID + Secret).
          Após isso, também detectamos o Instagram Business linkado à sua página.
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

      <PinterestSection />
    </div>
  );
}


function PinterestSection() {
  const [loading, setLoading] = useState(true);
  const [configured, setConfigured] = useState(false);
  const [info, setInfo] = useState(null);
  const [accessToken, setAccessToken] = useState("");
  const [defaultBoardId, setDefaultBoardId] = useState("");
  const [boards, setBoards] = useState([]);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testRes, setTestRes] = useState(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { data } = await api.get("/social/pinterest/credentials");
        if (cancelled) return;
        setConfigured(Boolean(data.configured));
        setInfo(data);
        if (data.configured) setDefaultBoardId(data.default_board_id || "");
      } catch (e) {
        logger.error("pinterest creds", e);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const save = async () => {
    if (!accessToken && !configured) {
      toast.error("Cole o Access Token do Pinterest");
      return;
    }
    setSaving(true);
    try {
      const body = { default_board_id: defaultBoardId };
      if (accessToken) body.access_token = accessToken;
      await api.post("/social/pinterest/credentials", body);
      toast.success("Credenciais Pinterest salvas");
      setAccessToken("");
      const { data } = await api.get("/social/pinterest/credentials");
      setConfigured(Boolean(data.configured));
      setInfo(data);
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Erro ao salvar");
    } finally {
      setSaving(false);
    }
  };

  const test = async () => {
    setTesting(true);
    setTestRes(null);
    try {
      const { data } = await api.post("/social/pinterest/test");
      setTestRes(data);
      if (data.ok) {
        toast.success(`Conectado: @${data.username}`);
        const b = await api.get("/social/pinterest/boards");
        setBoards(b.data.items || []);
      } else {
        toast.error(data.error);
      }
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Falha no teste");
    } finally {
      setTesting(false);
    }
  };

  if (loading) return null;

  return (
    <div className="space-y-4 pt-2">
      <div className="border-t border-border pt-6">
        <h2 className="font-display text-xl font-bold tracking-tighter">Pinterest</h2>
        <p className="text-sm text-muted-foreground mt-1">
          Publique pins automaticamente ao gerar um anúncio. Gere o Access Token em{" "}
          <a href="https://developers.pinterest.com/" target="_blank" rel="noreferrer" className="text-[#EE7B22] inline-flex items-center gap-1 hover:underline">
            developers.pinterest.com <ExternalLink className="h-3 w-3" />
          </a>{" "}
          → My Apps → Generate Access Token (escopo <code>pins:write boards:read</code>).
        </p>
      </div>

      {configured && info && (
        <div className="border border-emerald-300 bg-emerald-50 p-3 text-sm" data-testid="pinterest-status-card">
          <div className="flex items-center gap-2 font-semibold text-emerald-900">
            <CheckCircle2 className="h-4 w-4" /> Token configurado
          </div>
          <div className="text-xs mt-1 font-mono">Board padrão: {info.default_board_id || "— (sem padrão)"}</div>
        </div>
      )}

      <div className="border border-border bg-white p-5 space-y-4">
        <div className="flex flex-col gap-1">
          <label className="label-overline">Access Token Pinterest</label>
          <input
            type="password"
            data-testid="pinterest-token"
            value={accessToken}
            onChange={(e) => setAccessToken(e.target.value)}
            placeholder={configured ? "Deixe vazio para manter o token atual" : "pina_..."}
            className="text-sm border border-border rounded-sm px-3 py-2 font-mono focus:outline-none focus:ring-2 focus:ring-[#EE7B22]"
          />
        </div>

        <div className="flex flex-col gap-1">
          <label className="label-overline">Board padrão (opcional)</label>
          {boards.length > 0 ? (
            <select
              data-testid="pinterest-board-select"
              value={defaultBoardId}
              onChange={(e) => setDefaultBoardId(e.target.value)}
              className="text-sm border border-border rounded-sm px-3 py-2 focus:outline-none focus:ring-2 focus:ring-[#EE7B22]"
            >
              <option value="">— selecione —</option>
              {boards.map((b) => (
                <option key={b.id} value={b.id}>{b.name}</option>
              ))}
            </select>
          ) : (
            <input
              type="text"
              data-testid="pinterest-board-id"
              value={defaultBoardId}
              onChange={(e) => setDefaultBoardId(e.target.value)}
              placeholder="ID do board (ex: 123456789012345678) ou clique Testar para buscar"
              className="text-sm border border-border rounded-sm px-3 py-2 font-mono focus:outline-none focus:ring-2 focus:ring-[#EE7B22]"
            />
          )}
        </div>

        <div className="flex gap-2 pt-2 flex-wrap">
          <button
            onClick={save}
            disabled={saving}
            data-testid="save-pinterest-creds"
            className="bg-[#EE7B22] text-white text-sm font-medium px-4 py-2 rounded-sm hover:bg-[#C9651A] disabled:opacity-40 inline-flex items-center gap-2"
          >
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            Salvar
          </button>
          <button
            onClick={test}
            disabled={testing || !configured}
            data-testid="test-pinterest-conn"
            className="bg-zinc-900 text-white text-sm font-medium px-4 py-2 rounded-sm hover:bg-zinc-700 disabled:opacity-40 inline-flex items-center gap-2"
          >
            {testing ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
            Testar e buscar boards
          </button>
        </div>

        {testRes && (
          <div className={`text-sm p-3 rounded-sm ${testRes.ok ? "bg-emerald-50 border border-emerald-200 text-emerald-900" : "bg-rose-50 border border-rose-200 text-rose-900"}`}>
            {testRes.ok ? (
              <>Conectado como <strong>@{testRes.username}</strong> ({testRes.account_type})</>
            ) : (
              <div className="space-y-2">
                <div className="font-mono text-xs">{testRes.error}</div>
                {/consumer type is not supported/i.test(testRes.error || "") && (
                  <div className="text-xs bg-white p-2 border border-rose-200 rounded-sm space-y-1">
                    <div><strong>O que isso significa:</strong> seu app Pinterest está em modo <em>Sandbox</em> (limite a você mesmo). A API <code>/v5/pins</code> só funciona com apps aprovados em produção.</div>
                    <div><strong>Como resolver:</strong></div>
                    <ol className="list-decimal ml-4 space-y-0.5">
                      <li>Acesse <a href="https://developers.pinterest.com/apps/" target="_blank" rel="noreferrer" className="text-[#EE7B22] hover:underline">developers.pinterest.com/apps</a></li>
                      <li>Selecione seu app → aba <strong>Apply for Production</strong></li>
                      <li>Preencha o formulário (use case: &quot;Auto-pin product images from Bling ERP&quot;) e submeta</li>
                      <li>Aprovação leva ~1-3 dias úteis. Após aprovação, gere um novo token <strong>de produção</strong> e cole aqui novamente.</li>
                    </ol>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
