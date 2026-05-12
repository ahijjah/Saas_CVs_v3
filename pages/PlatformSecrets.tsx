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
  ai:       'AI / LLM',
  security: 'Security',
  email:    'Email',
  database: 'Database',
  queue:    'Queue / Cache',
  general:  'General',
};

const CATEGORY_COLORS: Record<string, string> = {
  ai:       'bg-purple-100 text-purple-800',
  security: 'bg-red-100 text-red-800',
  email:    'bg-blue-100 text-blue-800',
  database: 'bg-orange-100 text-orange-800',
  queue:    'bg-cyan-100 text-cyan-800',
  general:  'bg-slate-100 text-slate-700',
};

const CATEGORY_ORDER = ['security', 'ai', 'email', 'database', 'queue', 'general'];

export const PlatformSecretsPage: React.FC<Props> = ({ auth, addToast }) => {
  const [secrets, setSecrets]                   = useState<PlatformSecret[]>([]);
  const [loading, setLoading]                   = useState(true);
  const [editKey, setEditKey]                   = useState<string | null>(null);
  const [newValue, setNewValue]                 = useState('');
  const [showValue, setShowValue]               = useState(false);
  const [saving, setSaving]                     = useState(false);
  const [confirmSecret, setConfirmSecret]       = useState<PlatformSecret | null>(null);
  const [validationError, setValidationError]   = useState<string | null>(null);
  // Restart state — enabled only after a restart_required secret is successfully updated
  const [restartPending, setRestartPending]     = useState(false);
  const [confirmRestart, setConfirmRestart]     = useState(false);
  const [restarting, setRestarting]             = useState(false);

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

  const openEdit = (secret: PlatformSecret) => {
    if (secret.is_critical) {
      setConfirmSecret(secret);
    } else {
      startEdit(secret.key);
    }
  };

  const startEdit = (key: string) => {
    setEditKey(key);
    setNewValue('');
    setShowValue(false);
    setValidationError(null);
    setConfirmSecret(null);
  };

  const handleValueChange = (val: string, minLen: number) => {
    setNewValue(val);
    if (val.trim().length > 0 && val.trim().length < minLen) {
      setValidationError(`Minimum length: ${minLen} characters (${val.trim().length} entered)`);
    } else {
      setValidationError(null);
    }
  };

  const handleSave = async () => {
    const secret = secrets.find(s => s.key === editKey);
    if (!editKey || !newValue.trim()) return;
    if (secret && newValue.trim().length < secret.min_length) {
      setValidationError(`Minimum length: ${secret.min_length} characters`);
      return;
    }
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
        // Enable restart button if this secret requires a service restart
        if (res.restart_required || secret?.restart_required) {
          setRestartPending(true);
        }
        setEditKey(null);
        setNewValue('');
        setValidationError(null);
      }
    } catch (err: any) {
      addToast(err.message || 'Failed to update secret.', 'error');
    } finally {
      setSaving(false);
    }
  };

  const handleRestart = async () => {
    setConfirmRestart(false);
    setRestarting(true);
    try {
      const res = await apiService.post(
        WEBHOOK_CONFIG.PLATFORM_SECRETS_RESTART_URL,
        {},
        auth.token!,
      );
      if (res?.success) {
        addToast(res.message || 'Backend services restart requested successfully.', 'success');
        setRestartPending(false);
      } else {
        // success=false means manual restart required — show as info, not error
        addToast(res?.message || 'Restart must be performed manually from the server.', 'info' as ToastType);
        // Keep restartPending=true so the user knows a restart is still needed
      }
    } catch (err: any) {
      addToast(err.message || 'Failed to request restart.', 'error');
    } finally {
      setRestarting(false);
    }
  };

  const grouped = secrets.reduce<Record<string, PlatformSecret[]>>((acc, s) => {
    (acc[s.category] = acc[s.category] || []).push(s);
    return acc;
  }, {});

  const orderedCategories = [
    ...CATEGORY_ORDER.filter(c => grouped[c]),
    ...Object.keys(grouped).filter(c => !CATEGORY_ORDER.includes(c)),
  ];

  const editingSecret = secrets.find(s => s.key === editKey);

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

      {/* Runtime note banner */}
      <div className="bg-amber-50 border border-amber-200 rounded-xl px-6 py-4 flex gap-3">
        <svg className="w-5 h-5 text-amber-500 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
        </svg>
        <div>
          <p className="text-sm font-bold text-amber-800">Runtime secrets are read from environment variables</p>
          <p className="text-xs text-amber-700 mt-0.5 leading-relaxed">
            The service loads all secrets from <strong>environment variables</strong> at startup.
            Updating a value here records it in the database for audit purposes, but{' '}
            <strong>the running service will not use the new value until you also update
            the environment variable and restart the service.</strong>
          </p>
        </div>
      </div>

      {/* Restart required banner — shown after a restart_required secret is updated */}
      {restartPending && (
        <div className="bg-orange-50 border border-orange-300 rounded-xl px-6 py-4 flex items-start justify-between gap-4">
          <div className="flex gap-3">
            <svg className="w-5 h-5 text-orange-500 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
            <div>
              <p className="text-sm font-bold text-orange-800">Some changes require backend services to restart before they take effect.</p>
              <p className="text-xs text-orange-700 mt-0.5">
                One or more secrets marked as <strong>restart required</strong> were updated.
                Use the button to request a controlled restart, or restart services manually from the server.
              </p>
            </div>
          </div>
          <button
            onClick={() => setConfirmRestart(true)}
            disabled={restarting}
            className="shrink-0 flex items-center gap-2 px-5 py-2 bg-orange-600 text-white rounded-xl text-sm font-bold hover:bg-orange-700 transition-colors disabled:opacity-50 shadow-sm"
          >
            {restarting
              ? <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
              : <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                </svg>
            }
            {restarting ? 'Requesting…' : 'Restart Backend Services'}
          </button>
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {(['total', 'configured', 'missing', 'critical'] as const).map(stat => {
          const count =
            stat === 'configured' ? secrets.filter(s => s.has_value).length :
            stat === 'missing'    ? secrets.filter(s => !s.has_value).length :
            stat === 'critical'   ? secrets.filter(s => s.is_critical).length :
            secrets.length;
          const color =
            stat === 'configured' ? 'text-green-600' :
            stat === 'missing'    ? 'text-amber-600' :
            stat === 'critical'   ? 'text-error'     : 'text-textMain';
          return (
            <div key={stat} className="bg-white rounded-xl border border-border p-4">
              <p className={`text-2xl font-black ${color}`}>{count}</p>
              <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mt-0.5 capitalize">{stat}</p>
            </div>
          );
        })}
      </div>

      {/* Restart confirmation modal */}
      {confirmRestart && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-2xl max-w-md w-full p-8 animate-fade-in">
            <div className="flex items-center gap-3 mb-4">
              <div className="w-10 h-10 rounded-full bg-orange-100 flex items-center justify-center shrink-0">
                <svg className="w-5 h-5 text-orange-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                </svg>
              </div>
              <h3 className="text-base font-bold text-textMain">Restart Backend Services?</h3>
            </div>
            <div className="bg-orange-50 border border-orange-200 rounded-lg px-4 py-3 mb-5">
              <p className="text-xs text-orange-700 leading-relaxed">
                This will restart the backend <strong>API, worker, and beat</strong> services.
                The system may be unavailable for a short period while services come back up.
                Active scoring jobs may be interrupted.
              </p>
            </div>
            <ul className="text-xs text-textMuted space-y-1.5 mb-6 list-disc list-inside">
              <li>Logged-in users may need to refresh their browser.</li>
              <li>CV scoring jobs in progress may need to be re-queued.</li>
              <li>Services will restart with <strong>environment variables</strong> — ensure they have been updated separately.</li>
            </ul>
            <div className="flex gap-3 justify-end">
              <button onClick={() => setConfirmRestart(false)}
                className="px-5 py-2 rounded-xl text-sm font-bold text-textMuted hover:text-textMain transition-colors">
                Cancel
              </button>
              <button onClick={handleRestart}
                className="px-6 py-2 bg-orange-600 text-white rounded-xl text-sm font-bold hover:bg-orange-700 transition-colors">
                Restart Now
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Critical confirmation dialog */}
      {confirmSecret && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-2xl max-w-md w-full p-8 animate-fade-in">
            <div className="flex items-center gap-3 mb-4">
              <div className="w-10 h-10 rounded-full bg-red-100 flex items-center justify-center shrink-0">
                <svg className="w-5 h-5 text-error" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
                </svg>
              </div>
              <div>
                <h3 className="text-base font-bold text-textMain">Critical Secret Warning</h3>
                <p className="text-xs font-mono text-textMuted">{confirmSecret.key}</p>
              </div>
            </div>
            <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3 mb-5">
              <p className="text-xs text-red-700 leading-relaxed">
                {confirmSecret.critical_warning ||
                  `Changing ${confirmSecret.key} is a critical operation that may immediately affect service availability.`}
              </p>
            </div>
            <ul className="text-xs text-textMuted space-y-1.5 mb-6 list-disc list-inside">
              <li>A service restart will be required for the new value to take effect.</li>
              <li>Ensure the new value is correct before saving — you cannot retrieve the old value.</li>
              <li>Coordinate any restart with active users to minimise disruption.</li>
            </ul>
            <div className="flex gap-3 justify-end">
              <button onClick={() => setConfirmSecret(null)}
                className="px-5 py-2 rounded-xl text-sm font-bold text-textMuted hover:text-textMain transition-colors">
                Cancel
              </button>
              <button onClick={() => startEdit(confirmSecret.key)}
                className="px-6 py-2 bg-error text-white rounded-xl text-sm font-bold hover:bg-red-700 transition-colors">
                I understand, proceed
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Edit dialog */}
      {editKey && editingSecret && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-2xl max-w-lg w-full p-8 animate-fade-in">
            <div className="flex items-start gap-3 mb-5">
              <div className={`w-10 h-10 rounded-full flex items-center justify-center shrink-0 ${editingSecret.is_critical ? 'bg-red-100' : 'bg-slate-100'}`}>
                <svg className={`w-5 h-5 ${editingSecret.is_critical ? 'text-error' : 'text-textMuted'}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" />
                </svg>
              </div>
              <div>
                <h3 className="text-base font-bold text-textMain">Update Secret</h3>
                <p className="text-xs font-mono text-textMuted">{editKey}</p>
              </div>
            </div>

            <p className="text-xs text-textMuted mb-4 leading-relaxed">{editingSecret.description}</p>

            {editingSecret.restart_required && (
              <div className="flex items-center gap-2 px-3 py-2 bg-amber-50 border border-amber-200 rounded-lg mb-5">
                <svg className="w-3.5 h-3.5 text-amber-500 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                </svg>
                <p className="text-[11px] font-semibold text-amber-700">
                  Service restart required — runtime reads from the <strong>environment variable</strong>.
                </p>
              </div>
            )}

            <div className="space-y-1.5">
              <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">
                New Value
                {editingSecret.min_length > 1 && (
                  <span className="ml-1 normal-case font-normal text-textMuted">(min {editingSecret.min_length} chars)</span>
                )}
              </label>
              <div className="relative">
                <input
                  autoFocus
                  type={showValue ? 'text' : 'password'}
                  className={`w-full px-4 pr-10 py-2.5 border rounded-xl text-sm font-mono bg-white outline-none transition-colors ${
                    validationError
                      ? 'border-error focus:ring-2 focus:ring-error/20 focus:border-error'
                      : 'border-border focus:ring-2 focus:ring-primary/20 focus:border-primary'
                  }`}
                  placeholder="Paste new secret value…"
                  value={newValue}
                  onChange={e => handleValueChange(e.target.value, editingSecret.min_length)}
                  onPaste={e => e.stopPropagation()}
                />
                <button type="button" onClick={() => setShowValue(v => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-textMuted hover:text-primary transition-colors">
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    {showValue
                      ? <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3.98 8.223A10.477 10.477 0 001.934 12C3.226 16.338 7.244 19.5 12 19.5c.993 0 1.953-.138 2.863-.395M6.228 6.228A10.45 10.45 0 0112 4.5c4.756 0 8.773 3.162 10.065 7.498a10.523 10.523 0 01-4.293 5.774M6.228 6.228L3 3m3.228 3.228l3.65 3.65m7.894 7.894L21 21m-3.228-3.228l-3.65-3.65m0 0a3 3 0 10-4.243-4.243m4.242 4.242L9.88 9.88" />
                      : <><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M2.036 12.322a1.012 1.012 0 010-.644C3.399 8.049 7.21 5 12 5c4.79 0 8.601 3.049 9.964 6.678.14.373.14.76 0 1.134C20.601 15.951 16.79 19 12 19c-4.79 0-8.601-3.049-9.964-6.678z" /><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /></>
                    }
                  </svg>
                </button>
              </div>
              {validationError
                ? <p className="text-[11px] text-error font-medium">{validationError}</p>
                : <p className="text-[10px] text-textMuted">Value is stored masked. Once saved it cannot be retrieved — only replaced.</p>
              }
            </div>

            <div className="flex gap-3 justify-end mt-6">
              <button
                onClick={() => { setEditKey(null); setNewValue(''); setValidationError(null); }}
                disabled={saving}
                className="px-5 py-2 rounded-xl text-sm font-bold text-textMuted hover:text-textMain transition-colors disabled:opacity-50">
                Cancel
              </button>
              <button
                onClick={handleSave}
                disabled={saving || !newValue.trim() || !!validationError}
                className={`px-8 py-2 rounded-xl text-sm font-bold flex items-center gap-2 disabled:opacity-50 transition-colors ${
                  editingSecret.is_critical ? 'bg-error text-white hover:bg-red-700' : 'bg-primary text-white hover:bg-primaryDark'
                }`}
              >
                {saving && <div className="w-3.5 h-3.5 border-2 border-white/20 border-t-white rounded-full animate-spin" />}
                {saving ? 'Saving…' : 'Save Secret'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Secrets by category */}
      {orderedCategories.map((category) => {
        const items = grouped[category] || [];
        const configuredCount = items.filter(s => s.has_value).length;
        const missingCount = items.length - configuredCount;
        return (
          <section key={category} className="bg-white rounded-2xl border border-border shadow-sm overflow-hidden">
            <div className="px-8 py-5 border-b border-border bg-slate-50 flex items-center gap-3 flex-wrap">
              <span className={`px-2.5 py-0.5 rounded-md text-[10px] font-black uppercase tracking-widest ${CATEGORY_COLORS[category] || 'bg-slate-100 text-slate-700'}`}>
                {CATEGORY_LABELS[category] || category}
              </span>
              <span className="text-[10px] text-textMuted font-semibold">{items.length} secret{items.length !== 1 ? 's' : ''}</span>
              <span className="text-[10px] text-textMuted">·</span>
              <span className="text-[10px] text-green-600 font-semibold">{configuredCount} configured</span>
              {missingCount > 0 && (
                <>
                  <span className="text-[10px] text-textMuted">·</span>
                  <span className="text-[10px] text-amber-600 font-semibold">{missingCount} missing</span>
                </>
              )}
            </div>

            <div className="divide-y divide-border">
              {items.map(secret => (
                <div key={secret.key} className={`px-8 py-5 flex flex-col sm:flex-row sm:items-start gap-4 ${!secret.has_value ? 'bg-amber-50/30' : ''}`}>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                      <p className="text-sm font-black text-textMain font-mono">{secret.key}</p>
                      {secret.is_critical && (
                        <span className="px-1.5 py-0.5 bg-red-100 text-red-700 rounded text-[9px] font-black uppercase tracking-widest">Critical</span>
                      )}
                      {!secret.has_value && (
                        <span className="px-1.5 py-0.5 bg-amber-100 text-amber-700 rounded text-[9px] font-black uppercase tracking-widest">Not set</span>
                      )}
                      <span className="px-1.5 py-0.5 bg-slate-100 text-slate-600 rounded text-[9px] font-black uppercase tracking-widest">
                        {secret.source === 'env' ? 'From ENV' : 'From DB'}
                      </span>
                      {secret.restart_required && (
                        <span className="px-1.5 py-0.5 bg-amber-50 text-amber-600 border border-amber-200 rounded text-[9px] font-bold">
                          ↻ Restart needed
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-textMuted leading-relaxed mb-2">{secret.description}</p>
                    <div className="flex items-center gap-4 flex-wrap">
                      <span className={`text-xs font-mono tracking-widest ${secret.has_value ? 'text-textMuted' : 'text-amber-500 italic'}`}>
                        {secret.has_value ? (secret.masked_value || '••••••••••••') : '— not set —'}
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
                    onClick={() => openEdit(secret)}
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
        );
      })}
    </div>
  );
};
