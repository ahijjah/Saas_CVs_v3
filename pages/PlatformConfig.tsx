import React, { useState, useEffect, useCallback } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { AuthState, PlatformConfig as PlatformConfigItem } from '../types';

interface Props {
  auth: AuthState;
  addToast: (msg: string, type: 'success' | 'error' | 'info') => void;
}

const T = {
  en: {
    title: 'Platform Configuration',
    subtitle: 'Manage system-wide operational parameters',
    searchPlaceholder: 'Search parameters...',
    allCategories: 'All Categories',
    key: 'Key', value: 'Value', type: 'Type', category: 'Category',
    description: 'Description', lastUpdated: 'Last Updated', updatedBy: 'By',
    actions: 'Actions', edit: 'Edit', save: 'Save', cancel: 'Cancel',
    add: 'Add Parameter', addTitle: 'Add New Parameter',
    keyLabel: 'Key (lowercase, underscores only)',
    valueLabel: 'Value', typeLabel: 'Type', categoryLabel: 'Category',
    descriptionLabel: 'Description', editableLabel: 'Editable',
    readOnly: 'Read-only', editable: 'Editable',
    saving: 'Saving...', adding: 'Adding...',
    saveSuccess: 'Parameter updated', addSuccess: 'Parameter added',
    errorLoad: 'Failed to load configuration',
    errorSave: 'Failed to save parameter',
    never: 'Never',
    categories: {
      scoring: 'Scoring', ai: 'AI', email: 'Email',
      queue: 'Queue', subscription: 'Subscription',
      security: 'Security', general: 'General',
    } as Record<string, string>,
  },
  ar: {
    title: 'إعدادات المنصة',
    subtitle: 'إدارة المعاملات التشغيلية على مستوى النظام',
    searchPlaceholder: 'بحث في المعاملات...',
    allCategories: 'كل الفئات',
    key: 'المفتاح', value: 'القيمة', type: 'النوع', category: 'الفئة',
    description: 'الوصف', lastUpdated: 'آخر تحديث', updatedBy: 'بواسطة',
    actions: 'إجراءات', edit: 'تعديل', save: 'حفظ', cancel: 'إلغاء',
    add: 'إضافة معامل', addTitle: 'إضافة معامل جديد',
    keyLabel: 'المفتاح (حروف صغيرة وشرطة سفلية)',
    valueLabel: 'القيمة', typeLabel: 'النوع', categoryLabel: 'الفئة',
    descriptionLabel: 'الوصف', editableLabel: 'قابل للتعديل',
    readOnly: 'للقراءة فقط', editable: 'قابل للتعديل',
    saving: 'جاري الحفظ...', adding: 'جاري الإضافة...',
    saveSuccess: 'تم تحديث المعامل', addSuccess: 'تمت إضافة المعامل',
    errorLoad: 'فشل تحميل الإعدادات',
    errorSave: 'فشل حفظ المعامل',
    never: 'لم يتم',
    categories: {
      scoring: 'التقييم', ai: 'الذكاء الاصطناعي', email: 'البريد الإلكتروني',
      queue: 'قائمة الانتظار', subscription: 'الاشتراك',
      security: 'الأمان', general: 'عام',
    } as Record<string, string>,
  },
};

const CATEGORIES = ['scoring', 'ai', 'email', 'queue', 'subscription', 'security', 'general'];
const TYPES = ['string', 'number', 'boolean', 'json'];

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

const EMPTY_NEW: Omit<PlatformConfigItem, 'updated_at' | 'updated_by' | 'updated_by_email'> = {
  key: '', value: '', type: 'string', category: 'general', description: '', editable: true,
};

export const PlatformConfigPage: React.FC<Props> = ({ auth, addToast }) => {
  const lang = (document.documentElement.lang as 'en' | 'ar') === 'ar' ? 'ar' : 'en';
  const t = T[lang];

  const [configs, setConfigs] = useState<PlatformConfigItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [catFilter, setCatFilter] = useState('all');
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [editValue, setEditValue] = useState('');
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [newItem, setNewItem] = useState({ ...EMPTY_NEW });
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiService.get(WEBHOOK_CONFIG.PLATFORM_CONFIG_URL, {}, auth.token!);
      setConfigs(data.configs || []);
    } catch (err: any) {
      addToast(err.message || t.errorLoad, 'error');
    } finally {
      setLoading(false);
    }
  }, [auth.token, t.errorLoad]);

  useEffect(() => { load(); }, [load]);

  const startEdit = (item: PlatformConfigItem) => {
    if (!item.editable) return;
    setEditingKey(item.key);
    setEditValue(item.value);
  };

  const cancelEdit = () => { setEditingKey(null); setEditValue(''); };

  const saveEdit = async (key: string) => {
    setSavingKey(key);
    try {
      await apiService.put(
        `${WEBHOOK_CONFIG.PLATFORM_CONFIG_URL}/${key}`,
        { value: editValue },
        auth.token!,
      );
      addToast(t.saveSuccess, 'success');
      setEditingKey(null);
      await load();
    } catch (err: any) {
      addToast(err.message || t.errorSave, 'error');
    } finally {
      setSavingKey(null);
    }
  };

  const handleAdd = async () => {
    setAdding(true);
    try {
      await apiService.post(WEBHOOK_CONFIG.PLATFORM_CONFIG_URL, newItem, auth.token!);
      addToast(t.addSuccess, 'success');
      setShowAdd(false);
      setNewItem({ ...EMPTY_NEW });
      await load();
    } catch (err: any) {
      addToast(err.message || t.errorSave, 'error');
    } finally {
      setAdding(false);
    }
  };

  const filtered = configs.filter((c) => {
    const matchCat = catFilter === 'all' || c.category === catFilter;
    const q = search.toLowerCase();
    const matchSearch = !q || c.key.includes(q) || (c.description || '').toLowerCase().includes(q) || c.value.toLowerCase().includes(q);
    return matchCat && matchSearch;
  });

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center h-64 space-y-4">
        <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-indigo-600" />
        <p className="text-textMuted animate-pulse font-bold uppercase tracking-widest text-xs">Loading...</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h2 className="text-xl font-black text-textMain">{t.title}</h2>
          <p className="text-xs text-textMuted mt-0.5">{t.subtitle}</p>
        </div>
        <button
          onClick={() => setShowAdd(true)}
          className="flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white text-sm font-bold rounded-xl hover:bg-indigo-700 transition-all shadow-md"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 4v16m8-8H4" />
          </svg>
          {t.add}
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-col sm:flex-row gap-3">
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t.searchPlaceholder}
          className="flex-1 px-4 py-2.5 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-indigo-300"
        />
        <select
          value={catFilter}
          onChange={(e) => setCatFilter(e.target.value)}
          className="px-4 py-2.5 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-indigo-300 bg-white"
        >
          <option value="all">{t.allCategories}</option>
          {CATEGORIES.map((c) => (
            <option key={c} value={c}>{t.categories[c]}</option>
          ))}
        </select>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {CATEGORIES.slice(0, 4).map((cat) => {
          const count = configs.filter((c) => c.category === cat).length;
          return (
            <div key={cat} className="bg-white rounded-xl border border-border p-3 flex items-center justify-between">
              <div>
                <p className="text-lg font-black text-textMain">{count}</p>
                <p className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.categories[cat]}</p>
              </div>
              <span className={`px-2 py-0.5 rounded-full text-[10px] font-black uppercase ${CAT_COLORS[cat]}`}>
                {cat}
              </span>
            </div>
          );
        })}
      </div>

      {/* Table */}
      <div className="bg-white rounded-2xl border border-border shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left min-w-[900px]">
            <thead className="bg-slate-50 border-b border-border">
              <tr>
                {[t.key, t.category, t.type, t.value, t.description, t.lastUpdated, t.actions].map((h) => (
                  <th key={h} className="px-5 py-3.5 text-[10px] font-black text-textMuted uppercase tracking-widest">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-5 py-8 text-center text-sm text-textMuted">
                    No parameters found
                  </td>
                </tr>
              )}
              {filtered.map((item) => (
                <tr key={item.key} className="hover:bg-slate-50 transition-colors">
                  <td className="px-5 py-3.5">
                    <div className="flex items-center gap-2">
                      <code className="text-xs font-bold text-indigo-700 bg-indigo-50 px-2 py-0.5 rounded">
                        {item.key}
                      </code>
                      {!item.editable && (
                        <span className="text-[9px] bg-slate-100 text-slate-500 px-1.5 py-0.5 rounded font-black uppercase">
                          {t.readOnly}
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-5 py-3.5">
                    <span className={`px-2 py-0.5 rounded-full text-[10px] font-black uppercase ${CAT_COLORS[item.category] || ''}`}>
                      {t.categories[item.category] || item.category}
                    </span>
                  </td>
                  <td className="px-5 py-3.5">
                    <span className={`px-2 py-0.5 rounded-full text-[10px] font-black ${TYPE_COLORS[item.type] || ''}`}>
                      {item.type}
                    </span>
                  </td>
                  <td className="px-5 py-3.5 max-w-[200px]">
                    {editingKey === item.key ? (
                      <input
                        autoFocus
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        className="w-full px-3 py-1.5 text-sm border border-indigo-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-300"
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') saveEdit(item.key);
                          if (e.key === 'Escape') cancelEdit();
                        }}
                      />
                    ) : (
                      <code className="text-xs text-textMain font-medium truncate block max-w-[180px]" title={item.value}>
                        {item.value}
                      </code>
                    )}
                  </td>
                  <td className="px-5 py-3.5 max-w-[220px]">
                    <p className="text-xs text-textMuted truncate" title={item.description || ''}>
                      {item.description || '—'}
                    </p>
                  </td>
                  <td className="px-5 py-3.5 whitespace-nowrap">
                    <p className="text-xs text-textMuted">
                      {item.updated_at ? new Date(item.updated_at).toLocaleDateString() : t.never}
                    </p>
                    {item.updated_by_email && (
                      <p className="text-[10px] text-textMuted truncate max-w-[120px]" title={item.updated_by_email}>
                        {item.updated_by_email}
                      </p>
                    )}
                  </td>
                  <td className="px-5 py-3.5">
                    {editingKey === item.key ? (
                      <div className="flex items-center gap-2">
                        <button
                          disabled={savingKey === item.key}
                          onClick={() => saveEdit(item.key)}
                          className="text-xs font-bold px-3 py-1 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50"
                        >
                          {savingKey === item.key ? t.saving : t.save}
                        </button>
                        <button
                          onClick={cancelEdit}
                          className="text-xs font-bold px-3 py-1 border border-border text-textMuted rounded-lg hover:bg-slate-50"
                        >
                          {t.cancel}
                        </button>
                      </div>
                    ) : (
                      item.editable && (
                        <button
                          onClick={() => startEdit(item)}
                          className="text-xs font-bold px-3 py-1 border border-indigo-200 text-indigo-600 rounded-lg hover:bg-indigo-50 transition-all"
                        >
                          {t.edit}
                        </button>
                      )
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Add Modal */}
      {showAdd && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4 backdrop-blur-sm">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg">
            <div className="px-6 py-4 border-b border-border flex items-center justify-between">
              <h3 className="font-black text-textMain text-sm uppercase tracking-widest">{t.addTitle}</h3>
              <button onClick={() => setShowAdd(false)} className="text-textMuted hover:text-textMain p-1">
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <div className="p-6 space-y-4">
              <div>
                <label className="block text-xs font-black text-textMuted uppercase tracking-widest mb-1.5">{t.keyLabel}</label>
                <input
                  value={newItem.key}
                  onChange={(e) => setNewItem({ ...newItem, key: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, '') })}
                  className="w-full px-4 py-2.5 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-indigo-300"
                  placeholder="e.g. my_parameter"
                />
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-black text-textMuted uppercase tracking-widest mb-1.5">{t.typeLabel}</label>
                  <select
                    value={newItem.type}
                    onChange={(e) => setNewItem({ ...newItem, type: e.target.value })}
                    className="w-full px-4 py-2.5 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-indigo-300 bg-white"
                  >
                    {TYPES.map((t_) => <option key={t_} value={t_}>{t_}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-black text-textMuted uppercase tracking-widest mb-1.5">{t.categoryLabel}</label>
                  <select
                    value={newItem.category}
                    onChange={(e) => setNewItem({ ...newItem, category: e.target.value })}
                    className="w-full px-4 py-2.5 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-indigo-300 bg-white"
                  >
                    {CATEGORIES.map((c) => <option key={c} value={c}>{T.en.categories[c]}</option>)}
                  </select>
                </div>
              </div>
              <div>
                <label className="block text-xs font-black text-textMuted uppercase tracking-widest mb-1.5">{t.valueLabel}</label>
                <input
                  value={newItem.value}
                  onChange={(e) => setNewItem({ ...newItem, value: e.target.value })}
                  className="w-full px-4 py-2.5 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-indigo-300"
                />
              </div>
              <div>
                <label className="block text-xs font-black text-textMuted uppercase tracking-widest mb-1.5">{t.descriptionLabel}</label>
                <textarea
                  rows={2}
                  value={newItem.description}
                  onChange={(e) => setNewItem({ ...newItem, description: e.target.value })}
                  className="w-full px-4 py-2.5 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-indigo-300 resize-none"
                />
              </div>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={newItem.editable}
                  onChange={(e) => setNewItem({ ...newItem, editable: e.target.checked })}
                  className="w-4 h-4 rounded text-indigo-600"
                />
                <span className="text-sm font-medium text-textMain">{t.editableLabel}</span>
              </label>
            </div>
            <div className="px-6 py-4 border-t border-border flex justify-end gap-3">
              <button
                onClick={() => setShowAdd(false)}
                className="px-4 py-2 text-sm font-bold border border-border text-textMuted rounded-xl hover:bg-slate-50"
              >
                {t.cancel}
              </button>
              <button
                disabled={adding || !newItem.key || !newItem.value}
                onClick={handleAdd}
                className="px-4 py-2 text-sm font-bold bg-indigo-600 text-white rounded-xl hover:bg-indigo-700 disabled:opacity-50"
              >
                {adding ? t.adding : t.add}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
