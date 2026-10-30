import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Brain, CheckCircle2, Loader2, Save, FlaskConical, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { logger } from "@/lib/logger";

const PROVIDERS = [
  {
    id: "anthropic",
    name: "Anthropic (Claude)",
    hint: "Recomendado para textos. console.anthropic.com → API Keys",
    placeholder: "sk-ant-...",
  },
  {
    id: "openai",
    name: "OpenAI (GPT)",
    hint: "Texto, imagem e voz dos Shorts. platform.openai.com → API keys",
    placeholder: "sk-...",
  },
  {
    id: "gemini",
    name: "Google Gemini",
    hint: "Imagens dos anúncios (Nano Banana). aistudio.google.com → Get API key",
    placeholder: "AIza...",
  },
  {
    id: "openrouter",
    name: "OpenRouter",
    hint: "Uma chave só para vários modelos. openrouter.ai → Keys",
    placeholder: "sk-or-...",
  },
];

export default function AISettings() {
  const [status, setStatus] = useState(null);
  const [keys, setKeys] = useState({});
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(null);
  const [results, setResults] = useState({});

  const load = useCallback(async () => {
    try {
      const { data } = await api.get("/settings/ai");
      setStatus(data);
    } catch (err) {
      logger.error("ai settings:", err);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const save = async (override) => {
    const payload = override || Object.fromEntries(
      Object.entries(keys).filter(([, v]) => v && v.trim()),
    );
    if (!Object.keys(payload).length) {
      toast.info("Cole pelo menos uma chave");
      return;
    }
    setSaving(true);
    try {
      const { data } = await api.post("/settings/ai", payload);
      setStatus(data);
      setKeys({});
      toast.success("Chaves de IA salvas");
    } catch (err) {
      logger.error("save ai:", err);
      toast.error("Falha ao salvar as chaves");
    } finally {
      setSaving(false);
    }
  };

  const test = async (provider) => {
    setTesting(provider);
    try {
      const { data } = await api.post("/settings/ai/test", { provider }, { timeout: 90000 });
      setResults((r) => ({ ...r, [provider]: data }));
      if (data.ok) toast.success(`${provider}: funcionando`);
      else toast.error(`${provider}: ${data.error}`);
    } catch (err) {
      toast.error("Falha no teste");
    } finally {
      setTesting(null);
    }
  };

  const prov = status?.providers || {};
  const any = Object.values(prov).some((p) => p.configured);

  return (
    <div className="border border-border bg-white p-6 space-y-4" data-testid="ai-settings">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-display font-semibold flex items-center gap-2">
            <Brain className="h-5 w-5 text-[#EE7B22]" /> Inteligência Artificial
          </h2>
          <p className="text-sm text-muted-foreground mt-1 max-w-2xl">
            Antes a IA vinha da chave da Emergent. Agora você usa a sua chave, direto no provedor,
            e paga só pelo que usar. Uma chave já basta. Ela fica guardada só neste computador/servidor.
          </p>
        </div>
        {any && (
          <span className="text-xs px-3 py-1 bg-emerald-100 text-emerald-700 border border-emerald-300 rounded-sm uppercase font-semibold inline-flex items-center gap-1.5 shrink-0">
            <CheckCircle2 className="h-3.5 w-3.5" /> Ativa
          </span>
        )}
      </div>

      {status && (
        <div className="text-xs bg-zinc-50 border border-border px-3 py-2 space-y-0.5">
          <div>
            <strong>Textos</strong> (títulos, descrições, categorias):{" "}
            {status.text?.provider ? `${status.text.provider} · ${status.text.model}` : "— nenhuma chave"}
          </div>
          <div>
            <strong>Imagens</strong> (Criar Anúncio):{" "}
            {status.image?.provider ? `${status.image.provider} · ${status.image.model}` : "— precisa de Gemini, OpenAI ou OpenRouter"}
          </div>
          <div>
            <strong>Voz</strong> (YouTube Shorts): {status.tts ? "OpenAI tts-1" : "— precisa de OpenAI"}
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {PROVIDERS.map((p) => {
          const st = prov[p.id] || {};
          const res = results[p.id];
          return (
            <div key={p.id} className="border border-border p-3 space-y-2">
              <div className="flex items-center justify-between gap-2">
                <div className="text-sm font-medium">{p.name}</div>
                {st.configured ? (
                  <span className="text-[11px] text-emerald-700 inline-flex items-center gap-1">
                    <CheckCircle2 className="h-3 w-3" /> {st.hint} {st.source === "env" ? "(.env)" : ""}
                  </span>
                ) : (
                  <span className="text-[11px] text-zinc-400">não configurada</span>
                )}
              </div>
              <div className="text-[11px] text-muted-foreground">{p.hint}</div>
              <input
                type="password"
                autoComplete="off"
                data-testid={`ai-key-${p.id}`}
                value={keys[p.id] || ""}
                onChange={(e) => setKeys((k) => ({ ...k, [p.id]: e.target.value }))}
                placeholder={st.configured ? "•••••••• (cole para trocar)" : p.placeholder}
                className="w-full text-sm border border-border rounded-sm px-3 py-2 focus:outline-none focus:ring-2 focus:ring-[#EE7B22]"
              />
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => test(p.id)}
                  disabled={!st.configured || testing === p.id}
                  className="text-xs px-3 py-1.5 border border-border hover:bg-zinc-50 disabled:opacity-40 inline-flex items-center gap-1"
                >
                  {testing === p.id ? <Loader2 className="h-3 w-3 animate-spin" /> : <FlaskConical className="h-3 w-3" />}
                  Testar
                </button>
                {st.configured && st.source !== "env" && (
                  <button
                    type="button"
                    onClick={() => save({ [p.id]: "" })}
                    className="text-xs px-3 py-1.5 border border-border hover:bg-rose-50 text-rose-700 inline-flex items-center gap-1"
                  >
                    <Trash2 className="h-3 w-3" /> Remover
                  </button>
                )}
                {res && (
                  <span className={`text-[11px] truncate ${res.ok ? "text-emerald-700" : "text-rose-700"}`}>
                    {res.ok ? "OK" : res.error}
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>

      <button
        type="button"
        data-testid="save-ai-keys"
        onClick={() => save()}
        disabled={saving}
        className="bg-[#EE7B22] text-white text-sm font-medium px-5 py-2.5 rounded-sm hover:bg-[#C9651A] disabled:opacity-50 inline-flex items-center gap-2"
      >
        {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
        Salvar chaves
      </button>
    </div>
  );
}
