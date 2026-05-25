import React, { useState, useEffect, useCallback } from 'react';
import { WEBHOOK_CONFIG } from '../config';
import { AuthState } from '../types';
import { ToastType } from '../components/Toast';
import { useLanguage } from '../context/LanguageContext';

interface Props {
  auth: AuthState;
  addToast?: (msg: string, type?: ToastType) => void;
}

// ── Translation table ────────────────────────────────────────────────────────
const T = {
  en: {
    title:               'AI Models',
    subtitle:            'Manage the AI model registry and configure which LLM model is used for each AI operation.',
    registryTab:         'Model Registry',
    stageDefaultsTab:    'LLM Usage Defaults',
    stageDefaultsNote:   'Only LLM-based operations are configurable here. Security checks, duplicate detection, gatekeeper screening, and other rule-based stages run locally and do not use an LLM.',
    secretsTab:          'Provider Secrets',
    addModel:            'Add Model',
    provider:            'Provider',
    modelName:           'Model Name',
    displayName:         'Display Name',
    secretKey:           'Secret Key',
    baseUrl:             'Base URL',
    stages:              'Supported Stages',
    enabled:             'Enabled',
    inputPrice:          'Input $/1M',
    outputPrice:         'Output $/1M',
    contextTokens:       'Context',
    notes:               'Notes',
    actions:             'Actions',
    edit:                'Edit',
    save:                'Save',
    cancel:              'Cancel',
    saving:              'Saving...',
    loading:             'Loading...',
    stage:               'Stage',
    primaryModel:        'Primary Model',
    fallbackModel:       'Fallback Model',
    noModel:             '— none —',
    secretStatus:        'Secret',
    configured:          'Configured',
    missing:             'Missing',
    providerSecrets:     'Provider API Key Status',
    secretsNote:         'Values are never shown. Green = key is set in Platform Secrets.',
    noModels:            'No models registered yet.',
    noSecrets:           'No provider secrets found.',
    errorLoad:           'Failed to load data.',
    savedOk:             'Saved successfully.',
    errorSave:           'Save failed.',
    enabledBadge:        'Active',
    disabledBadge:       'Disabled',
    createModel:         'Create Model',
    formProvider:        'Provider (e.g. openai)',
    formModelName:       'Model name (e.g. gpt-4o)',
    formDisplayName:     'Display name',
    formSecretKey:       'Platform secret key (e.g. OPENAI_API_KEY)',
    formBaseUrl:         'Base URL (leave blank for provider default)',
    formStages:          'Supported LLM stages (comma-separated: cv_analyzer, cv_scoring, cv_comparison, fallback)',
    formContextTokens:   'Max context tokens',
    formInputPrice:      'Input price per 1M tokens ($)',
    formOutputPrice:     'Output price per 1M tokens ($)',
    formNotes:           'Notes',
    toggle:              'Toggle enabled',
  },
  ar: {
    title:               'نماذج الذكاء الاصطناعي',
    subtitle:            'إدارة سجل نماذج الذكاء الاصطناعي وتكوين النموذج المستخدم لكل عملية ذكاء اصطناعي.',
    registryTab:         'سجل النماذج',
    stageDefaultsTab:    'إعدادات نماذج LLM',
    stageDefaultsNote:   'يمكن تكوين عمليات الذكاء الاصطناعي (LLM) فقط هنا. تعمل مراحل الفحص الأمني وكشف التكرار والحارس الدلالي وغيرها من المراحل القائمة على القواعد محلياً ولا تستخدم نماذج LLM.',
    secretsTab:          'مفاتيح المزودين',
    addModel:            'إضافة نموذج',
    provider:            'المزود',
    modelName:           'اسم النموذج',
    displayName:         'الاسم المعروض',
    secretKey:           'مفتاح سري',
    baseUrl:             'عنوان URL الأساسي',
    stages:              'المراحل المدعومة',
    enabled:             'مفعّل',
    inputPrice:          'سعر الإدخال',
    outputPrice:         'سعر الإخراج',
    contextTokens:       'السياق',
    notes:               'ملاحظات',
    actions:             'إجراءات',
    edit:                'تعديل',
    save:                'حفظ',
    cancel:              'إلغاء',
    saving:              'جارٍ الحفظ...',
    loading:             'جارٍ التحميل...',
    stage:               'المرحلة',
    primaryModel:        'النموذج الأساسي',
    fallbackModel:       'نموذج الاحتياط',
    noModel:             '— لا يوجد —',
    secretStatus:        'المفتاح',
    configured:          'محدد',
    missing:             'مفقود',
    providerSecrets:     'حالة مفاتيح API',
    secretsNote:         'القيم لا تُعرض أبداً. الأخضر = المفتاح مُعيَّن في أسرار المنصة.',
    noModels:            'لا توجد نماذج مسجلة بعد.',
    noSecrets:           'لم يتم العثور على أسرار مزودين.',
    errorLoad:           'فشل تحميل البيانات.',
    savedOk:             'تم الحفظ بنجاح.',
    errorSave:           'فشل الحفظ.',
    enabledBadge:        'نشط',
    disabledBadge:       'معطّل',
    createModel:         'إنشاء نموذج',
    formProvider:        'المزود (مثل openai)',
    formModelName:       'اسم النموذج (مثل gpt-4o)',
    formDisplayName:     'الاسم المعروض',
    formSecretKey:       'مفتاح سري للمنصة (مثل OPENAI_API_KEY)',
    formBaseUrl:         'عنوان URL الأساسي (اتركه فارغاً للقيمة الافتراضية)',
    formStages:          'مراحل LLM المدعومة (مفصولة بفواصل: cv_analyzer, cv_scoring, cv_comparison, fallback)',
    formContextTokens:   'الحد الأقصى لرموز السياق',
    formInputPrice:      'سعر الإدخال لكل مليون رمز ($)',
    formOutputPrice:     'سعر الإخراج لكل مليون رمز ($)',
    formNotes:           'ملاحظات',
    toggle:              'تبديل التفعيل',
  },
};

// ── Types ────────────────────────────────────────────────────────────────────

interface AIModel {
  model_id: string;
  provider: string;
  model_name: string;
  display_name: string;
  provider_secret_key: string | null;
  base_url: string | null;
  supported_stages: string[];
  enabled: boolean;
  max_context_tokens: number | null;
  input_price_per_1m_tokens: number | null;
  output_price_per_1m_tokens: number | null;
  notes: string | null;
  created_at: string | null;
  updated_at: string | null;
}

interface StageDefault {
  stage: string;
  primary_model_id: string | null;
  primary_model_name: string | null;
  primary_provider: string | null;
  primary_enabled: boolean | null;
  fallback_model_id: string | null;
  fallback_model_name: string | null;
  fallback_provider: string | null;
  fallback_enabled: boolean | null;
  updated_at: string | null;
}

interface BlankForm {
  provider: string;
  model_name: string;
  display_name: string;
  provider_secret_key: string;
  base_url: string;
  supported_stages: string;
  enabled: boolean;
  max_context_tokens: string;
  input_price_per_1m_tokens: string;
  output_price_per_1m_tokens: string;
  notes: string;
}

const BLANK_FORM: BlankForm = {
  provider: '',
  model_name: '',
  display_name: '',
  provider_secret_key: '',
  base_url: '',
  supported_stages: '',
  enabled: true,
  max_context_tokens: '',
  input_price_per_1m_tokens: '',
  output_price_per_1m_tokens: '',
  notes: '',
};

// Only real LLM stages shown in the UI — local-code stages are intentionally excluded
const LLM_STAGES: { key: string; labelEn: string; labelAr: string }[] = [
  { key: 'cv_analyzer', labelEn: 'CV Analyzer (Criteria Extraction)', labelAr: 'محلل السيرة الذاتية (استخراج المعايير)' },
  { key: 'cv_scoring',  labelEn: 'CV Scoring',                        labelAr: 'تقييم السيرة الذاتية' },
  { key: 'fallback',    labelEn: 'Fallback Model',                    labelAr: 'نموذج الاحتياط' },
];

// ── Helpers ──────────────────────────────────────────────────────────────────

function authHeaders(token: string | null): Record<string, string> {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function apiFetch(url: string, token: string | null, options: RequestInit = {}) {
  const res = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(token),
      ...(options.headers as Record<string, string> || {}),
    },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(text || res.statusText);
  }
  return res.json();
}

// ── Stage badge ──────────────────────────────────────────────────────────────
const STAGE_COLORS: Record<string, string> = {
  cv_analyzer:   'bg-cyan-100 text-cyan-700',
  cv_scoring:    'bg-indigo-100 text-indigo-700',
  cv_comparison: 'bg-purple-100 text-purple-700',
  fallback:      'bg-slate-100 text-slate-600',
};

const StageBadge: React.FC<{ stage: string }> = ({ stage }) => (
  <span className={`inline-block text-[10px] font-semibold px-1.5 py-0.5 rounded-full ${STAGE_COLORS[stage] || 'bg-slate-100 text-slate-600'}`}>
    {stage}
  </span>
);

// ── Main page ────────────────────────────────────────────────────────────────

export const AIModelsPage: React.FC<Props> = ({ auth, addToast }) => {
  const { lang } = useLanguage();
  const t = T[lang];
  const token = auth.token;

  const [tab, setTab] = useState<'registry' | 'stage-defaults' | 'secrets'>('registry');
  const [models, setModels] = useState<AIModel[]>([]);
  const [stageDefaults, setStageDefaults] = useState<StageDefault[]>([]);
  const [secrets, setSecrets] = useState<Record<string, boolean>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Add model form
  const [showAddForm, setShowAddForm] = useState(false);
  const [addForm, setAddForm] = useState<BlankForm>(BLANK_FORM);
  const [addSaving, setAddSaving] = useState(false);

  // Inline edit per model
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<Partial<BlankForm>>({});
  const [editSaving, setEditSaving] = useState(false);

  // Stage defaults edit — track pending changes per stage
  const [stagePending, setStagePending] = useState<Record<string, { primary: string | null; fallback: string | null }>>({});
  const [stageSaving, setStageSaving] = useState<Record<string, boolean>>({});

  // ── Load all data ──────────────────────────────────────────────────────────
  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [modelsData, stageData, secretsData] = await Promise.all([
        apiFetch((WEBHOOK_CONFIG as any).AI_MODELS_REGISTRY_URL, token),
        apiFetch((WEBHOOK_CONFIG as any).AI_STAGE_DEFAULTS_URL, token),
        apiFetch((WEBHOOK_CONFIG as any).AI_PROVIDER_SECRETS_URL, token),
      ]);
      setModels(modelsData);
      setStageDefaults(stageData);
      setSecrets(secretsData.secrets || {});
    } catch (err: any) {
      setError(err.message || t.errorLoad);
    } finally {
      setLoading(false);
    }
  }, [token, t.errorLoad]);

  useEffect(() => { loadAll(); }, [loadAll]);

  // ── Add model ──────────────────────────────────────────────────────────────
  const handleAddSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setAddSaving(true);
    try {
      const stages = addForm.supported_stages.split(',').map(s => s.trim()).filter(Boolean);
      await apiFetch((WEBHOOK_CONFIG as any).AI_MODELS_REGISTRY_URL, token, {
        method: 'POST',
        body: JSON.stringify({
          provider: addForm.provider.trim(),
          model_name: addForm.model_name.trim(),
          display_name: addForm.display_name.trim(),
          provider_secret_key: addForm.provider_secret_key.trim() || null,
          base_url: addForm.base_url.trim() || null,
          supported_stages: stages,
          enabled: addForm.enabled,
          max_context_tokens: addForm.max_context_tokens ? parseInt(addForm.max_context_tokens) : null,
          input_price_per_1m_tokens: addForm.input_price_per_1m_tokens ? parseFloat(addForm.input_price_per_1m_tokens) : null,
          output_price_per_1m_tokens: addForm.output_price_per_1m_tokens ? parseFloat(addForm.output_price_per_1m_tokens) : null,
          notes: addForm.notes.trim() || null,
        }),
      });
      addToast?.(t.savedOk, 'success');
      setShowAddForm(false);
      setAddForm(BLANK_FORM);
      await loadAll();
    } catch (err: any) {
      addToast?.(err.message || t.errorSave, 'error');
    } finally {
      setAddSaving(false);
    }
  };

  // ── Edit model ─────────────────────────────────────────────────────────────
  const startEdit = (model: AIModel) => {
    setEditingId(model.model_id);
    setEditForm({
      display_name: model.display_name,
      provider_secret_key: model.provider_secret_key || '',
      base_url: model.base_url || '',
      supported_stages: model.supported_stages.join(', '),
      enabled: model.enabled,
      max_context_tokens: model.max_context_tokens?.toString() || '',
      input_price_per_1m_tokens: model.input_price_per_1m_tokens?.toString() || '',
      output_price_per_1m_tokens: model.output_price_per_1m_tokens?.toString() || '',
      notes: model.notes || '',
    });
  };

  const saveEdit = async (modelId: string) => {
    setEditSaving(true);
    try {
      const stages = (editForm.supported_stages || '').split(',').map(s => s.trim()).filter(Boolean);
      const body: Record<string, any> = {
        display_name: editForm.display_name,
        provider_secret_key: editForm.provider_secret_key?.trim() || null,
        base_url: editForm.base_url?.trim() || null,
        supported_stages: stages,
        enabled: editForm.enabled,
        max_context_tokens: editForm.max_context_tokens ? parseInt(editForm.max_context_tokens) : null,
        input_price_per_1m_tokens: editForm.input_price_per_1m_tokens ? parseFloat(editForm.input_price_per_1m_tokens) : null,
        output_price_per_1m_tokens: editForm.output_price_per_1m_tokens ? parseFloat(editForm.output_price_per_1m_tokens) : null,
        notes: editForm.notes?.trim() || null,
      };
      await apiFetch(`${(WEBHOOK_CONFIG as any).AI_MODELS_REGISTRY_URL}/${modelId}`, token, {
        method: 'PUT',
        body: JSON.stringify(body),
      });
      addToast?.(t.savedOk, 'success');
      setEditingId(null);
      await loadAll();
    } catch (err: any) {
      addToast?.(err.message || t.errorSave, 'error');
    } finally {
      setEditSaving(false);
    }
  };

  const toggleEnabled = async (model: AIModel) => {
    try {
      await apiFetch(`${(WEBHOOK_CONFIG as any).AI_MODELS_REGISTRY_URL}/${model.model_id}`, token, {
        method: 'PUT',
        body: JSON.stringify({ enabled: !model.enabled }),
      });
      addToast?.(t.savedOk, 'success');
      await loadAll();
    } catch (err: any) {
      addToast?.(err.message || t.errorSave, 'error');
    }
  };

  // ── Stage defaults ─────────────────────────────────────────────────────────
  const getStageModels = (stage: string): AIModel[] =>
    models.filter(m => m.enabled && (m.supported_stages.includes(stage) || m.supported_stages.includes('fallback')));

  const getPending = (stage: string) =>
    stagePending[stage] ?? {
      primary: stageDefaults.find(s => s.stage === stage)?.primary_model_id ?? null,
      fallback: stageDefaults.find(s => s.stage === stage)?.fallback_model_id ?? null,
    };

  const saveStageDefault = async (stage: string) => {
    const pending = getPending(stage);
    setStageSaving(prev => ({ ...prev, [stage]: true }));
    try {
      await apiFetch(`${(WEBHOOK_CONFIG as any).AI_STAGE_DEFAULTS_URL}/${stage}`, token, {
        method: 'PUT',
        body: JSON.stringify({
          primary_model_id: pending.primary || null,
          fallback_model_id: pending.fallback || null,
        }),
      });
      addToast?.(t.savedOk, 'success');
      setStagePending(prev => { const n = { ...prev }; delete n[stage]; return n; });
      await loadAll();
    } catch (err: any) {
      addToast?.(err.message || t.errorSave, 'error');
    } finally {
      setStageSaving(prev => ({ ...prev, [stage]: false }));
    }
  };

  // ── Render ─────────────────────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="w-8 h-8 border-4 border-indigo-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-xl border border-red-200 bg-red-50 p-6 text-red-700 text-sm">
        {error}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-slate-900">{t.title}</h1>
        <p className="mt-1 text-sm text-slate-500">{t.subtitle}</p>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-slate-100 p-1 rounded-xl w-fit">
        {(['registry', 'stage-defaults', 'secrets'] as const).map(id => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${
              tab === id ? 'bg-white text-indigo-700 shadow-sm' : 'text-slate-500 hover:text-slate-700'
            }`}
          >
            {id === 'registry' ? t.registryTab : id === 'stage-defaults' ? t.stageDefaultsTab : t.secretsTab}
          </button>
        ))}
      </div>

      {/* ── Registry tab ────────────────────────────────────────────────── */}
      {tab === 'registry' && (
        <div className="bg-white rounded-2xl border border-slate-200 overflow-hidden">
          {/* Toolbar */}
          <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between">
            <span className="text-sm font-semibold text-slate-700">{t.registryTab}</span>
            <button
              onClick={() => { setShowAddForm(v => !v); setAddForm(BLANK_FORM); }}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-700 transition-colors"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 4v16m8-8H4" />
              </svg>
              {t.addModel}
            </button>
          </div>

          {/* Add form */}
          {showAddForm && (
            <form onSubmit={handleAddSubmit} className="px-6 py-5 border-b border-slate-100 bg-slate-50 space-y-4">
              <p className="text-sm font-semibold text-slate-700">{t.createModel}</p>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                {([
                  ['provider',            t.formProvider,       'text', true],
                  ['model_name',          t.formModelName,      'text', true],
                  ['display_name',        t.formDisplayName,    'text', true],
                  ['provider_secret_key', t.formSecretKey,      'text', false],
                  ['base_url',            t.formBaseUrl,        'text', false],
                  ['supported_stages',    t.formStages,         'text', false],
                  ['max_context_tokens',  t.formContextTokens,  'number', false],
                  ['input_price_per_1m_tokens',  t.formInputPrice,  'number', false],
                  ['output_price_per_1m_tokens', t.formOutputPrice, 'number', false],
                ] as [keyof BlankForm, string, string, boolean][]).map(([field, label, type, required]) => (
                  <div key={field}>
                    <label className="block text-xs font-medium text-slate-500 mb-1">{label}</label>
                    <input
                      type={type}
                      step={type === 'number' ? 'any' : undefined}
                      required={required}
                      value={addForm[field] as string}
                      onChange={e => setAddForm(prev => ({ ...prev, [field]: e.target.value }))}
                      className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-indigo-400"
                    />
                  </div>
                ))}
                <div>
                  <label className="block text-xs font-medium text-slate-500 mb-1">{t.formNotes}</label>
                  <input
                    type="text"
                    value={addForm.notes}
                    onChange={e => setAddForm(prev => ({ ...prev, notes: e.target.value }))}
                    className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-indigo-400"
                  />
                </div>
                <div className="flex items-center gap-2 pt-5">
                  <input
                    id="add-enabled"
                    type="checkbox"
                    checked={addForm.enabled}
                    onChange={e => setAddForm(prev => ({ ...prev, enabled: e.target.checked }))}
                    className="rounded border-slate-300 text-indigo-600"
                  />
                  <label htmlFor="add-enabled" className="text-sm text-slate-700">{t.enabled}</label>
                </div>
              </div>
              <div className="flex gap-3">
                <button
                  type="submit"
                  disabled={addSaving}
                  className="px-5 py-2 rounded-lg bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-700 disabled:opacity-50 transition-colors"
                >
                  {addSaving ? t.saving : t.createModel}
                </button>
                <button
                  type="button"
                  onClick={() => setShowAddForm(false)}
                  className="px-5 py-2 rounded-lg border border-slate-200 text-slate-600 text-sm font-medium hover:bg-slate-50 transition-colors"
                >
                  {t.cancel}
                </button>
              </div>
            </form>
          )}

          {/* Table */}
          {models.length === 0 ? (
            <div className="px-6 py-12 text-center text-slate-400 text-sm">{t.noModels}</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-100 bg-slate-50">
                    {[t.provider, t.displayName, t.stages, t.secretKey, t.inputPrice, t.outputPrice, t.enabled, t.actions].map(h => (
                      <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider whitespace-nowrap">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-50">
                  {models.map(model => (
                    <tr key={model.model_id} className="hover:bg-slate-50/50 transition-colors">
                      {editingId === model.model_id ? (
                        <>
                          <td className="px-4 py-3 text-slate-700 font-mono text-xs whitespace-nowrap">
                            {model.provider}<br /><span className="text-slate-400">{model.model_name}</span>
                          </td>
                          <td className="px-4 py-3">
                            <input
                              className="border border-slate-200 rounded px-2 py-1 text-sm w-36 focus:outline-none focus:ring-2 focus:ring-indigo-400"
                              value={editForm.display_name || ''}
                              onChange={e => setEditForm(p => ({ ...p, display_name: e.target.value }))}
                            />
                          </td>
                          <td className="px-4 py-3">
                            <input
                              className="border border-slate-200 rounded px-2 py-1 text-sm w-64 focus:outline-none focus:ring-2 focus:ring-indigo-400"
                              value={editForm.supported_stages || ''}
                              onChange={e => setEditForm(p => ({ ...p, supported_stages: e.target.value }))}
                              placeholder="stage1, stage2"
                            />
                          </td>
                          <td className="px-4 py-3">
                            <input
                              className="border border-slate-200 rounded px-2 py-1 text-sm w-40 focus:outline-none focus:ring-2 focus:ring-indigo-400"
                              value={editForm.provider_secret_key || ''}
                              onChange={e => setEditForm(p => ({ ...p, provider_secret_key: e.target.value }))}
                            />
                          </td>
                          <td className="px-4 py-3">
                            <input
                              type="number" step="any"
                              className="border border-slate-200 rounded px-2 py-1 text-sm w-24 focus:outline-none focus:ring-2 focus:ring-indigo-400"
                              value={editForm.input_price_per_1m_tokens || ''}
                              onChange={e => setEditForm(p => ({ ...p, input_price_per_1m_tokens: e.target.value }))}
                            />
                          </td>
                          <td className="px-4 py-3">
                            <input
                              type="number" step="any"
                              className="border border-slate-200 rounded px-2 py-1 text-sm w-24 focus:outline-none focus:ring-2 focus:ring-indigo-400"
                              value={editForm.output_price_per_1m_tokens || ''}
                              onChange={e => setEditForm(p => ({ ...p, output_price_per_1m_tokens: e.target.value }))}
                            />
                          </td>
                          <td className="px-4 py-3">
                            <input
                              type="checkbox"
                              checked={editForm.enabled ?? model.enabled}
                              onChange={e => setEditForm(p => ({ ...p, enabled: e.target.checked }))}
                              className="rounded border-slate-300 text-indigo-600"
                            />
                          </td>
                          <td className="px-4 py-3">
                            <div className="flex gap-2">
                              <button
                                onClick={() => saveEdit(model.model_id)}
                                disabled={editSaving}
                                className="px-3 py-1 rounded-lg bg-indigo-600 text-white text-xs font-medium hover:bg-indigo-700 disabled:opacity-50 transition-colors"
                              >
                                {editSaving ? t.saving : t.save}
                              </button>
                              <button
                                onClick={() => setEditingId(null)}
                                className="px-3 py-1 rounded-lg border border-slate-200 text-slate-600 text-xs font-medium hover:bg-slate-50 transition-colors"
                              >
                                {t.cancel}
                              </button>
                            </div>
                          </td>
                        </>
                      ) : (
                        <>
                          <td className="px-4 py-3 whitespace-nowrap">
                            <span className="font-semibold text-slate-800">{model.provider}</span>
                            <br />
                            <span className="text-slate-400 font-mono text-xs">{model.model_name}</span>
                          </td>
                          <td className="px-4 py-3 text-slate-700 whitespace-nowrap">
                            {model.display_name}
                          </td>
                          <td className="px-4 py-3">
                            <div className="flex flex-wrap gap-1 max-w-xs">
                              {model.supported_stages.map(s => <StageBadge key={s} stage={s} />)}
                            </div>
                          </td>
                          <td className="px-4 py-3 text-slate-500 font-mono text-xs whitespace-nowrap">
                            {model.provider_secret_key || '—'}
                          </td>
                          <td className="px-4 py-3 text-slate-600 text-xs whitespace-nowrap">
                            {model.input_price_per_1m_tokens != null ? `$${model.input_price_per_1m_tokens}` : '—'}
                          </td>
                          <td className="px-4 py-3 text-slate-600 text-xs whitespace-nowrap">
                            {model.output_price_per_1m_tokens != null ? `$${model.output_price_per_1m_tokens}` : '—'}
                          </td>
                          <td className="px-4 py-3">
                            <button
                              onClick={() => toggleEnabled(model)}
                              title={t.toggle}
                              className={`inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold transition-colors ${
                                model.enabled
                                  ? 'bg-green-100 text-green-700 hover:bg-green-200'
                                  : 'bg-slate-100 text-slate-500 hover:bg-slate-200'
                              }`}
                            >
                              {model.enabled ? t.enabledBadge : t.disabledBadge}
                            </button>
                          </td>
                          <td className="px-4 py-3">
                            <button
                              onClick={() => startEdit(model)}
                              className="px-3 py-1 rounded-lg border border-slate-200 text-slate-600 text-xs font-medium hover:bg-slate-50 transition-colors"
                            >
                              {t.edit}
                            </button>
                          </td>
                        </>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ── LLM Usage Defaults tab ───────────────────────────────────────── */}
      {tab === 'stage-defaults' && (
        <div className="space-y-4">
          {/* Explanatory note */}
          <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 flex gap-3 items-start">
            <svg className="w-4 h-4 text-amber-500 mt-0.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <p className="text-sm text-amber-800">{(t as any).stageDefaultsNote}</p>
          </div>

          <div className="bg-white rounded-2xl border border-slate-200 overflow-hidden">
          <div className="px-6 py-4 border-b border-slate-100">
            <span className="text-sm font-semibold text-slate-700">{t.stageDefaultsTab}</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-100 bg-slate-50">
                  {[t.stage, t.primaryModel, t.fallbackModel, t.actions].map(h => (
                    <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {LLM_STAGES.map(({ key: stage, labelEn, labelAr }) => {
                  const stageLabel = lang === 'ar' ? labelAr : labelEn;
                  const def = stageDefaults.find(s => s.stage === stage);
                  const pending = getPending(stage);
                  const stageModels = getStageModels(stage);
                  const isSavingStage = stageSaving[stage] || false;
                  const hasPending = stagePending[stage] !== undefined;

                  return (
                    <tr key={stage} className="hover:bg-slate-50/50 transition-colors">
                      <td className="px-4 py-3 whitespace-nowrap">
                        <div className="flex flex-col gap-0.5">
                          <StageBadge stage={stage} />
                          <span className="text-xs text-slate-500 mt-0.5">{stageLabel}</span>
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <select
                          value={pending.primary ?? ''}
                          onChange={e => setStagePending(prev => ({
                            ...prev,
                            [stage]: { ...getPending(stage), primary: e.target.value || null },
                          }))}
                          className="border border-slate-200 rounded-lg px-3 py-1.5 text-sm text-slate-700 bg-white focus:outline-none focus:ring-2 focus:ring-indigo-400 min-w-[200px]"
                        >
                          <option value="">{t.noModel}</option>
                          {stageModels.map(m => (
                            <option key={m.model_id} value={m.model_id}>
                              {m.display_name} ({m.provider})
                            </option>
                          ))}
                        </select>
                        {def?.primary_model_name && !hasPending && (
                          <span className="block text-xs text-slate-400 mt-0.5">{def.primary_model_name}</span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <select
                          value={pending.fallback ?? ''}
                          onChange={e => setStagePending(prev => ({
                            ...prev,
                            [stage]: { ...getPending(stage), fallback: e.target.value || null },
                          }))}
                          className="border border-slate-200 rounded-lg px-3 py-1.5 text-sm text-slate-700 bg-white focus:outline-none focus:ring-2 focus:ring-indigo-400 min-w-[200px]"
                        >
                          <option value="">{t.noModel}</option>
                          {stageModels.map(m => (
                            <option key={m.model_id} value={m.model_id}>
                              {m.display_name} ({m.provider})
                            </option>
                          ))}
                        </select>
                        {def?.fallback_model_name && !hasPending && (
                          <span className="block text-xs text-slate-400 mt-0.5">{def.fallback_model_name}</span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <button
                          onClick={() => saveStageDefault(stage)}
                          disabled={isSavingStage}
                          className="px-3 py-1.5 rounded-lg bg-indigo-600 text-white text-xs font-medium hover:bg-indigo-700 disabled:opacity-50 transition-colors"
                        >
                          {isSavingStage ? t.saving : t.save}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          </div>
        </div>
      )}

      {/* ── Provider Secrets tab ─────────────────────────────────────────── */}
      {tab === 'secrets' && (
        <div className="bg-white rounded-2xl border border-slate-200 overflow-hidden">
          <div className="px-6 py-4 border-b border-slate-100">
            <p className="text-sm font-semibold text-slate-700">{t.providerSecrets}</p>
            <p className="text-xs text-slate-400 mt-0.5">{t.secretsNote}</p>
          </div>
          {Object.keys(secrets).length === 0 ? (
            <div className="px-6 py-12 text-center text-slate-400 text-sm">{t.noSecrets}</div>
          ) : (
            <div className="divide-y divide-slate-50">
              {Object.entries(secrets).map(([key, hasValue]) => (
                <div key={key} className="flex items-center justify-between px-6 py-4">
                  <span className="font-mono text-sm text-slate-700">{key}</span>
                  <span className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold ${
                    hasValue ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-600'
                  }`}>
                    <span className={`w-1.5 h-1.5 rounded-full ${hasValue ? 'bg-green-500' : 'bg-red-400'}`} />
                    {hasValue ? t.configured : t.missing}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default AIModelsPage;
