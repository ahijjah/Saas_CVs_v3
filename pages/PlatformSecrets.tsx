import React, { useState, useEffect, useCallback } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { AuthState, PlatformSecret } from '../types';
import { ToastType } from '../components/Toast';

interface Props {
  auth: AuthState;
  addToast: (msg: string, type: ToastType) => void;
}

const CATEGORY_LABELS: Record<string, string> = {
  ai: 'AI / LLM',
  security: 'Security',
  email: 'Email',
  database: 'Database',
  queue: 'Queue / Cache',
  general: 'General',
};

const CATEGORY_COLORS: Record<string, string> = {
  ai:       'bg-purple-100 text-purple-800',
  security: 'bg-red-100 text-red-800',
  email:    'bg-blue-100 text-blue-800',
  database: 'bg-orange-100 text-orange-800',
  queue:    'bg-cyan-100 text-cyan-800',
  general:  'bg-slate-100 text-slate-700',
};

export const PlatformSecretsPage: React.FC<Props> = ({ auth, addToast }) => {
  const [secrets, setSecrets]         = useState<PlatformSecret[]>([]);
  const [loading, setLoading]         = useState(true);
  const [editKey, setEditKey]         = useState<string | null>(null);
  const [newValue, setNewValue]       = useState('');
  const [showValue, setShowValue]     = useState(false);
  const [saving, setSaving]           = useState(false);
  const [confirmKey, setConfirmKey]   = useState<string | null>(null);

  const fetchSecrets = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiService.get(WEBHOOK_CONFIG.PLATFORM_SECRETS_URL, {}, auth.token!);
      if (res?.success) setSecrets(res.secrets || []);
    } catch (err: any) {
      addToast(err.message || 'Failed to load secrets.', 'error');
    } finally {
      setLoading(false);
    }
  }, [auth.token, addToast]);

  useEffect(() => { fetchSecrets(); }, [fetchSecrets]);

  const openEdit = (key: string, isCritical: boolean) => {
    if (isCritical) {
      setConfirmKey(key);
    } else {
      setEditKey(key);
      setNewValue('');
      setShowValue(false);
    }
  };

  const confirmCritical = () => {
    if (confirmKey) {
      setEditKey(confirmKey);
      setNewValue('');
      setShowValue(false);
      setConfirmKey(null);
    }
  };

  const handleSave = async () => {
    if (!editKey || !newValue.trim()) return;
    setSaving(true);
    try {
      const res = await apiService.put(
        `${WEBHOOK_CONFIG.PLATFORM_SECRETS_URL}/${editKey}`,
        { value: newValue.trim() },
        auth.token!,
      );
      if (res?.success) {
        addToast(`Secret '${editKey}' updated successfully.`, 'success');
        if (res.warning) addToast(res.warning, 'warning' as ToastType);
        setSecrets(prev => prev.map(s =>
          s.key === editKey ? { ...s, masked_value: res.masked_value, has_value: true } : s
        ));
        setEditKey(null);
        setNewValue('');
      }
    } catch (err: any) {
      addToast(err.message || 'Failed to update secret.', 'error');
    } finally {
      setSaving(false);
    }
  };

  const grouped = secrets.reduce<Record<string, PlatformSecret[]>>((acc, s) => {
    (acc[s.category] = acc[s.category] || []).push(s);
    return acc;
  }, {});

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center h-64 space-y-4">
        <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-primary" />
        <p className="text-textMuted text-xs font-black uppercase tracking-widest animate-pulse">Loading secrets…</p>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto space-y-6 pb-12 animate-fade-in">

      {/* Warning banner */}
      <div className="bg-amber-50 border border-amber-200 rounded-xl px-6 py-4 flex gap-3">
        <svg className="w-5 h-5 text-amber-500 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
        </svg>
        <div>
          <p className="text-sm font-bold text-amber-800">Runtime Secrets Note</p>
          <p className="text-xs text-amber-700 mt-0.5 leading-relaxed">
            The application currently reads secrets from <strong>environment variables</strong> at startup.
            Values stored here are auditable records. To apply a new secret to the running service, update
            the environment variable and restart the service. Values set here are never exposed via any API.
          </p>
        </div>
      </div>

      {/* Critical confirmation dialog */}
      {confirmKey && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-2xl max-w-md w-full p-8 animate-fade-in">
            <div className="flex items-center gap-3 mb-4">
              <div className="w-10 h-10 rounded-full bg-red-100 flex items-center justify-center shrink-0">
                <svg className="w-5 h-5 text-error" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
                </svg>
              </div>
              <h3 className="text-lg font-bold text-textMain">Critical Secret Warning</h3>
            </div>
            <p className="text-sm text-textMuted mb-2">You are about to update <strong className="text-textMain">{confirmKey}</strong>, which is a critical credential.</p>
            <ul className="text-xs text-textMuted space-y-1 mb-6 list-disc list-inside">
              {confirmKey === 'JWT_SECRET' && <li>All active user sessions will be <strong>immediately invalidated</strong>.</li>}
              {confirmKey === 'DB_PASSWORD' && <li>Service will fail to connect to the database until restarted with the new password.</li>}
              <li>Ensure you have the new value ready and that a service restart is planned.</li>
            </ul>
            <div className="flex gap-3 justify-end">
              <button onClick={() => setConfirmKey(null)} className="px-5 py-2 rounded-xl text-sm font-bold text-textMuted hover:text-textMain transition-colors">
                Cancel
              </button>
              <button onClick={confirmCritical} className="px-6 py-2 bg-error text-white rounded-xl text-sm font-bold hover:bg-red-700 transition-colors">
                I understand, proceed
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Edit dialog */}
      {editKey && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-2xl max-w-lg w-full p-8 animate-fade-in">
            <h3 className="text-lg font-bold text-textMain mb-1">Update Secret</h3>
            <p className="text-xs text-textMuted mb-6">
              Enter the new value for <strong className="text-textMain font-black">{editKey}</strong>.
              The value will be stored securely and only a masked version will be shown.
            </p>
            <div className="space-y-1.5">
              <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">New Value</label>
              <div className="relative">
                <input
                  autoFocus
                  type={showValue ? 'text' : 'password'}
                  className="w-full px-4 pr-10 py-2.5 border border-border rounded-xl text-sm font-mono bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none"
                  placeholder="Paste new secret value…"
                  value={newValue}
                  onChange={e => setNewValue(e.target.value)}
                  onPaste={e => e.stopPropagation()}
                />
                <button
                  type="button"
                  onClick={() => setShowValue(v => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-textMuted hover:text-primary transition-colors"
                >
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    {showValue
                      ? <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3.98 8.223A10.477 10.477 0 001.934 12C3.226 16.338 7.244 19.5 12 19.5c.993 0 1.953-.138 2.863-.395M6.228 6.228A10.45 10.45 0 0112 4.5c4.756 0 8.773 3.162 10.065 7.498a10.523 10.523 0 01-4.293 5.774M6.228 6.228L3 3m3.228 3.228l3.65 3.65m7.894 7.894L21 21m-3.228-3.228l-3.65-3.65m0 0a3 3 0 10-4.243-4.243m4.242 4.242L9.88 9.88" />
                      : <><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M2.036 12.322a1.012 1.012 0 010-.644C3.399 8.049 7.21 5 12 5c4.79 0 8.601 3.049 9.964 6.678.14.373.14.76 0 1.134C20.601 15.951 16.79 19 12 19c-4.79 0-8.601-3.049-9.964-6.678z" /><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /></>
                    }
                  </svg>
                </button>
              </div>
              <p className="text-[10px] text-textMuted">Do not copy this value. Once saved, it cannot be retrieved — only replaced.</p>
            </div>
            <div className="flex gap-3 justify-end mt-6">
              <button onClick={() => { setEditKey(null); setNewValue(''); }} disabled={saving}
                className="px-5 py-2 rounded-xl text-sm font-bold text-textMuted hover:text-textMain transition-colors disabled:opacity-50">
                Cancel
              </button>
              <button onClick={handleSave} disabled={saving || !newValue.trim()}
                className="px-8 py-2 bg-primary text-white rounded-xl text-sm font-bold hover:bg-primaryDark transition-colors flex items-center gap-2 disabled:opacity-50">
                {saving && <div className="w-3.5 h-3.5 border-2 border-white/20 border-t-white rounded-full animate-spin" />}
                {saving ? 'Saving…' : 'Save Secret'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Secrets by category */}
      {Object.entries(grouped).map(([category, items]) => (
        <section key={category} className="bg-white rounded-2xl border border-border shadow-sm overflow-hidden">
          <div className="px-8 py-5 border-b border-border bg-slate-50 flex items-center gap-3">
            <span className={`px-2.5 py-0.5 rounded-md text-[10px] font-black uppercase tracking-widest ${CATEGORY_COLORS[category] || 'bg-slate-100 text-slate-700'}`}>
              {CATEGORY_LABELS[category] || category}
            </span>
            <span className="text-[10px] text-textMuted font-semibold">{items.length} secret{items.length !== 1 ? 's' : ''}</span>
          </div>

          <div className="divide-y divide-border">
            {items.map(secret => (
              <div key={secret.key} className="px-8 py-5 flex flex-col sm:flex-row sm:items-center gap-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <p className="text-sm font-black text-textMain font-mono">{secret.key}</p>
                    {secret.is_critical && (
                      <span className="px-1.5 py-0.5 bg-red-100 text-red-700 rounded text-[9px] font-black uppercase tracking-widest">Critical</span>
                    )}
                    {!secret.has_value && (
                      <span className="px-1.5 py-0.5 bg-amber-100 text-amber-700 rounded text-[9px] font-black uppercase tracking-widest">Not set</span>
                    )}
                  </div>
                  <p className="text-xs text-textMuted leading-relaxed mb-2">{secret.description}</p>
                  <div className="flex items-center gap-4">
                    <span className="text-xs font-mono text-textMuted tracking-widest">
                      {secret.has_value ? secret.masked_value || '••••••••••••' : '— not set —'}
                    </span>
                    {secret.updated_by_email && (
                      <span className="text-[10px] text-textMuted">
                        Updated by {secret.updated_by_email}
                        {secret.updated_at ? ` · ${new Date(secret.updated_at).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })}` : ''}
                      </span>
                    )}
                  </div>
                </div>
                <button
                  onClick={() => openEdit(secret.key, secret.is_critical)}
                  className={`shrink-0 flex items-center gap-2 px-4 py-1.5 rounded-lg text-xs font-bold border transition-colors ${
                    secret.is_critical
                      ? 'border-red-200 text-error hover:bg-red-50'
                      : 'border-border text-textMuted hover:bg-slate-50 hover:text-textMain'
                  }`}
                >
                  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                  </svg>
                  {secret.has_value ? 'Replace' : 'Set value'}
                </button>
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
};
