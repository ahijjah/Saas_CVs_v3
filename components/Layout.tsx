
import React, { useState } from 'react';
import { useLocation, useNavigate, Outlet } from 'react-router-dom';
import { User } from '../types';
import { useLanguage } from '../context/LanguageContext';
import { usePageTitle } from '../context/PageTitleContext';
import { useFeatures } from '../context/FeatureContext';

interface LayoutProps {
  user: User | null;
  onLogout: () => void;
  /** Optional children; if omitted, <Outlet /> is used (React Router layout route). */
  children?: React.ReactNode;
}

const T = {
  en: {
    dashboard: 'Home', jobs: 'Jobs', campaigns: 'Campaigns', candidates: 'Candidates', talentPool: 'Talent Pool', analytics: 'Analytics', settings: 'Settings', planUsage: 'Plan & Usage',
    clientOrgs: 'Client Organizations', clients: 'Clients',
    sysAdmin: 'System Admin', tenantMgmt: 'Tenant Management',
    platformControl: 'Platform Control', workspaceSection: 'Workspace', accountSection: 'Account',
    logout: 'Logout', superAdmin: 'Super Admin', usersGlobal: 'Users Global',
    platformConfig: 'Platform Config', subscriptionPlans: 'Subscription Plans',
    tenantSubscriptions: 'Tenant Subscriptions',
    secrets: 'Secrets & Credentials', aiPrompts: 'AI Prompts', auditLogs: 'Audit Logs',
    aiUsage: 'AI Usage & Cost', aiModels: 'AI Models',
    tenantFeatures: 'Tenant Features',
    organization: 'Organization', langBtn: 'عربي',
    pageTitles: {
      '/jobs':                        'Jobs',
      '/jobs/:id':                    'Job Details',
      '/campaigns':                   'Campaigns',
      '/candidates':                  'Candidates',
      '/talent-pool':                 'Talent Pool',
      '/analytics':                   'Analytics',
      '/applications':                'Applications',
      '/applications/details':        'Application Details',
      '/settings':                    'Settings',
      '/plan-usage':                  'Plan & Usage',
      '/client-organizations':        'Client Organizations',
      '/admin/dashboard':             'Super Admin',
      '/admin/users':                 'Users Global',
      '/admin/platform-config':       'Platform Config',
      '/admin/subscription-plans':    'Subscription Plans',
      '/admin/tenants':               'Tenant Subscriptions',
      '/admin/platform-secrets':      'Secrets & Credentials',
      '/admin/ai-prompts':            'AI Prompts',
      '/admin/audit-logs':            'Audit Logs',
      '/admin/ai-usage':              'AI Usage & Cost',
      '/admin/ai-models':             'AI Models',
      '/admin/tenant-features':       'Tenant Features',
    } as Record<string, string>,
  },
  ar: {
    dashboard: 'الرئيسية', jobs: 'الوظائف', campaigns: 'الحملات', candidates: 'المرشحون', talentPool: 'مجمع المواهب', analytics: 'التحليلات', settings: 'الإعدادات', planUsage: 'الخطة والاستخدام',
    clientOrgs: 'منظمات العملاء', clients: 'العملاء',
    sysAdmin: 'مشرف النظام', tenantMgmt: 'إدارة المستأجر',
    platformControl: 'التحكم بالمنصة', workspaceSection: 'مساحة العمل', accountSection: 'الحساب',
    logout: 'تسجيل الخروج', superAdmin: 'المشرف العام', usersGlobal: 'المستخدمون',
    platformConfig: 'إعدادات المنصة', subscriptionPlans: 'خطط الاشتراك',
    tenantSubscriptions: 'اشتراكات المستأجرين',
    secrets: 'المفاتيح والبيانات السرية', aiPrompts: 'موجهات الذكاء الاصطناعي', auditLogs: 'سجل التدقيق',
    aiUsage: 'استخدام الذكاء الاصطناعي والتكلفة', aiModels: 'نماذج الذكاء الاصطناعي',
    tenantFeatures: 'ميزات المستأجرين',
    organization: 'المنظمة', langBtn: 'English',
    pageTitles: {
      '/jobs':                        'الوظائف',
      '/jobs/:id':                    'تفاصيل الوظيفة',
      '/campaigns':                   'الحملات',
      '/candidates':                  'المرشحون',
      '/talent-pool':                 'مجمع المواهب',
      '/analytics':                   'التحليلات',
      '/applications':                'الطلبات',
      '/applications/details':        'تفاصيل الطلب',
      '/settings':                    'الإعدادات',
      '/plan-usage':                  'الخطة والاستخدام',
      '/client-organizations':        'منظمات العملاء',
      '/admin/dashboard':             'المشرف العام',
      '/admin/users':                 'المستخدمون',
      '/admin/platform-config':       'إعدادات المنصة',
      '/admin/subscription-plans':    'خطط الاشتراك',
      '/admin/tenants':               'اشتراكات المستأجرين',
      '/admin/platform-secrets':      'المفاتيح السرية',
      '/admin/ai-prompts':            'موجهات الذكاء الاصطناعي',
      '/admin/audit-logs':            'سجل التدقيق',
      '/admin/ai-usage':              'استخدام الذكاء الاصطناعي والتكلفة',
      '/admin/ai-models':             'نماذج الذكاء الاصطناعي',
      '/admin/tenant-features':       'ميزات المستأجرين',
    } as Record<string, string>,
  },
};

/** Map current URL pathname → sidebar menu item ID (for active highlighting). */
function pathnameToMenuId(pathname: string): string {
  if (pathname.startsWith('/admin/dashboard')) return 'admin-dashboard';
  if (pathname.startsWith('/admin/users'))     return 'admin-users';
  if (pathname.startsWith('/admin/platform-config'))    return 'admin-platform-config';
  if (pathname.startsWith('/admin/subscription-plans')) return 'admin-subscription-plans';
  if (pathname.startsWith('/admin/tenants'))            return 'admin-tenant-subscriptions';
  if (pathname.startsWith('/admin/platform-secrets'))   return 'admin-platform-secrets';
  if (pathname.startsWith('/admin/ai-prompts'))         return 'admin-ai-prompts';
  if (pathname.startsWith('/admin/audit-logs'))         return 'admin-audit-logs';
  if (pathname.startsWith('/admin/ai-usage'))           return 'admin-ai-usage';
  if (pathname.startsWith('/admin/ai-models'))          return 'admin-ai-models';
  if (pathname.startsWith('/admin/tenant-features'))    return 'admin-tenant-features';
  if (pathname === '/plan-usage')          return 'plan-usage';
  if (pathname === '/client-organizations') return 'client-organizations';
  if (pathname === '/dashboard')           return 'dashboard';
  if (pathname === '/candidates')          return 'candidates';
  if (pathname === '/talent-pool')         return 'talent-pool';
  if (pathname === '/analytics')           return 'analytics';
  if (pathname.startsWith('/campaigns'))   return 'campaigns';
  if (pathname === '/settings')            return 'settings';
  // /jobs, /jobs/:id, /applications all highlight the Jobs menu item
  return 'jobs';
}

/** Map sidebar menu item ID → route URL. */
function menuIdToRoute(id: string): string {
  const map: Record<string, string> = {
    'dashboard':                  '/dashboard',
    'candidates':                 '/candidates',
    'talent-pool':                '/talent-pool',
    'analytics':                  '/analytics',
    'jobs':                       '/jobs',
    'campaigns':                  '/campaigns',
    'plan-usage':                 '/plan-usage',
    'client-organizations':       '/client-organizations',
    'settings':                   '/settings',
    'admin-dashboard':            '/admin/dashboard',
    'admin-users':                '/admin/users',
    'admin-platform-config':      '/admin/platform-config',
    'admin-subscription-plans':   '/admin/subscription-plans',
    'admin-tenant-subscriptions': '/admin/tenants',
    'admin-platform-secrets':     '/admin/platform-secrets',
    'admin-ai-prompts':           '/admin/ai-prompts',
    'admin-audit-logs':           '/admin/audit-logs',
    'admin-ai-usage':             '/admin/ai-usage',
    'admin-ai-models':            '/admin/ai-models',
    'admin-tenant-features':      '/admin/tenant-features',
  };
  return map[id] || '/dashboard';
}

/** Derive a human-readable page title from the current pathname + search string. */
function pathnameToTitle(pathname: string, search: string, titles: Record<string, string>): string {
  // Exact and prefix matches
  if (titles[pathname]) return titles[pathname];
  // /jobs/:jobId → "Job Details"
  if (/^\/jobs\/[^/]+$/.test(pathname)) return titles['/jobs/:id'] || 'Job Details';
  if (pathname.startsWith('/admin/dashboard'))            return titles['/admin/dashboard'] || '';
  if (pathname.startsWith('/admin/users'))                return titles['/admin/users'] || '';
  if (pathname.startsWith('/admin/platform-config'))      return titles['/admin/platform-config'] || '';
  if (pathname.startsWith('/admin/subscription-plans'))   return titles['/admin/subscription-plans'] || '';
  if (pathname.startsWith('/admin/tenants'))              return titles['/admin/tenants'] || '';
  if (pathname.startsWith('/admin/platform-secrets'))     return titles['/admin/platform-secrets'] || '';
  if (pathname.startsWith('/admin/ai-prompts'))           return titles['/admin/ai-prompts'] || '';
  if (pathname.startsWith('/admin/audit-logs'))           return titles['/admin/audit-logs'] || '';
  if (pathname.startsWith('/admin/ai-usage'))             return titles['/admin/ai-usage'] || '';
  if (pathname.startsWith('/admin/ai-models'))            return titles['/admin/ai-models'] || '';
  if (pathname.startsWith('/admin/tenant-features'))      return titles['/admin/tenant-features'] || '';
  if (pathname.startsWith('/applications')) {
    // app_id in the query string signals we're viewing a single application's detail
    if (new URLSearchParams(search).has('app_id')) return titles['/applications/details'] || 'Application Details';
    return titles['/applications'] || '';
  }
  if (pathname.startsWith('/campaigns'))                  return titles['/campaigns'] || '';
  if (pathname.startsWith('/jobs'))                       return titles['/jobs'] || '';
  return pathname.split('/').filter(Boolean).pop()?.replace(/-/g, ' ') || '';
}

const GlobeIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-4 h-4">
    <circle cx="12" cy="12" r="10" /><path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
  </svg>
);

export const Layout: React.FC<LayoutProps> = ({ user, onLogout, children }) => {
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [isCollapsed, setIsCollapsed] = useState(() => {
    // Load collapsed state from localStorage on mount
    const saved = localStorage.getItem('sidebar-collapsed');
    return saved !== null ? JSON.parse(saved) : false;
  });
  const { lang, setLang, isAr } = useLanguage();
  const location = useLocation();
  const navigate = useNavigate();
  const t = T[lang];

  // Persist collapsed state to localStorage whenever it changes
  React.useEffect(() => {
    localStorage.setItem('sidebar-collapsed', JSON.stringify(isCollapsed));
  }, [isCollapsed]);

  const isSuperAdmin = user?.role?.toLowerCase() === 'super_admin';
  const isTenantAdmin = user?.role?.toLowerCase() === 'admin';
  const showClientOrgs = user?.tenant_type !== 'organization';
  const isAgencyOrFreelancer = user?.tenant_type === 'agency' || user?.tenant_type === 'individual_recruiter';

  const features = useFeatures();
  // super_admin always sees all nav items regardless of feature flags
  const showAIRecruitment = isSuperAdmin || !features.loaded || features.aiRecruitment;
  const showATSManagement = isSuperAdmin || !features.loaded || features.atsManagement;

  const { pageTitle: contextTitle } = usePageTitle();
  const currentMenuId = pathnameToMenuId(location.pathname);
  const pageTitle = contextTitle ?? pathnameToTitle(location.pathname, location.search, t.pageTitles);

  const handleNavigate = (id: string) => {
    navigate(menuIdToRoute(id));
    setIsSidebarOpen(false);
  };

  const isActive = (id: string) => currentMenuId === id;

  // ── Workspace menu items (tenant-type aware ordering) ──────────────────────
  const workspaceMenuItems = (() => {
    const items = [];

    // Dashboard (always first in workspace)
    items.push({
      id: 'dashboard',
      label: t.dashboard,
      icon: (
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
        </svg>
      ),
    });

    // Candidates (ATS Management)
    if (showATSManagement) {
      items.push({
        id: 'candidates',
        label: t.candidates,
        icon: (
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z" />
          </svg>
        ),
      });
    }

    // Talent Pool (ATS Management)
    if (showATSManagement) {
      items.push({
        id: 'talent-pool',
        label: t.talentPool || 'Talent Pool',
        icon: (
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M7 12a2 2 0 100-4 2 2 0 000 4zm0 0a3 3 0 103-3 3 3 0 00-3 3zm6 0h5v2a5 5 0 01-5 5h-4a5 5 0 01-5-5v-2h1M15 9a2 2 0 100-4 2 2 0 000 4z" />
          </svg>
        ),
      });
    }

    // Analytics — ATS Management (funnel, SLA monitoring)
    if (showATSManagement) {
      items.push({
        id: 'analytics',
        label: t.analytics,
        icon: (
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
          </svg>
        ),
      });
    }

    // For agency/freelancer: Clients next (if present)
    if (isAgencyOrFreelancer) {
      items.push({
        id: 'client-organizations',
        label: t.clients,
        icon: (
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
          </svg>
        ),
      });
    }

    // Campaigns — AI Recruitment
    if (showAIRecruitment) {
      items.push({
        id: 'campaigns',
        label: t.campaigns,
        icon: (
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M7 20l4-16m2 16l4-16M6 9h14M4 15h14" />
          </svg>
        ),
      });
    }

    // Jobs — AI Recruitment
    if (showAIRecruitment) {
      items.push({
        id: 'jobs',
        label: t.jobs,
        icon: (
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M21 13.255A23.931 23.931 0 0112 15c-3.183 0-6.22-.62-9-1.745M16 6V4a2 2 0 00-2-2h-4a2 2 0 00-2 2v2m4 6h.01M5 20h14a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
          </svg>
        ),
      });
    }

    return items;
  })();

  // ── Account menu items ─────────────────────────────────────────────────────
  const accountMenuItems = (() => {
    const items = [
      {
        id: 'settings',
        label: t.settings,
        icon: (
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
          </svg>
        ),
      },
    ];

    // Plan & Usage for admins only
    if (isTenantAdmin) {
      items.push({
        id: 'plan-usage',
        label: t.planUsage,
        icon: (
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 10h18M7 15h1m4 0h1m-7 4h12a3 3 0 003-3V8a3 3 0 00-3-3H6a3 3 0 00-3 3v8a3 3 0 003 3z" />
          </svg>
        ),
      });
    }

    return items;
  })();

  const adminMenuItems = [
    { id: 'admin-dashboard', label: t.superAdmin, icon: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
      </svg>
    )},
    { id: 'admin-users', label: t.usersGlobal, icon: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z" />
      </svg>
    )},
  ];

  const platformControlItems = [
    { id: 'admin-platform-config', label: t.platformConfig, icon: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4" />
      </svg>
    )},
    { id: 'admin-subscription-plans', label: t.subscriptionPlans, icon: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 10h18M7 15h1m4 0h1m-7 4h12a3 3 0 003-3V8a3 3 0 00-3-3H6a3 3 0 00-3 3v8a3 3 0 003 3z" />
      </svg>
    )},
    { id: 'admin-tenant-subscriptions', label: t.tenantSubscriptions, icon: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
      </svg>
    )},
    { id: 'admin-platform-secrets', label: t.secrets, icon: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" />
      </svg>
    )},
    { id: 'admin-ai-prompts', label: t.aiPrompts, icon: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
      </svg>
    )},
    { id: 'admin-audit-logs', label: t.auditLogs, icon: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01" />
      </svg>
    )},
    { id: 'admin-ai-usage', label: t.aiUsage, icon: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    )},
    { id: 'admin-ai-models', label: t.aiModels, icon: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 3H5a2 2 0 00-2 2v4m6-6h10a2 2 0 012 2v4M9 3v18m0 0h10a2 2 0 002-2V9M9 21H5a2 2 0 01-2-2V9m0 0h18" />
      </svg>
    )},
    { id: 'admin-tenant-features', label: t.tenantFeatures, icon: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 12l2 2 4-4M7.835 4.697a3.42 3.42 0 001.946-.806 3.42 3.42 0 014.438 0 3.42 3.42 0 001.946.806 3.42 3.42 0 013.138 3.138 3.42 3.42 0 00.806 1.946 3.42 3.42 0 010 4.438 3.42 3.42 0 00-.806 1.946 3.42 3.42 0 01-3.138 3.138 3.42 3.42 0 00-1.946.806 3.42 3.42 0 01-4.438 0 3.42 3.42 0 00-1.946-.806 3.42 3.42 0 01-3.138-3.138 3.42 3.42 0 00-.806-1.946 3.42 3.42 0 010-4.438 3.42 3.42 0 00.806-1.946 3.42 3.42 0 013.138-3.138z" />
      </svg>
    )},
  ];

  return (
    <div className="flex min-h-screen relative lg:flex-row flex-col bg-background h-screen overflow-hidden">
      {isSidebarOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-40 lg:hidden backdrop-blur-sm transition-opacity"
          onClick={() => setIsSidebarOpen(false)}
        />
      )}

      <aside className={`
        fixed lg:static inset-y-0 z-50 bg-white flex flex-col transform transition-transform duration-300 ease-in-out border-border
        ${isAr ? 'right-0 border-l' : 'left-0 border-r'}
        ${isCollapsed ? 'w-20' : 'w-64'}
        ${isSidebarOpen ? 'translate-x-0' : isAr ? 'translate-x-full lg:translate-x-0' : '-translate-x-full lg:translate-x-0'}
      `}>
        <div className="flex items-center justify-between p-4 border-b border-border">
          <div className="flex items-center gap-2 min-w-0">
            <div className="w-8 h-8 bg-primary rounded-lg flex items-center justify-center text-white font-black text-xl flex-shrink-0">C</div>
            {!isCollapsed && <h1 className="text-textMain font-bold text-lg tracking-tight whitespace-nowrap">CV Analyzer</h1>}
          </div>
          <div className="flex items-center">
            {/* Desktop collapse/expand button */}
            <button
              className="hidden lg:flex items-center justify-center p-1.5 text-textMuted hover:text-textMain transition-colors rounded-lg hover:bg-slate-50"
              onClick={() => setIsCollapsed(!isCollapsed)}
              title={isCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            >
              {isCollapsed ? (
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13 5l7 7-7 7M5 5l7 7-7 7" />
                </svg>
              ) : (
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M11 19l-7-7 7-7m8 14l-7-7 7-7" />
                </svg>
              )}
            </button>
            {/* Mobile close button */}
            <button className="lg:hidden p-2 text-textMuted hover:text-textMain transition-colors" onClick={() => setIsSidebarOpen(false)}>
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        <nav className={`flex-1 overflow-y-auto transition-all duration-300 ${isCollapsed ? 'px-2 py-4 space-y-4' : 'px-4 py-4 space-y-6'}`}>
          {isSuperAdmin && (
            <>
              <div>
                {!isCollapsed && <p className="px-4 text-[10px] font-black text-textMuted uppercase tracking-widest mb-2">{t.sysAdmin}</p>}
                <div className="space-y-1">
                  {adminMenuItems.map(item => (
                    <button
                      key={item.id}
                      onClick={() => handleNavigate(item.id)}
                      title={isCollapsed ? item.label : undefined}
                      className={`w-full flex items-center gap-3 transition-all rounded-xl text-sm font-medium ${isCollapsed ? 'p-2 justify-center' : 'px-4 py-2.5'} ${isActive(item.id) ? 'bg-primary text-white shadow-md shadow-primary/20' : 'text-textMuted hover:bg-slate-50 hover:text-textMain'}`}
                    >
                      {item.icon}{!isCollapsed && <span>{item.label}</span>}
                    </button>
                  ))}
                </div>
              </div>
              <div>
                {!isCollapsed && <p className="px-4 text-[10px] font-black text-textMuted uppercase tracking-widest mb-2">{t.platformControl}</p>}
                <div className="space-y-1">
                  {platformControlItems.map(item => (
                    <button
                      key={item.id}
                      onClick={() => handleNavigate(item.id)}
                      title={isCollapsed ? item.label : undefined}
                      className={`w-full flex items-center gap-3 transition-all rounded-xl text-sm font-medium ${isCollapsed ? 'p-2 justify-center' : 'px-4 py-2.5'} ${isActive(item.id) ? 'bg-primary text-white shadow-md shadow-primary/20' : 'text-textMuted hover:bg-slate-50 hover:text-textMain'}`}
                    >
                      {item.icon}{!isCollapsed && <span>{item.label}</span>}
                    </button>
                  ))}
                </div>
              </div>
            </>
          )}

          {/* Workspace section */}
          <div>
            {!isCollapsed && <p className="px-4 text-[10px] font-black text-textMuted uppercase tracking-widest mb-2">{t.workspaceSection}</p>}
            <div className="space-y-1">
              {workspaceMenuItems.map(item => (
                <button
                  key={item.id}
                  onClick={() => handleNavigate(item.id)}
                  title={isCollapsed ? item.label : undefined}
                  className={`w-full flex items-center gap-3 transition-all rounded-xl text-sm font-medium ${isCollapsed ? 'p-2 justify-center' : 'px-4 py-2.5'} ${isActive(item.id) ? 'bg-primary text-white shadow-md shadow-primary/20' : 'text-textMuted hover:bg-slate-50 hover:text-textMain'}`}
                >
                  {item.icon}{!isCollapsed && <span>{item.label}</span>}
                </button>
              ))}
            </div>
          </div>

          {/* Visual separator between workspace and account sections */}
          {!isCollapsed && <div className="h-px bg-border" />}

          {/* Account section */}
          <div>
            {!isCollapsed && <p className="px-4 text-[10px] font-black text-textMuted uppercase tracking-widest mb-2">{t.accountSection}</p>}
            <div className="space-y-1">
              {accountMenuItems.map(item => (
                <button
                  key={item.id}
                  onClick={() => handleNavigate(item.id)}
                  title={isCollapsed ? item.label : undefined}
                  className={`w-full flex items-center gap-3 transition-all rounded-xl text-sm font-medium ${isCollapsed ? 'p-2 justify-center' : 'px-4 py-2.5'} ${isActive(item.id) ? 'bg-primary text-white shadow-md shadow-primary/20' : 'text-textMuted hover:bg-slate-50 hover:text-textMain'}`}
                >
                  {item.icon}{!isCollapsed && <span>{item.label}</span>}
                </button>
              ))}
            </div>
          </div>
        </nav>

        <div className={`border-t border-border bg-white space-y-2 transition-all duration-300 ${isCollapsed ? 'p-2' : 'p-4'}`}>
          <button
            onClick={() => setLang(isAr ? 'en' : 'ar')}
            title={isCollapsed ? t.langBtn : undefined}
            className={`w-full flex items-center gap-2 rounded-xl text-sm font-semibold text-textMuted hover:text-primary hover:bg-slate-50 transition-all border border-border ${isCollapsed ? 'p-2 justify-center' : 'px-4 py-2'}`}
          >
            <GlobeIcon />{!isCollapsed && <span>{t.langBtn}</span>}
          </button>
          <button
            onClick={onLogout}
            title={isCollapsed ? t.logout : undefined}
            className={`w-full flex items-center gap-3 rounded-xl text-sm font-bold text-error hover:bg-red-50 transition-all ${isCollapsed ? 'p-2 justify-center' : 'px-4 py-2.5'}`}
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
            </svg>
            {!isCollapsed && <span>{t.logout}</span>}
          </button>
        </div>
      </aside>

      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <header className="h-16 bg-white border-b border-border flex items-center justify-between px-4 sm:px-8 shrink-0">
          <div className="flex items-center gap-4">
            <button className="lg:hidden p-2 text-textMuted hover:text-textMain transition-colors" onClick={() => setIsSidebarOpen(true)}>
              <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 6h16M4 12h16M4 18h16" />
              </svg>
            </button>
            <h2 className="text-lg sm:text-xl font-bold text-textMain capitalize truncate">{pageTitle}</h2>
          </div>

          <div className="flex items-center gap-3 sm:gap-4">
            {!isSuperAdmin && user?.subscription_status === 'trial' && (
              <span className="hidden sm:inline-block px-2.5 py-1 rounded-full bg-amber-100 text-amber-800 text-[9px] font-black uppercase tracking-widest shrink-0">
                Trial
              </span>
            )}
            <div className={`text-right hidden sm:block ${isAr ? 'text-left' : 'text-right'}`}>
              <p className="text-sm font-bold text-textMain max-w-[180px] truncate">
                {isSuperAdmin ? t.sysAdmin : (user?.tenant_name || t.organization)}
              </p>
              <p className="text-[10px] text-textMuted uppercase tracking-widest font-black flex items-center justify-end gap-1 max-w-[180px] truncate">
                <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${isSuperAdmin ? 'bg-indigo-500' : 'bg-primary'}`}></span>
                {user?.email}
              </p>
            </div>
            <div className={`w-9 h-9 sm:w-10 sm:h-10 rounded-xl flex items-center justify-center text-white font-black border transition-colors ${isSuperAdmin ? 'bg-indigo-600 border-indigo-700' : 'bg-primary border-primaryDark'}`}>
              {user?.email?.[0].toUpperCase()}
            </div>
          </div>
        </header>

        <main className="flex-1 overflow-y-auto p-4 sm:p-6 lg:p-8 animate-fade-in">
          {/* Render children (e.g. <Outlet />) passed from the layout route in App.tsx */}
          {children ?? <Outlet />}
        </main>
      </div>
    </div>
  );
};
