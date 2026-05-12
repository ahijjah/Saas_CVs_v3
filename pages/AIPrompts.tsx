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
};

const CATEGORY_COLORS: Record<string, string> = {
  criteria:  'bg-indigo-100 text-indigo-700',
  scoring:   'bg-green-100 text-green-700',
  screening: 'bg-cyan-100 text-cyan-700',
  summary:   'bg-purple-100 text-purple-700',
  interview: 'bg-amber-100 text-amber-700',
};

const KNOWN_CODES = ['criteria_extraction', 'cv_scoring', 'level2_screening'];

type ModalMode = 'edit' | 'create' | null;

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
  const [prompts, setPrompts]           = useState<AIPrompt[]>([]);
  const [loading, setLoading]           = useState(true);
  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [modalMode, setModalMode]       = useState<ModalMode>(null);
  const [editingId, setEditingId]       = useState<string | null>(null);
  const [form, setForm]                 = useState<FormState>(BLANK_FORM);
  const [saving, setSaving]             = useState(false);
  const [activating, setActivating]     = useState<string | null>(null);
  const [resetting, setResetting]       = useState<string | null>(null);

  const fetchPrompts = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiService.get(WEBHOOK_CONFIG.AI_PROMPTS_URL, {}, auth.token!);
      if (res?.success) setPrompts(res.prompts || []);
    } catch (err: any) {
      addToast(err.message || 'Failed to load prompts.', 'error');
    } finally {
      setLoading(false);
    }
  }, [auth.token, addToast]);

  useEffect(() => { fetchPrompts(); }, [fetchPrompts]);

  // Group all versions by prompt_code
  const grouped = prompts.reduce<Record<string, AIPrompt[]>>((acc, p) => {
    (acc[p.prompt_code] = acc[p.prompt_code] || []).push(p);
    return acc;
  }, {});

  // All unique codes (known ones first, then any custom ones)
  const allCodes = [
    ...KNOWN_CODES.filter(c => grouped[c]),
    ...Object.keys(grouped).filter(c => !KNOWN_CODES.includes(c)),
  ];

  const openCreate = () => {
    setForm({ ...BLANK_FORM });
    setEditingId(null);
    setModalMode('create');
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
    setEditingId(p.prompt_id);
    setModalMode('edit');
  };

  const handleSave = async () => {
    if (!form.system_prompt.trim()) {
      addToast('System prompt cannot be empty.', 'error');
      return;
    }
    setSaving(true);
    try {
      if (modalMode === 'create') {
        const res = await apiService.post(WEBHOOK_CONFIG.AI_PROMPTS_URL, form, auth.token!);
        if (res?.success) {
          addToast(`Prompt v${res.version} created. Activate it to use it in scoring.`, 'success');
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

  const handleActivate = async (promptId: string, promptCode: string) => {
    setActivating(promptId);
    try {
      const res = await apiService.post(
        `${WEBHOOK_CONFIG.AI_PROMPTS_URL}/${promptId}/activate`,
        {},
        auth.token!,
      );
      if (res?.success) {
        addToast(`Activated — future scoring jobs for '${promptCode}' will use this version.`, 'success');
        setPrompts(prev => prev.map(p =>
          p.prompt_code === promptCode
            ? { ...p, is_active: p.prompt_id === promptId }
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

  const formatDate = (iso: string | null) => {
    if (!iso) return '';
    return new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
  };

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
            Changing the active prompt does not re-score existing CVs. New scoring jobs submitted after activation
            will use the new prompt. The pipeline falls back to hardcoded defaults if no active DB prompt exists for a code.
          </p>
        </div>
      </div>

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <p className="text-[10px] font-black text-textMuted uppercase tracking-widest">
            {prompts.length} prompt version{prompts.length !== 1 ? 's' : ''} across {allCodes.length} code{allCodes.length !== 1 ? 's' : ''}
          </p>
        </div>
        <button onClick={openCreate}
          className="flex items-center gap-2 px-4 py-2 bg-primary text-white rounded-xl text-sm font-bold hover:bg-primaryDark transition-colors shadow-sm shadow-primary/20">
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 4v16m8-8H4" />
          </svg>
          New Prompt Version
        </button>
      </div>

      {/* Prompt code tabs */}
      {allCodes.length > 0 && (
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => setSelectedCode(null)}
            className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-colors ${selectedCode === null ? 'bg-primary text-white' : 'bg-white border border-border text-textMuted hover:text-textMain'}`}
          >
            All codes
          </button>
          {allCodes.map(code => {
            const active = grouped[code]?.find(p => p.is_active);
            return (
              <button key={code} onClick={() => setSelectedCode(selectedCode === code ? null : code)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold transition-colors ${selectedCode === code ? 'bg-primary text-white' : 'bg-white border border-border text-textMuted hover:text-textMain'}`}
              >
                <span className="font-mono">{code}</span>
                {active && <span className="w-1.5 h-1.5 rounded-full bg-green-500" title="Has active version" />}
                {!active && <span className="w-1.5 h-1.5 rounded-full bg-amber-400" title="No active version — using hardcoded default" />}
              </button>
            );
          })}
        </div>
      )}

      {/* Prompt list */}
      {(selectedCode ? [selectedCode] : allCodes).map(code => {
        const versions = grouped[code] || [];
        const hasDefault = KNOWN_CODES.includes(code);
        return (
          <section key={code} className="bg-white rounded-2xl border border-border shadow-sm overflow-hidden">
            {/* Section header */}
            <div className="px-8 py-5 border-b border-border bg-slate-50 flex flex-col sm:flex-row sm:items-center gap-3 justify-between">
              <div>
                <div className="flex items-center gap-2 mb-1">
                  <p className="text-sm font-black text-textMain font-mono">{code}</p>
                  {versions.find(p => p.is_active) ? (
                    <span className="px-2 py-0.5 bg-green-100 text-green-800 rounded text-[10px] font-black uppercase tracking-widest">Active version set</span>
                  ) : (
                    <span className="px-2 py-0.5 bg-amber-100 text-amber-700 rounded text-[10px] font-black uppercase tracking-widest">Using hardcoded default</span>
                  )}
                </div>
                <p className="text-xs text-textMuted">{versions.length} version{versions.length !== 1 ? 's' : ''}</p>
              </div>
              <div className="flex items-center gap-2">
                {hasDefault && (
                  <button
                    onClick={() => handleResetDefault(code)}
                    disabled={resetting === code}
                    className="flex items-center gap-1.5 px-3 py-1.5 border border-border rounded-lg text-xs font-bold text-textMuted hover:bg-slate-100 transition-colors disabled:opacity-50"
                  >
                    {resetting === code
                      ? <div className="w-3 h-3 border-2 border-textMuted/30 border-t-textMuted rounded-full animate-spin" />
                      : <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>
                    }
                    Reset to default
                  </button>
                )}
              </div>
            </div>

            {/* Version rows */}
            {versions.length === 0 ? (
              <div className="px-8 py-10 text-center text-sm text-textMuted">No versions yet.</div>
            ) : (
              <div className="divide-y divide-border">
                {versions.map(p => (
                  <div key={p.prompt_id} className={`px-8 py-5 flex flex-col lg:flex-row lg:items-start gap-4 ${p.is_active ? 'bg-green-50/30' : ''}`}>
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

                      <div className="flex items-center gap-4 text-[11px] text-textMuted flex-wrap">
                        <span className="font-mono">{p.model}</span>
                        <span>temp={p.temperature}</span>
                        <span>max_tokens={p.max_tokens}</span>
                        <span>lang={p.output_language}</span>
                        {p.updated_by_email && <span>by {p.updated_by_email}</span>}
                        {p.updated_at && <span>{formatDate(p.updated_at)}</span>}
                      </div>

                      {p.notes && (
                        <p className="text-xs text-textMuted italic">{p.notes}</p>
                      )}

                      {/* System prompt preview */}
                      <div className="mt-2 bg-slate-50 border border-border rounded-lg p-3">
                        <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">System Prompt (preview)</p>
                        <p className="text-xs text-textMuted font-mono whitespace-pre-wrap leading-relaxed line-clamp-4 overflow-hidden">
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
                          onClick={() => handleActivate(p.prompt_id, p.prompt_code)}
                          disabled={activating === p.prompt_id}
                          className="flex items-center gap-1.5 px-3 py-1.5 bg-green-600 text-white rounded-lg text-xs font-bold hover:bg-green-700 transition-colors disabled:opacity-50"
                        >
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
          <p className="text-xs text-textMuted mb-6">The scoring pipeline uses hardcoded defaults. Create a prompt version to override any stage.</p>
          <button onClick={openCreate} className="px-6 py-2.5 bg-primary text-white rounded-xl text-sm font-bold hover:bg-primaryDark transition-colors">
            Create First Prompt
          </button>
        </div>
      )}

      {/* Create / Edit modal */}
      {modalMode && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4 overflow-y-auto">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-3xl my-6 animate-fade-in">
            <div className="px-8 py-6 border-b border-border flex items-center justify-between">
              <h3 className="text-lg font-bold text-textMain">
                {modalMode === 'create' ? 'New Prompt Version' : 'Edit Prompt'}
              </h3>
              <button onClick={() => setModalMode(null)} className="text-textMuted hover:text-textMain transition-colors">
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            <div className="px-8 py-6 space-y-5 max-h-[70vh] overflow-y-auto">
              {/* Warning */}
              <div className="bg-amber-50 border border-amber-200 rounded-lg px-4 py-3 text-xs text-amber-700">
                Changes here affect <strong>future scoring jobs only</strong>. Existing evaluations are not re-run.
                New versions start as inactive — you must explicitly activate them.
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
                <div className="space-y-1.5">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">Prompt Code *</label>
                  <input
                    type="text" placeholder="e.g. criteria_extraction"
                    className="w-full px-3 py-2 border border-border rounded-lg text-sm font-mono bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none"
                    value={form.prompt_code}
                    onChange={e => setForm(f => ({ ...f, prompt_code: e.target.value }))}
                    readOnly={modalMode === 'edit'}
                  />
                </div>
                <div className="space-y-1.5">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">Display Name *</label>
                  <input
                    type="text" placeholder="e.g. Job Criteria Extraction"
                    className="w-full px-3 py-2 border border-border rounded-lg text-sm bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none"
                    value={form.prompt_name}
                    onChange={e => setForm(f => ({ ...f, prompt_name: e.target.value }))}
                  />
                </div>
                <div className="space-y-1.5">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">Category</label>
                  <select
                    className="w-full px-3 py-2 border border-border rounded-lg text-sm bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none"
                    value={form.prompt_category}
                    onChange={e => setForm(f => ({ ...f, prompt_category: e.target.value as PromptCategory }))}
                  >
                    {Object.entries(CATEGORY_LABELS).map(([k, v]) => (
                      <option key={k} value={k}>{v}</option>
                    ))}
                  </select>
                </div>
                <div className="space-y-1.5">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">Model</label>
                  <input
                    type="text" placeholder="gpt-4o-mini"
                    className="w-full px-3 py-2 border border-border rounded-lg text-sm font-mono bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none"
                    value={form.model}
                    onChange={e => setForm(f => ({ ...f, model: e.target.value }))}
                  />
                </div>
                <div className="space-y-1.5">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">Temperature (0–2)</label>
                  <input
                    type="number" min="0" max="2" step="0.05"
                    className="w-full px-3 py-2 border border-border rounded-lg text-sm bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none"
                    value={form.temperature}
                    onChange={e => setForm(f => ({ ...f, temperature: parseFloat(e.target.value) || 0 }))}
                  />
                </div>
                <div className="space-y-1.5">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">Max Tokens</label>
                  <input
                    type="number" min="1" max="32000"
                    className="w-full px-3 py-2 border border-border rounded-lg text-sm bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none"
                    value={form.max_tokens}
                    onChange={e => setForm(f => ({ ...f, max_tokens: parseInt(e.target.value) || 1000 }))}
                  />
                </div>
                <div className="space-y-1.5">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">Output Language</label>
                  <select
                    className="w-full px-3 py-2 border border-border rounded-lg text-sm bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none"
                    value={form.output_language}
                    onChange={e => setForm(f => ({ ...f, output_language: e.target.value }))}
                  >
                    <option value="ar">Arabic (ar)</option>
                    <option value="en">English (en)</option>
                    <option value="auto">Auto-detect</option>
                  </select>
                </div>
              </div>

              <div className="space-y-1.5">
                <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">System Prompt *</label>
                <textarea
                  rows={12}
                  className="w-full px-3 py-2 border border-border rounded-lg text-sm font-mono bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none resize-y"
                  placeholder="Enter the system prompt…"
                  value={form.system_prompt}
                  onChange={e => setForm(f => ({ ...f, system_prompt: e.target.value }))}
                />
                <p className="text-[10px] text-textMuted">{form.system_prompt.length.toLocaleString()} characters</p>
              </div>

              <div className="space-y-1.5">
                <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">User Prompt Template (optional)</label>
                <textarea
                  rows={4}
                  className="w-full px-3 py-2 border border-border rounded-lg text-sm font-mono bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none resize-y"
                  placeholder="Optional template with {variable} placeholders…"
                  value={form.user_prompt_template}
                  onChange={e => setForm(f => ({ ...f, user_prompt_template: e.target.value }))}
                />
              </div>

              <div className="space-y-1.5">
                <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">Change Notes</label>
                <input
                  type="text" placeholder="What changed in this version?"
                  className="w-full px-3 py-2 border border-border rounded-lg text-sm bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none"
                  value={form.notes}
                  onChange={e => setForm(f => ({ ...f, notes: e.target.value }))}
                />
              </div>
            </div>

            <div className="px-8 py-5 border-t border-border flex justify-end gap-3 bg-slate-50/50">
              <button onClick={() => setModalMode(null)} disabled={saving}
                className="px-5 py-2 rounded-xl text-sm font-bold text-textMuted hover:text-textMain transition-colors disabled:opacity-50">
                Cancel
              </button>
              <button onClick={handleSave} disabled={saving || !form.system_prompt.trim() || !form.prompt_code.trim()}
                className="px-8 py-2 bg-primary text-white rounded-xl text-sm font-bold hover:bg-primaryDark transition-colors flex items-center gap-2 disabled:opacity-50">
                {saving && <div className="w-3.5 h-3.5 border-2 border-white/20 border-t-white rounded-full animate-spin" />}
                {saving ? 'Saving…' : modalMode === 'create' ? 'Create Version' : 'Save Changes'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
