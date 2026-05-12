import React, { useState, useEffect, useCallback } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { AuthState, PlatformConfig as PlatformConfigItem } from '../types';

interface Props {
  auth: AuthState;
  addToast: (msg: string, type: 'success' | 'error' | 'info') => void;
}

const CATEGORIES = ['scoring', 'ai', 'email', 'queue', 'subscription', 'security', 'general'];
const TYPES = ['string', 'number', 'boolean', 'json'];

// Controlled options for specific string config keys
const AI_MODELS = ['gpt-4o-mini', 'gpt-4o', 'gpt-4.1-mini', 'gpt-4.1'];
const LANGUAGES = ['ar', 'en', 'mixed'];
const SUBSCRIPTION_STATUSES = ['trial', 'active', 'suspended', 'expired'];
const TENANT_STATUSES = ['active', 'suspended'];

const CAT_LABELS: Record<string, string> = {
  scoring: 'Scoring', ai: 'AI', email: 'Email',
  queue: 'Queue', subscription: 'Subscription',
  security: 'Security', general: 'General',
};

const TYPE_COLORS: Record<string, string> = {
  string:  'bg-blue-100 text-blue-700',
  number:  'bg-violet-100 text-violet-700',
  boolean: 'bg-amber-100 text-amber-700',
  json:    'bg-slate-100 text-slate-700',
};

const CAT_COLORS: Record<string, string> = {
  scoring:      'bg-green-100 text-green-700',
  ai:           'bg-indigo-100 text-indigo-700',
  email:        'bg-sky-100 text-sky-700',
  queue:        'bg-orange-100 text-orange-700',
  subscription: 'bg-rose-100 text-rose-700',
  security:     'bg-red-100 text-red-700',
  general:      'bg-slate-100 text-slate-600',
};

// Detect what input control to show for a config entry
type ControlType = 'boolean-select' | 'model-select' | 'language-select' | 'status-select' | 'number-input' | 'json-textarea' | 'text-input';

function resolveControl(item: PlatformConfigItem): ControlType {
  if (item.type === 'boolean') return 'boolean-select';
  if (item.type === 'number') return 'number-input';
  if (item.type === 'json') return 'json-textarea';
  // string — check key patterns
  const k = item.key.toLowerCase();
  if (k.includes('model')) return 'model-select';
  if (k === 'output_language' || k.endsWith('_language')) return 'language-select';
  if (k.endsWith('_status') && item.category === 'subscription') return 'status-select';
  if (k.endsWith('_status')) return 'status-select';
  return 'text-input';
}

function getStatusOptions(item: PlatformConfigItem): string[] {
  if (item.category === 'subscription') return SUBSCRIPTION_STATUSES;
  return TENANT_STATUSES;
}

function validateValue(item: PlatformConfigItem, value: string): string | null {
  if (!value.trim() && item.type !== 'boolean') return 'Value cannot be empty';
  if (item.type === 'number') {
    if (isNaN(Number(value))) return 'Must be a valid number';
  }
  if (item.type === 'json') {
    try { JSON.parse(value); } catch { return 'Must be valid JSON'; }
  }
  return null;
}

interface EditState {
  item: PlatformConfigItem;
  value: string;
  error: string | null;
}

const EMPTY_NEW = {
  key: '', value: '', type: 'string', category: 'general', description: '', editable: true,
};

export const PlatformConfigPage: React.FC<Props> = ({ auth, addToast }) => {
  const [configs, setConfigs]       = useState<PlatformConfigItem[]>([]);
  const [loading, setLoading]       = useState(true);
  const [search, setSearch]         = useState('');
  const [catFilter, setCatFilter]   = useState('all');
  const [editState, setEditState]   = useState<EditState | null>(null);
  const [saving, setSaving]         = useState(false);
  const [showAdd, setShowAdd]       = useState(false);
  const [newItem, setNewItem]       = useState({ ...EMPTY_NEW });
  const [newItemError, setNewItemError] = useState<string | null>(null);
  const [adding, setAdding]         = useState(false);
  const [confirmEdit, setConfirmEdit] = useState<EditState | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiService.get(WEBHOOK_CONFIG.PLATFORM_CONFIG_URL, {}, auth.token!);
      setConfigs(data.configs || []);
    } catch (err: any) {
      addToast(err.message || 'Failed to load configuration', 'error');
    } finally {
      setLoading(false);
    }
  }, [auth.token]);

  useEffect(() => { load(); }, [load]);

  const openEdit = (item: PlatformConfigItem) => {
    if (!item.editable) return;
    const state: EditState = { item, value: item.value, error: null };
    if (item.category === 'security') {
      setConfirmEdit(state);
    } else {
      setEditState(state);
    }
  };

  const commitEdit = () => {
    if (confirmEdit) { setEditState(confirmEdit); setConfirmEdit(null); }
  };

  const updateEditValue = (val: string) => {
    if (!editState) return;
    const error = validateValue(editState.item, val);
    setEditState({ ...editState, value: val, error });
  };

  const saveEdit = async () => {
    if (!editState) return;
    const error = validateValue(editState.item, editState.value);
    if (error) { setEditState({ ...editState, error }); return; }
    setSaving(true);
    try {
      await apiService.put(
        `${WEBHOOK_CONFIG.PLATFORM_CONFIG_URL}/${editState.item.key}`,
        { value: editState.value },
        auth.token!,
      );
      addToast('Parameter updated', 'success');
      setEditState(null);
      await load();
    } catch (err: any) {
      addToast(err.message || 'Failed to save parameter', 'error');
    } finally {
      setSaving(false);
    }
  };

  const handleAdd = async () => {
    if (!newItem.key || !newItem.value) { setNewItemError('Key and value are required'); return; }
    const error = validateValue(newItem as PlatformConfigItem, newItem.value);
    if (error) { setNewItemError(error); return; }
    setAdding(true);
    try {
      await apiService.post(WEBHOOK_CONFIG.PLATFORM_CONFIG_URL, newItem, auth.token!);
      addToast('Parameter added', 'success');
      setShowAdd(false);
      setNewItem({ ...EMPTY_NEW });
      setNewItemError(null);
      await load();
    } catch (err: any) {
      addToast(err.message || 'Failed to add parameter', 'error');
    } finally {
      setAdding(false);
    }
  };

  const filtered = configs.filter(c => {
    const matchCat = catFilter === 'all' || c.category === catFilter;
    const q = search.toLowerCase();
    const matchSearch = !q || c.key.includes(q) || (c.description || '').toLowerCase().includes(q) || c.value.toLowerCase().includes(q);
    return matchCat && matchSearch;
  });

  // Group filtered by category, preserving order
  const groupedFiltered = CATEGORIES.reduce<Record<string, PlatformConfigItem[]>>((acc, cat) => {
    const items = filtered.filter(c => c.category === cat);
    if (items.length) acc[cat] = items;
    return acc;
  }, {});

  // ValueEditor: renders the right control for editing
  const ValueEditor = ({ value, onChange, item }: { value: string; onChange: (v: string) => void; item: PlatformConfigItem }) => {
    const control = resolveControl(item);
    const baseCls = 'w-full px-3 py-2 border border-primary/50 rounded-lg text-sm bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none';
    switch (control) {
      case 'boolean-select':
        return (
          <select value={value} onChange={e => onChange(e.target.value)} className={baseCls} autoFocus>
            <option value="true">true</option>
            <option value="false">false</option>
          </select>
        );
      case 'model-select':
        return (
          <select value={value} onChange={e => onChange(e.target.value)} className={baseCls} autoFocus>
            {AI_MODELS.map(m => <option key={m} value={m}>{m}</option>)}
          </select>
        );
      case 'language-select':
        return (
          <select value={value} onChange={e => onChange(e.target.value)} className={baseCls} autoFocus>
            {LANGUAGES.map(l => <option key={l} value={l}>{l === 'ar' ? 'Arabic (ar)' : l === 'en' ? 'English (en)' : 'Mixed (mixed)'}</option>)}
          </select>
        );
      case 'status-select':
        return (
          <select value={value} onChange={e => onChange(e.target.value)} className={baseCls} autoFocus>
            {getStatusOptions(item).map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        );
      case 'number-input':
        return (
          <input autoFocus type="number" value={value} onChange={e => onChange(e.target.value)}
            className={baseCls} />
        );
      case 'json-textarea':
        return (
          <textarea autoFocus rows={4} value={value} onChange={e => onChange(e.target.value)}
            className={`${baseCls} font-mono resize-y text-xs`} />
        );
      default:
        return (
          <input autoFocus type="text" value={value} onChange={e => onChange(e.target.value)}
            className={baseCls}
            onKeyDown={e => { if (e.key === 'Enter') saveEdit(); if (e.key === 'Escape') setEditState(null); }}
          />
        );
    }
  };

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center h-64 space-y-4">
        <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-primary" />
        <p className="text-textMuted animate-pulse font-bold uppercase tracking-widest text-xs">Loading…</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h2 className="text-xl font-black text-textMain">Platform Configuration</h2>
          <p className="text-xs text-textMuted mt-0.5">Manage system-wide operational parameters</p>
        </div>
        <button onClick={() => setShowAdd(true)}
          className="flex items-center gap-2 px-4 py-2 bg-primary text-white text-sm font-bold rounded-xl hover:bg-primaryDark transition-all shadow-md">
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 4v16m8-8H4" />
          </svg>
          Add Parameter
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-col sm:flex-row gap-3">
        <input type="text" value={search} onChange={e => setSearch(e.target.value)}
          placeholder="Search parameters…"
          className="flex-1 px-4 py-2.5 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary" />
        <select value={catFilter} onChange={e => setCatFilter(e.target.value)}
          className="px-4 py-2.5 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-primary/20 bg-white">
          <option value="all">All Categories</option>
          {CATEGORIES.map(c => <option key={c} value={c}>{CAT_LABELS[c]}</option>)}
        </select>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {(['ai', 'email', 'scoring', 'security'] as const).map(cat => {
          const count = configs.filter(c => c.category === cat).length;
          return (
            <div key={cat} className="bg-white rounded-xl border border-border p-3 flex items-center justify-between">
              <div>
                <p className="text-lg font-black text-textMain">{count}</p>
                <p className="text-[10px] font-black text-textMuted uppercase tracking-widest">{CAT_LABELS[cat]}</p>
              </div>
              <span className={`px-2 py-0.5 rounded-full text-[10px] font-black uppercase ${CAT_COLORS[cat]}`}>{cat}</span>
            </div>
          );
        })}
      </div>

      {/* Security category warning */}
      {(catFilter === 'all' || catFilter === 'security') && configs.some(c => c.category === 'security') && (
        <div className="bg-red-50 border border-red-200 rounded-xl px-5 py-3 flex gap-3">
          <svg className="w-4 h-4 text-red-500 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
          </svg>
          <p className="text-xs text-red-700">
            <strong>Security parameters</strong> are locked settings that affect authentication and access control.
            A confirmation is required before editing any security-category value.
          </p>
        </div>
      )}

      {/* Security confirmation modal */}
      {confirmEdit && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-2xl max-w-md w-full p-8 animate-fade-in">
            <div className="flex items-center gap-3 mb-4">
              <div className="w-10 h-10 rounded-full bg-red-100 flex items-center justify-center shrink-0">
                <svg className="w-5 h-5 text-error" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                </svg>
              </div>
              <div>
                <h3 className="text-base font-bold text-textMain">Security Parameter</h3>
                <p className="text-xs font-mono text-textMuted">{confirmEdit.item.key}</p>
              </div>
            </div>
            <p className="text-xs text-textMuted mb-6 leading-relaxed">
              You are about to edit a <strong>security-category</strong> parameter.
              {confirmEdit.item.description && ` — ${confirmEdit.item.description}`}
              {' '}Ensure you understand the impact before saving.
            </p>
            <div className="flex gap-3 justify-end">
              <button onClick={() => setConfirmEdit(null)}
                className="px-5 py-2 rounded-xl text-sm font-bold text-textMuted hover:text-textMain transition-colors">
                Cancel
              </button>
              <button onClick={commitEdit}
                className="px-6 py-2 bg-error text-white rounded-xl text-sm font-bold hover:bg-red-700 transition-colors">
                Proceed to edit
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Edit modal */}
      {editState && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-2xl max-w-lg w-full p-8 animate-fade-in">
            <div className="flex items-start justify-between mb-5">
              <div>
                <div className="flex items-center gap-2 mb-1">
                  <h3 className="text-base font-bold text-textMain">Edit Parameter</h3>
                  {editState.item.category === 'security' && (
                    <span className="px-2 py-0.5 bg-red-100 text-red-700 rounded text-[9px] font-black uppercase tracking-widest">Security</span>
                  )}
                </div>
                <p className="text-xs font-mono text-textMuted">{editState.item.key}</p>
              </div>
              <div className="flex items-center gap-2">
                <span className={`px-2 py-0.5 rounded text-[10px] font-black ${TYPE_COLORS[editState.item.type] || ''}`}>
                  {editState.item.type}
                </span>
                <span className={`px-2 py-0.5 rounded-full text-[10px] font-black uppercase ${CAT_COLORS[editState.item.category] || ''}`}>
                  {CAT_LABELS[editState.item.category] || editState.item.category}
                </span>
              </div>
            </div>

            {editState.item.description && (
              <p className="text-xs text-textMuted mb-4 leading-relaxed">{editState.item.description}</p>
            )}

            <div className="space-y-1.5 mb-6">
              <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">Value</label>
              <ValueEditor
                value={editState.value}
                onChange={updateEditValue}
                item={editState.item}
              />
              {editState.error && (
                <p className="text-[11px] text-error font-medium">{editState.error}</p>
              )}
            </div>

            <div className="flex items-center justify-between">
              <div className="text-[10px] text-textMuted">
                {editState.item.updated_at
                  ? `Last updated ${new Date(editState.item.updated_at).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })}${editState.item.updated_by_email ? ` by ${editState.item.updated_by_email}` : ''}`
                  : 'Never updated'
                }
              </div>
              <div className="flex gap-3">
                <button onClick={() => setEditState(null)} disabled={saving}
                  className="px-5 py-2 rounded-xl text-sm font-bold text-textMuted hover:text-textMain transition-colors disabled:opacity-50">
                  Cancel
                </button>
                <button onClick={saveEdit} disabled={saving || !!editState.error}
                  className="px-6 py-2 bg-primary text-white rounded-xl text-sm font-bold hover:bg-primaryDark transition-colors flex items-center gap-2 disabled:opacity-50">
                  {saving && <div className="w-3.5 h-3.5 border-2 border-white/20 border-t-white rounded-full animate-spin" />}
                  {saving ? 'Saving…' : 'Save'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Config groups by category */}
      {Object.entries(groupedFiltered).map(([cat, items]) => (
        <section key={cat} className="bg-white rounded-2xl border border-border shadow-sm overflow-hidden">
          <div className="px-6 py-4 border-b border-border bg-slate-50 flex items-center gap-3">
            <span className={`px-2.5 py-0.5 rounded-md text-[10px] font-black uppercase tracking-widest ${CAT_COLORS[cat] || ''}`}>
              {CAT_LABELS[cat] || cat}
            </span>
            <span className="text-[10px] text-textMuted font-semibold">{items.length} parameter{items.length !== 1 ? 's' : ''}</span>
            {cat === 'security' && (
              <span className="ml-auto px-2 py-0.5 bg-red-100 text-red-700 rounded text-[9px] font-black uppercase tracking-widest flex items-center gap-1">
                <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                </svg>
                Dangerous
              </span>
            )}
          </div>

          <div className="divide-y divide-border">
            {items.map(item => (
              <div key={item.key} className={`px-6 py-4 flex flex-col sm:flex-row sm:items-center gap-4 ${item.category === 'security' ? 'bg-red-50/20' : ''}`}>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1 flex-wrap">
                    <code className="text-xs font-bold text-primary bg-primary/5 px-2 py-0.5 rounded font-mono">
                      {item.key}
                    </code>
                    <span className={`px-1.5 py-0.5 rounded text-[10px] font-black ${TYPE_COLORS[item.type] || ''}`}>
                      {item.type}
                    </span>
                    {!item.editable && (
                      <span className="text-[9px] bg-slate-100 text-slate-500 px-1.5 py-0.5 rounded font-black uppercase">Read-only</span>
                    )}
                    {item.category === 'security' && (
                      <span className="text-[9px] bg-red-100 text-red-600 px-1.5 py-0.5 rounded font-black uppercase">Sensitive</span>
                    )}
                  </div>
                  {item.description && (
                    <p className="text-xs text-textMuted mb-1.5 leading-relaxed">{item.description}</p>
                  )}
                  <div className="flex items-center gap-3 flex-wrap">
                    {/* Current value display — smart rendering */}
                    {item.type === 'boolean' ? (
                      <span className={`text-xs font-bold px-2 py-0.5 rounded ${item.value === 'true' || item.value === '1' ? 'bg-green-100 text-green-700' : 'bg-slate-100 text-slate-600'}`}>
                        {item.value === 'true' || item.value === '1' ? '✓ true' : '✗ false'}
                      </span>
                    ) : (
                      <code className="text-xs text-textMain font-medium truncate max-w-[200px]" title={item.value}>
                        {item.value}
                      </code>
                    )}
                    {item.updated_at && (
                      <span className="text-[10px] text-textMuted">
                        {new Date(item.updated_at).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}
                        {item.updated_by_email ? ` · ${item.updated_by_email}` : ''}
                      </span>
                    )}
                  </div>
                </div>
                {item.editable && (
                  <button onClick={() => openEdit(item)}
                    className={`shrink-0 text-xs font-bold px-3 py-1.5 border rounded-lg transition-all ${
                      item.category === 'security'
                        ? 'border-red-200 text-error hover:bg-red-50'
                        : 'border-border text-textMuted hover:bg-slate-50 hover:text-textMain hover:border-primary/30'
                    }`}>
                    Edit
                  </button>
                )}
              </div>
            ))}
          </div>
        </section>
      ))}

      {Object.keys(groupedFiltered).length === 0 && (
        <div className="bg-white rounded-2xl border border-border p-12 text-center">
          <p className="text-sm text-textMuted">No parameters match your filter.</p>
        </div>
      )}

      {/* Add Parameter Modal */}
      {showAdd && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4 backdrop-blur-sm">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg">
            <div className="px-6 py-4 border-b border-border flex items-center justify-between">
              <h3 className="font-black text-textMain text-sm uppercase tracking-widest">Add New Parameter</h3>
              <button onClick={() => { setShowAdd(false); setNewItemError(null); }} className="text-textMuted hover:text-textMain p-1">
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <div className="p-6 space-y-4">
              <div>
                <label className="block text-xs font-black text-textMuted uppercase tracking-widest mb-1.5">
                  Key <span className="normal-case font-normal">(lowercase, underscores only)</span>
                </label>
                <input value={newItem.key}
                  onChange={e => setNewItem({ ...newItem, key: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, '') })}
                  className="w-full px-4 py-2.5 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary font-mono"
                  placeholder="e.g. my_parameter"
                />
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-black text-textMuted uppercase tracking-widest mb-1.5">Type</label>
                  <select value={newItem.type}
                    onChange={e => setNewItem({ ...newItem, type: e.target.value })}
                    className="w-full px-4 py-2.5 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-primary/20 bg-white">
                    {TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-black text-textMuted uppercase tracking-widest mb-1.5">Category</label>
                  <select value={newItem.category}
                    onChange={e => setNewItem({ ...newItem, category: e.target.value })}
                    className="w-full px-4 py-2.5 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-primary/20 bg-white">
                    {CATEGORIES.map(c => <option key={c} value={c}>{CAT_LABELS[c]}</option>)}
                  </select>
                </div>
              </div>
              <div>
                <label className="block text-xs font-black text-textMuted uppercase tracking-widest mb-1.5">Value</label>
                {newItem.type === 'boolean' ? (
                  <select value={newItem.value}
                    onChange={e => setNewItem({ ...newItem, value: e.target.value })}
                    className="w-full px-4 py-2.5 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-primary/20 bg-white">
                    <option value="true">true</option>
                    <option value="false">false</option>
                  </select>
                ) : newItem.type === 'json' ? (
                  <textarea rows={3} value={newItem.value}
                    onChange={e => setNewItem({ ...newItem, value: e.target.value })}
                    className="w-full px-4 py-2.5 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-primary/20 resize-none font-mono" />
                ) : newItem.type === 'number' ? (
                  <input type="number" value={newItem.value}
                    onChange={e => setNewItem({ ...newItem, value: e.target.value })}
                    className="w-full px-4 py-2.5 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-primary/20" />
                ) : (
                  <input type="text" value={newItem.value}
                    onChange={e => setNewItem({ ...newItem, value: e.target.value })}
                    className="w-full px-4 py-2.5 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-primary/20" />
                )}
              </div>
              <div>
                <label className="block text-xs font-black text-textMuted uppercase tracking-widest mb-1.5">Description</label>
                <textarea rows={2} value={newItem.description}
                  onChange={e => setNewItem({ ...newItem, description: e.target.value })}
                  className="w-full px-4 py-2.5 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-primary/20 resize-none" />
              </div>
              <label className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked={newItem.editable}
                  onChange={e => setNewItem({ ...newItem, editable: e.target.checked })}
                  className="w-4 h-4 rounded text-primary" />
                <span className="text-sm font-medium text-textMain">Editable by admins</span>
              </label>
              {newItemError && <p className="text-xs text-error font-medium">{newItemError}</p>}
            </div>
            <div className="px-6 py-4 border-t border-border flex justify-end gap-3">
              <button onClick={() => { setShowAdd(false); setNewItemError(null); }}
                className="px-4 py-2 text-sm font-bold border border-border text-textMuted rounded-xl hover:bg-slate-50">
                Cancel
              </button>
              <button disabled={adding || !newItem.key || !newItem.value} onClick={handleAdd}
                className="px-4 py-2 text-sm font-bold bg-primary text-white rounded-xl hover:bg-primaryDark disabled:opacity-50">
                {adding ? 'Adding…' : 'Add Parameter'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
