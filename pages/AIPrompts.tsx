import React, { useState, useEffect, useCallback } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { AuthState, AIPrompt, PromptCategory } from '../types';
import { ToastType } from '../components/Toast';

interface Props {
  auth: AuthState;
  addToast: (msg: string, type: ToastType) => void;
}

const CATEGORY_LABELS: Record<PromptCategory | string, string> = {
  criteria:  'Criteria Extraction',
  scoring:   'CV Scoring',
  screening: 'Pre-screening',
  summary:   'Summary',
  interview: 'Interview Questions',
  knockout:  'Knockout Analysis',
};

const CATEGORY_COLORS: Record<string, string> = {
  criteria:  'bg-indigo-100 text-indigo-700',
  scoring:   'bg-green-100 text-green-700',
  screening: 'bg-cyan-100 text-cyan-700',
  summary:   'bg-purple-100 text-purple-700',
  interview: 'bg-amber-100 text-amber-700',
  knockout:  'bg-rose-100 text-rose-700',
};

const PIPELINE_DESCRIPTIONS: Record<string, string> = {
  criteria_extraction: 'Extracts structured hiring criteria (skills, experience, education, weights) from a job description.',
  cv_scoring:          'Full 7-dimension bilingual CV scoring — Level 3 deep evaluation.',
  level2_screening:    'Lightweight binary PASS/REJECT pre-screen — Level 2 fast gate-keeper.',
  knockout_analysis:   'Analyzes CV text and email context to suggest answers for unanswered knockout questions. Returns confidence, evidence, and verification status per question.',
};

const KNOWN_CODES = ['criteria_extraction', 'cv_scoring', 'level2_screening', 'knockout_analysis'];

// Defaults used when backend does not return valid_models (graceful fallback)
const FALLBACK_MODELS = ['gpt-4o-mini', 'gpt-4o', 'gpt-4.1-mini', 'gpt-4.1'];
const FALLBACK_LANGUAGES = ['ar', 'en', 'auto'];

type ModalMode = 'new_prompt' | 'new_version' | 'edit' | null;

interface FormState {
  prompt_code: string;
  prompt_name: string;
  prompt_category: PromptCategory;
  system_prompt: string;
  user_prompt_template: string;
  model: string;
  temperature: number;
  max_tokens: number;
  output_language: string;
  notes: string;
}

const BLANK_FORM: FormState = {
  prompt_code: '',
  prompt_name: '',
  prompt_category: 'scoring',
  system_prompt: '',
  user_prompt_template: '',
  model: 'gpt-4o-mini',
  temperature: 0.2,
  max_tokens: 2000,
  output_language: 'ar',
  notes: '',
};

export const AIPromptsPage: React.FC<Props> = ({ auth, addToast }) => {
  const [prompts, setPrompts]             = useState<AIPrompt[]>([]);
  const [loading, setLoading]             = useState(true);
  const [validModels, setValidModels]     = useState<string[]>(FALLBACK_MODELS);
  const [validLanguages, setValidLanguages] = useState<string[]>(FALLBACK_LANGUAGES);
  const [selectedCode, setSelectedCode]   = useState<string | null>(null);
  const [modalMode, setModalMode]         = useState<ModalMode>(null);
  const [editingId, setEditingId]         = useState<string | null>(null);
  const [form, setForm]                   = useState<FormState>(BLANK_FORM);
  const [saving, setSaving]               = useState(false);
  const [activating, setActivating]       = useState<string | null>(null);
  const [resetting, setResetting]         = useState<string | null>(null);
  const [confirmActivate, setConfirmActivate] = useState<AIPrompt | null>(null);
  const [confirmReset, setConfirmReset]   = useState<string | null>(null);
  const [formErrors, setFormErrors]       = useState<Partial<Record<keyof FormState, string>>>({});

  const fetchPrompts = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiService.get(WEBHOOK_CONFIG.AI_PROMPTS_URL, {}, auth.token!);
      if (res?.success) {
        setPrompts(res.prompts || []);
        if (res.valid_models?.length) setValidModels(res.valid_models);
        if (res.valid_languages?.length) setValidLanguages(res.valid_languages);
      }
    } catch (err: any) {
      addToast(err.message || 'Failed to load prompts.', 'error');
    } finally {
      setLoading(false);
    }
  }, [auth.token, addToast]);

  useEffect(() => { fetchPrompts(); }, [fetchPrompts]);

  const grouped = prompts.reduce<Record<string, AIPrompt[]>>((acc, p) => {
    (acc[p.prompt_code] = acc[p.prompt_code] || []).push(p);
    return acc;
  }, {});

  const allCodes = [
    ...KNOWN_CODES.filter(c => grouped[c]),
    ...Object.keys(grouped).filter(c => !KNOWN_CODES.includes(c)),
  ];

  const PROMPT_CODE_RE = /^[a-z][a-z0-9_]*$/;

  const validateForm = (): boolean => {
    const errors: Partial<Record<keyof FormState, string>> = {};
    const code = form.prompt_code.trim();
    if (!code) {
      errors.prompt_code = 'Prompt code is required';
    } else if (!PROMPT_CODE_RE.test(code)) {
      errors.prompt_code = 'Use lowercase letters, digits, and underscores only — no spaces or capitals (e.g. cv_scoring)';
    }
    if (!form.prompt_name.trim()) errors.prompt_name = 'Display name is required';
    if (!form.system_prompt.trim()) errors.system_prompt = 'System prompt is required';
    if (form.temperature < 0 || form.temperature > 2) errors.temperature = 'Must be between 0 and 2';
    if (form.max_tokens < 1 || form.max_tokens > 32000) errors.max_tokens = 'Must be between 1 and 32000';
    setFormErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const openCreate = () => {
    setForm({ ...BLANK_FORM, model: validModels[0] || 'gpt-4o-mini' });
    setFormErrors({});
    setEditingId(null);
    setModalMode('new_prompt');
  };

  const openNewVersion = (code: string) => {
    const versions = (grouped[code] || []).slice().sort((a, b) => b.version - a.version);
    const latest = versions[0];
    if (latest) {
      setForm({
        prompt_code:          code,
        prompt_name:          latest.prompt_name,
        prompt_category:      latest.prompt_category,
        system_prompt:        latest.system_prompt,
        user_prompt_template: latest.user_prompt_template || '',
        model:                latest.model,
        temperature:          latest.temperature,
        max_tokens:           latest.max_tokens,
        output_language:      latest.output_language,
        notes:                '',
      });
    } else {
      setForm({ ...BLANK_FORM, prompt_code: code, model: validModels[0] || 'gpt-4o-mini' });
    }
    setFormErrors({});
    setEditingId(null);
    setModalMode('new_version');
  };

  const openEdit = (p: AIPrompt) => {
    setForm({
      prompt_code:          p.prompt_code,
      prompt_name:          p.prompt_name,
      prompt_category:      p.prompt_category,
      system_prompt:        p.system_prompt,
      user_prompt_template: p.user_prompt_template || '',
      model:                p.model,
      temperature:          p.temperature,
      max_tokens:           p.max_tokens,
      output_language:      p.output_language,
      notes:                '',
    });
    setFormErrors({});
    setEditingId(p.prompt_id);
    setModalMode('edit');
  };

  const handleSave = async () => {
    if (!validateForm()) return;
    setSaving(true);
    try {
      if (modalMode === 'new_prompt' || modalMode === 'new_version') {
        const res = await apiService.post(WEBHOOK_CONFIG.AI_PROMPTS_URL, form, auth.token!);
        if (res?.success) {
          const msg = modalMode === 'new_version'
            ? `Version v${res.version} created for '${form.prompt_code}'. Activate it to use it in scoring.`
            : `Prompt '${form.prompt_code}' v${res.version} created. Activate it to use it in scoring.`;
          addToast(msg, 'success');
          setModalMode(null);
          fetchPrompts();
        }
      } else if (modalMode === 'edit' && editingId) {
        const res = await apiService.put(`${WEBHOOK_CONFIG.AI_PROMPTS_URL}/${editingId}`, form, auth.token!);
        if (res?.success) {
          addToast('Prompt updated.', 'success');
          setModalMode(null);
          fetchPrompts();
        }
      }
    } catch (err: any) {
      addToast(err.message || 'Failed to save prompt.', 'error');
    } finally {
      setSaving(false);
    }
  };

  const handleActivate = async (prompt: AIPrompt) => {
    setActivating(prompt.prompt_id);
    setConfirmActivate(null);
    try {
      const res = await apiService.post(
        `${WEBHOOK_CONFIG.AI_PROMPTS_URL}/${prompt.prompt_id}/activate`,
        {},
        auth.token!,
      );
      if (res?.success) {
        addToast(`'${prompt.prompt_code}' v${prompt.version} is now active — future scoring will use this prompt.`, 'success');
        setPrompts(prev => prev.map(p =>
          p.prompt_code === prompt.prompt_code
            ? { ...p, is_active: p.prompt_id === prompt.prompt_id }
            : p
        ));
      }
    } catch (err: any) {
      addToast(err.message || 'Failed to activate prompt.', 'error');
    } finally {
      setActivating(null);
    }
  };

  const handleResetDefault = async (promptCode: string) => {
    setResetting(promptCode);
    setConfirmReset(null);
    try {
      const res = await apiService.post(
        `${WEBHOOK_CONFIG.AI_PROMPTS_URL}/${promptCode}/reset-default`,
        {},
        auth.token!,
      );
      if (res?.success) {
        addToast(res.message || `Reset '${promptCode}' to default.`, 'success');
        fetchPrompts();
      }
    } catch (err: any) {
      addToast(err.message || 'No default exists for this prompt.', 'error');
    } finally {
      setResetting(null);
    }
  };

  const formatDate = (iso: string | null) =>
    iso ? new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) : '';

  const Field = ({ label, error, children }: { label: string; error?: string; children: React.ReactNode }) => (
    <div className="space-y-1.5">
      <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{label}</label>
      {children}
      {error && <p className="text-[11px] text-error font-medium">{error}</p>}
    </div>
  );

  const inputCls = (err?: string) =>
    `w-full px-3 py-2 border rounded-lg text-sm bg-white outline-none transition-colors ${
      err ? 'border-error focus:ring-2 focus:ring-error/20' : 'border-border focus:ring-2 focus:ring-primary/20 focus:border-primary'
    }`;

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center h-64 space-y-4">
        <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-primary" />
        <p className="text-textMuted text-xs font-black uppercase tracking-widest animate-pulse">Loading prompts…</p>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto space-y-6 pb-12 animate-fade-in">

      {/* Info banner */}
      <div className="bg-blue-50 border border-blue-200 rounded-xl px-6 py-4 flex gap-3">
        <svg className="w-5 h-5 text-blue-500 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
        <div>
          <p className="text-sm font-bold text-blue-800">Active prompts affect future scoring only</p>
          <p className="text-xs text-blue-700 mt-0.5 leading-relaxed">
            Changing the active prompt does not re-score existing CVs. New scoring jobs submitted after
            activation will use the new prompt. The pipeline falls back to hardcoded defaults if no
            active DB prompt exists for a code.
          </p>
        </div>
      </div>

      {/* Header */}
      <div className="flex items-center justify-between">
        <p className="text-[10px] font-black text-textMuted uppercase tracking-widest">
          {allCodes.length} prompt code{allCodes.length !== 1 ? 's' : ''} · {prompts.length} version{prompts.length !== 1 ? 's' : ''}
        </p>
        <button onClick={openCreate}
          className="flex items-center gap-2 px-4 py-2 bg-primary text-white rounded-xl text-sm font-bold hover:bg-primaryDark transition-colors shadow-sm shadow-primary/20">
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 4v16m8-8H4" />
          </svg>
          New Prompt
        </button>
      </div>

      {/* Code filter tabs */}
      {allCodes.length > 0 && (
        <div className="flex flex-wrap gap-2">
          <button onClick={() => setSelectedCode(null)}
            className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-colors ${selectedCode === null ? 'bg-primary text-white' : 'bg-white border border-border text-textMuted hover:text-textMain'}`}>
            All codes
          </button>
          {allCodes.map(code => {
            const active = grouped[code]?.find(p => p.is_active);
            return (
              <button key={code} onClick={() => setSelectedCode(selectedCode === code ? null : code)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold transition-colors ${selectedCode === code ? 'bg-primary text-white' : 'bg-white border border-border text-textMuted hover:text-textMain'}`}>
                <span className="font-mono">{code}</span>
                <span className={`w-1.5 h-1.5 rounded-full ${active ? 'bg-green-500' : 'bg-amber-400'}`}
                  title={active ? 'Has active version' : 'No active version — using hardcoded default'} />
              </button>
            );
          })}
        </div>
      )}

      {/* Prompt list */}
      {(selectedCode ? [selectedCode] : allCodes).map(code => {
        const versions = grouped[code] || [];
        const hasDefault = KNOWN_CODES.includes(code);
        const activeVersion = versions.find(p => p.is_active);
        return (
          <section key={code} className="bg-white rounded-2xl border border-border shadow-sm overflow-hidden">
            <div className="px-8 py-5 border-b border-border bg-slate-50 flex flex-col sm:flex-row sm:items-start gap-3 justify-between">
              <div>
                <div className="flex items-center gap-2 mb-1 flex-wrap">
                  <p className="text-sm font-black text-textMain font-mono">{code}</p>
                  {activeVersion ? (
                    <span className="px-2 py-0.5 bg-green-100 text-green-800 rounded text-[10px] font-black uppercase tracking-widest">
                      Active: v{activeVersion.version}
                    </span>
                  ) : (
                    <span className="px-2 py-0.5 bg-amber-100 text-amber-700 rounded text-[10px] font-black uppercase tracking-widest">
                      Using hardcoded default
                    </span>
                  )}
                </div>
                {PIPELINE_DESCRIPTIONS[code] && (
                  <p className="text-xs text-textMuted">{PIPELINE_DESCRIPTIONS[code]}</p>
                )}
                <p className="text-[10px] text-textMuted mt-1">{versions.length} version{versions.length !== 1 ? 's' : ''}</p>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {hasDefault && (
                  <button
                    onClick={() => setConfirmReset(code)}
                    disabled={resetting === code}
                    className="flex items-center gap-1.5 px-3 py-1.5 border border-border rounded-lg text-xs font-bold text-textMuted hover:bg-slate-100 transition-colors disabled:opacity-50">
                    {resetting === code
                      ? <div className="w-3 h-3 border-2 border-textMuted/30 border-t-textMuted rounded-full animate-spin" />
                      : <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>
                    }
                    Reset to default
                  </button>
                )}
                <button
                  onClick={() => openNewVersion(code)}
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-primary text-white rounded-lg text-xs font-bold hover:bg-primaryDark transition-colors">
                  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 4v16m8-8H4" />
                  </svg>
                  New Version
                </button>
              </div>
            </div>

            {versions.length === 0 ? (
              <div className="px-8 py-10 text-center text-sm text-textMuted">No versions yet.</div>
            ) : (
              <div className="divide-y divide-border">
                {versions.map(p => (
                  <div key={p.prompt_id} className={`px-8 py-5 flex flex-col lg:flex-row lg:items-start gap-4 ${p.is_active ? 'bg-green-50/40' : ''}`}>
                    <div className="flex-1 min-w-0 space-y-2">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-sm font-bold text-textMain">{p.prompt_name}</span>
                        <span className="px-2 py-0.5 bg-slate-100 text-textMuted rounded text-[10px] font-black">v{p.version}</span>
                        <span className={`px-2 py-0.5 rounded text-[10px] font-black uppercase tracking-widest ${CATEGORY_COLORS[p.prompt_category] || 'bg-slate-100 text-slate-700'}`}>
                          {CATEGORY_LABELS[p.prompt_category] || p.prompt_category}
                        </span>
                        {p.is_active && (
                          <span className="px-2 py-0.5 bg-green-100 text-green-800 rounded text-[10px] font-black uppercase tracking-widest flex items-center gap-1">
                            <span className="w-1.5 h-1.5 rounded-full bg-green-500" /> Active
                          </span>
                        )}
                      </div>

                      {/* Metadata row */}
                      <div className="flex items-center gap-3 text-[11px] text-textMuted flex-wrap">
                        <span className="font-mono font-semibold text-textMain">{p.model}</span>
                        <span>temp <strong>{p.temperature}</strong></span>
                        <span>max_tokens <strong>{p.max_tokens}</strong></span>
                        <span>lang <strong>{p.output_language}</strong></span>
                        {p.updated_by_email && <span>· {p.updated_by_email}</span>}
                        {p.updated_at && <span>· {formatDate(p.updated_at)}</span>}
                      </div>

                      {p.notes && <p className="text-xs text-textMuted italic">{p.notes}</p>}

                      {/* System prompt preview */}
                      <div className="mt-2 bg-slate-50 border border-border rounded-lg p-3">
                        <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">System Prompt Preview</p>
                        <p className="text-xs text-textMuted font-mono whitespace-pre-wrap leading-relaxed line-clamp-4">
                          {p.system_prompt.slice(0, 400)}{p.system_prompt.length > 400 ? '…' : ''}
                        </p>
                      </div>
                    </div>

                    {/* Actions */}
                    <div className="flex items-center gap-2 shrink-0 lg:pt-1">
                      <button onClick={() => openEdit(p)}
                        className="flex items-center gap-1.5 px-3 py-1.5 border border-border rounded-lg text-xs font-bold text-textMuted hover:bg-slate-50 hover:text-textMain transition-colors">
                        <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                        </svg>
                        Edit
                      </button>
                      {!p.is_active && (
                        <button
                          onClick={() => setConfirmActivate(p)}
                          disabled={activating === p.prompt_id}
                          className="flex items-center gap-1.5 px-3 py-1.5 bg-green-600 text-white rounded-lg text-xs font-bold hover:bg-green-700 transition-colors disabled:opacity-50">
                          {activating === p.prompt_id
                            ? <div className="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                            : <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7" /></svg>
                          }
                          Activate
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
        );
      })}

      {allCodes.length === 0 && (
        <div className="bg-white rounded-2xl border border-border p-16 text-center">
          <svg className="w-10 h-10 mx-auto mb-4 text-slate-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
          </svg>
          <p className="text-sm font-bold text-textMain mb-1">No custom prompts yet</p>
          <p className="text-xs text-textMuted mb-6">The scoring pipeline uses hardcoded defaults. Create a new prompt to override any pipeline stage.</p>
          <button onClick={openCreate} className="px-6 py-2.5 bg-primary text-white rounded-xl text-sm font-bold hover:bg-primaryDark transition-colors">
            Create First Prompt
          </button>
        </div>
      )}

      {/* Activate confirmation modal */}
      {confirmActivate && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-2xl max-w-md w-full p-8 animate-fade-in">
            <div className="flex items-center gap-3 mb-4">
              <div className="w-10 h-10 rounded-full bg-green-100 flex items-center justify-center shrink-0">
                <svg className="w-5 h-5 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7" />
                </svg>
              </div>
              <div>
                <h3 className="text-base font-bold text-textMain">Activate Prompt Version</h3>
                <p className="text-xs text-textMuted font-mono">{confirmActivate.prompt_code} · v{confirmActivate.version}</p>
              </div>
            </div>
            <div className="bg-blue-50 border border-blue-200 rounded-lg px-4 py-3 mb-5 text-xs text-blue-700 leading-relaxed">
              Activating <strong>{confirmActivate.prompt_name}</strong> (v{confirmActivate.version}) will
              replace the current active version for <strong>{confirmActivate.prompt_code}</strong>.
              All new CV scoring jobs submitted after activation will use this prompt.
              Existing evaluations are not affected.
            </div>
            <div className="grid grid-cols-2 gap-3 text-xs text-textMuted bg-slate-50 rounded-lg p-3 mb-5">
              <div><span className="font-bold">Model:</span> {confirmActivate.model}</div>
              <div><span className="font-bold">Temperature:</span> {confirmActivate.temperature}</div>
              <div><span className="font-bold">Max tokens:</span> {confirmActivate.max_tokens}</div>
              <div><span className="font-bold">Language:</span> {confirmActivate.output_language}</div>
            </div>
            <div className="flex gap-3 justify-end">
              <button onClick={() => setConfirmActivate(null)}
                className="px-5 py-2 rounded-xl text-sm font-bold text-textMuted hover:text-textMain transition-colors">
                Cancel
              </button>
              <button onClick={() => handleActivate(confirmActivate)}
                className="px-6 py-2 bg-green-600 text-white rounded-xl text-sm font-bold hover:bg-green-700 transition-colors">
                Activate
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Reset to default confirmation modal */}
      {confirmReset && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-2xl max-w-md w-full p-8 animate-fade-in">
            <div className="flex items-center gap-3 mb-4">
              <div className="w-10 h-10 rounded-full bg-amber-100 flex items-center justify-center shrink-0">
                <svg className="w-5 h-5 text-amber-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                </svg>
              </div>
              <div>
                <h3 className="text-base font-bold text-textMain">Reset to Default</h3>
                <p className="text-xs font-mono text-textMuted">{confirmReset}</p>
              </div>
            </div>
            <p className="text-xs text-textMuted mb-6 leading-relaxed">
              This will create a new version of <strong>{confirmReset}</strong> from the hardcoded default
              and immediately activate it. Your custom versions will be preserved in history.
            </p>
            <div className="flex gap-3 justify-end">
              <button onClick={() => setConfirmReset(null)}
                className="px-5 py-2 rounded-xl text-sm font-bold text-textMuted hover:text-textMain transition-colors">
                Cancel
              </button>
              <button onClick={() => handleResetDefault(confirmReset)}
                className="px-6 py-2 bg-primary text-white rounded-xl text-sm font-bold hover:bg-primaryDark transition-colors">
                Reset & Activate Default
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Create / Edit modal */}
      {modalMode && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4 overflow-y-auto">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-3xl my-6 animate-fade-in">
            <div className="px-8 py-6 border-b border-border flex items-center justify-between">
              <h3 className="text-lg font-bold text-textMain">
                {modalMode === 'new_prompt' ? 'New Prompt' : modalMode === 'new_version' ? `New Version — ${form.prompt_code}` : 'Edit Prompt'}
              </h3>
              <button onClick={() => setModalMode(null)} className="text-textMuted hover:text-textMain transition-colors">
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            <div className="px-8 py-6 space-y-5 max-h-[70vh] overflow-y-auto">
              {modalMode === 'new_version' && (
                <div className="bg-blue-50 border border-blue-200 rounded-lg px-4 py-3 text-xs text-blue-700">
                  Creating a new version of <strong className="font-mono">{form.prompt_code}</strong>. The version number is assigned automatically. New versions start as inactive — activate explicitly to use in scoring.
                </div>
              )}
              {(modalMode === 'new_prompt' || modalMode === 'edit') && (
                <div className="bg-amber-50 border border-amber-200 rounded-lg px-4 py-3 text-xs text-amber-700">
                  Changes affect <strong>future scoring jobs only</strong>. New versions start as inactive — activate explicitly.
                </div>
              )}

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
                <Field label="Prompt Code *" error={formErrors.prompt_code}>
                  <input type="text" placeholder="e.g. criteria_extraction"
                    className={`${inputCls(formErrors.prompt_code)} font-mono ${modalMode !== 'new_prompt' ? 'bg-slate-50 cursor-not-allowed text-textMuted' : ''}`}
                    value={form.prompt_code}
                    onChange={e => setForm(f => ({ ...f, prompt_code: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, '') }))}
                    readOnly={modalMode !== 'new_prompt'}
                  />
                  {modalMode === 'new_prompt' && (
                    <p className="text-[10px] text-textMuted">Lowercase letters, digits, underscores only — e.g. <span className="font-mono">cv_scoring</span></p>
                  )}
                  {modalMode === 'new_version' && (
                    <p className="text-[10px] text-textMuted">Locked — versions share the same prompt code</p>
                  )}
                </Field>
                <Field label="Display Name *" error={formErrors.prompt_name}>
                  <input type="text" placeholder="e.g. Job Criteria Extraction"
                    className={inputCls(formErrors.prompt_name)}
                    value={form.prompt_name}
                    onChange={e => setForm(f => ({ ...f, prompt_name: e.target.value }))}
                  />
                </Field>
                <Field label="Category">
                  <select className={inputCls()}
                    value={form.prompt_category}
                    onChange={e => setForm(f => ({ ...f, prompt_category: e.target.value as PromptCategory }))}>
                    {Object.entries(CATEGORY_LABELS).map(([k, v]) => (
                      <option key={k} value={k}>{v}</option>
                    ))}
                  </select>
                </Field>
                <Field label="Model">
                  <select className={inputCls()}
                    value={form.model}
                    onChange={e => setForm(f => ({ ...f, model: e.target.value }))}>
                    {validModels.map(m => <option key={m} value={m}>{m}</option>)}
                  </select>
                </Field>
                <Field label="Temperature (0–2)" error={formErrors.temperature}>
                  <input type="number" min="0" max="2" step="0.05"
                    className={inputCls(formErrors.temperature)}
                    value={form.temperature}
                    onChange={e => setForm(f => ({ ...f, temperature: parseFloat(e.target.value) || 0 }))}
                  />
                </Field>
                <Field label="Max Tokens" error={formErrors.max_tokens}>
                  <input type="number" min="1" max="32000"
                    className={inputCls(formErrors.max_tokens)}
                    value={form.max_tokens}
                    onChange={e => setForm(f => ({ ...f, max_tokens: parseInt(e.target.value) || 1000 }))}
                  />
                </Field>
                <Field label="Output Language">
                  <select className={inputCls()}
                    value={form.output_language}
                    onChange={e => setForm(f => ({ ...f, output_language: e.target.value }))}>
                    {validLanguages.map(l => (
                      <option key={l} value={l}>
                        {l === 'ar' ? 'Arabic (ar)' : l === 'en' ? 'English (en)' : 'Auto-detect (auto)'}
                      </option>
                    ))}
                  </select>
                </Field>
              </div>

              <Field label="System Prompt *" error={formErrors.system_prompt}>
                <textarea rows={12}
                  className={`${inputCls(formErrors.system_prompt)} font-mono resize-y`}
                  placeholder="Enter the system prompt…"
                  value={form.system_prompt}
                  onChange={e => setForm(f => ({ ...f, system_prompt: e.target.value }))}
                />
                <p className="text-[10px] text-textMuted">{form.system_prompt.length.toLocaleString()} characters</p>
              </Field>

              <Field label="User Prompt Template (optional)">
                <textarea rows={4}
                  className={`${inputCls()} font-mono resize-y`}
                  placeholder="Optional template with {variable} placeholders…"
                  value={form.user_prompt_template}
                  onChange={e => setForm(f => ({ ...f, user_prompt_template: e.target.value }))}
                />
              </Field>

              <Field label="Change Notes">
                <input type="text" placeholder="What changed in this version?"
                  className={inputCls()}
                  value={form.notes}
                  onChange={e => setForm(f => ({ ...f, notes: e.target.value }))}
                />
              </Field>
            </div>

            <div className="px-8 py-5 border-t border-border flex justify-end gap-3 bg-slate-50/50">
              <button onClick={() => setModalMode(null)} disabled={saving}
                className="px-5 py-2 rounded-xl text-sm font-bold text-textMuted hover:text-textMain transition-colors disabled:opacity-50">
                Cancel
              </button>
              <button onClick={handleSave} disabled={saving}
                className="px-8 py-2 bg-primary text-white rounded-xl text-sm font-bold hover:bg-primaryDark transition-colors flex items-center gap-2 disabled:opacity-50">
                {saving && <div className="w-3.5 h-3.5 border-2 border-white/20 border-t-white rounded-full animate-spin" />}
                {saving ? 'Saving…' : modalMode === 'new_prompt' ? 'Create Prompt' : modalMode === 'new_version' ? 'Create Version' : 'Save Changes'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
