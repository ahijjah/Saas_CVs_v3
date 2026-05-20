
import React, { useState } from 'react';
import {
  BrowserRouter,
  Routes,
  Route,
  Navigate,
  useNavigate,
  useParams,
  useSearchParams,
  Outlet,
} from 'react-router-dom';
import { AuthState, User, ApplicationFilter } from './types';
import { LanguageProvider } from './context/LanguageContext';
import { AuthPage } from './pages/Auth';
import { LandingPage } from './pages/LandingPage';
import { Layout } from './components/Layout';
import { JobsDashboard } from './pages/JobsDashboard';
import { JobDetails } from './pages/JobDetails';
import { ApplicationsList } from './pages/ApplicationsList';
import { AddJobModal } from './components/AddJobModal';
import { Settings } from './pages/Settings';
import { ResetPassword } from './pages/ResetPassword';
import { ForgotPassword } from './pages/ForgotPassword';
import { AdminDashboard } from './pages/AdminDashboard';
import { AdminUsers } from './pages/AdminUsers';
import { PlatformConfigPage } from './pages/PlatformConfig';
import { SubscriptionPlansPage } from './pages/SubscriptionPlans';
import { TenantSubscriptionsPage } from './pages/TenantSubscriptions';
import { PlanUsagePage } from './pages/PlanUsage';
import { PlatformSecretsPage } from './pages/PlatformSecrets';
import { AIPromptsPage } from './pages/AIPrompts';
import AuditLogs from './pages/AuditLogs';
import { PublicJobApply } from './pages/PublicJobApply';
import { ClientOrganizationsPage } from './pages/ClientOrganizations';
import { PlanSelectionPage } from './pages/PlanSelection';
import { ToastContainer, ToastType } from './components/Toast';

// ── JWT helper ─────────────────────────────────────────────────────────────
function decodeJwtPayload(token: string) {
  try {
    const payload = token.split('.')[1];
    const json = atob(payload.replace(/-/g, '+').replace(/_/g, '/'));
    return JSON.parse(json);
  } catch {
    return null;
  }
}

// ── Route wrapper: /jobs/:jobId ────────────────────────────────────────────
const JobDetailsRoute: React.FC<{
  auth: AuthState;
  onViewApplications: (jobId: string, filter: string) => void;
  onOpenApplication: (jobId: string, applicationId: string) => void;
  addToast: (msg: string, type: ToastType) => void;
}> = ({ auth, onViewApplications, onOpenApplication, addToast }) => {
  const { jobId } = useParams<{ jobId: string }>();
  const navigate = useNavigate();
  if (!jobId) return <Navigate to="/jobs" replace />;
  return (
    <JobDetails
      jobId={jobId}
      auth={auth}
      onBack={() => navigate('/jobs')}
      onViewApplications={onViewApplications}
      onOpenApplication={onOpenApplication}
      addToast={addToast}
    />
  );
};

// ── Route wrapper: /applications?job_id=...&filter=...&app_id=... ──────────
const ApplicationsRoute: React.FC<{
  auth: AuthState;
  addToast: (msg: string, type: ToastType) => void;
}> = ({ auth, addToast }) => {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const jobId = searchParams.get('job_id');
  const filter = (searchParams.get('filter') || 'all') as ApplicationFilter;
  const appId = searchParams.get('app_id') || null;
  if (!jobId) return <Navigate to="/jobs" replace />;
  return (
    <ApplicationsList
      jobId={jobId}
      initialFilter={filter}
      initialApplicationId={appId}
      auth={auth}
      onBack={() => navigate('/jobs')}
      addToast={addToast}
    />
  );
};

// ── Layout route: persists Layout across navigations via <Outlet /> ────────
const AuthedLayout: React.FC<{
  auth: AuthState;
  onLogout: () => void;
}> = ({ auth, onLogout }) => {
  if (!auth.token) return <Navigate to="/login" replace />;
  return (
    <Layout user={auth.user} onLogout={onLogout}>
      <Outlet />
    </Layout>
  );
};

// ── Super-admin guard: renders <Outlet /> or redirects ────────────────────
const SuperAdminGuard: React.FC<{ isSuperAdmin: boolean }> = ({ isSuperAdmin }) =>
  isSuperAdmin ? <Outlet /> : <Navigate to="/jobs" replace />;

// ── Tenant access guard: pending_plan_selection tenants may only visit /plan-selection ──
const TenantAccessGuard: React.FC<{ subscriptionStatus: string | undefined }> = ({ subscriptionStatus }) =>
  subscriptionStatus === 'pending_plan_selection'
    ? <Navigate to="/plan-selection" replace />
    : <Outlet />;

// ── Not-found page inside layout ──────────────────────────────────────────
const NotFoundPage: React.FC<{ defaultHome: string }> = ({ defaultHome }) => {
  const navigate = useNavigate();
  return (
    <div className="flex flex-col items-center justify-center h-full py-12 text-center">
      <h3 className="text-lg font-medium text-textMain">Page not found</h3>
      <button onClick={() => navigate(defaultHome)} className="mt-4 text-primary hover:underline">
        Return to dashboard
      </button>
    </div>
  );
};

// ── Main app inner (must be inside BrowserRouter) ─────────────────────────
const AppInner: React.FC = () => {
  const navigate = useNavigate();

  const [auth, setAuth] = useState<AuthState>(() => {
    try {
      const token = localStorage.getItem('token');
      if (token) {
        const payload = decodeJwtPayload(token);
        if (payload) {
          const role = String(payload.role ?? '').toLowerCase();
          const tenant_id = String(payload.tenant_id ?? '');
          const email = String(payload.email ?? '');
          const sub = String(payload.sub ?? '');
          const userStr = localStorage.getItem('user');
          const cachedUser = userStr ? JSON.parse(userStr) : {};
          return { token, user: { ...cachedUser, role, tenant_id, email, sub } };
        }
      }
      return { token: null, user: null };
    } catch {
      return { token: null, user: null };
    }
  });

  const [isAddJobOpen, setIsAddJobOpen] = useState(false);
  const [toasts, setToasts] = useState<{ id: number; message: string; type: ToastType }[]>([]);

  const addToast = (message: string, type: ToastType) => {
    const id = Date.now();
    setToasts(prev => [...prev, { id, message, type }]);
  };

  const removeToast = (id: number) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  };

  React.useEffect(() => {
    if (auth.token && auth.user) {
      localStorage.setItem('token', auth.token);
      localStorage.setItem('user', JSON.stringify(auth.user));
    } else {
      localStorage.removeItem('token');
      localStorage.removeItem('user');
    }
  }, [auth]);

  const role = auth.user?.role?.toLowerCase() || '';
  const isSuperAdmin = role === 'super_admin';
  const defaultHome = isSuperAdmin ? '/admin/dashboard' : '/jobs';

  const getPostLoginRoute = (userRole: string, subscriptionStatus?: string): string => {
    if (userRole === 'super_admin') return '/admin/dashboard';
    switch (subscriptionStatus) {
      case 'pending_plan_selection': return '/plan-selection';
      case 'trial':
      case 'active':
      case 'cancelled_pending_expiry': return '/jobs';
      default: return '/plan-usage';
    }
  };

  const handleLoginSuccess = (token: string, user: User) => {
    const payload = decodeJwtPayload(token);
    const userRole = String(payload?.role ?? user.role ?? '').toLowerCase();
    const updatedUser = {
      ...user,
      role: userRole,
      tenant_id: payload?.tenant_id || user.tenant_id,
      email: payload?.email || user.email,
    };
    setAuth({ token, user: updatedUser });
    addToast('Welcome back!', 'success');
    navigate(getPostLoginRoute(userRole, user.subscription_status), { replace: true });
  };

  const handleUserUpdate = (patch: Partial<User>) => {
    setAuth(prev => ({
      ...prev,
      user: prev.user ? { ...prev.user, ...patch } : prev.user,
    }));
  };

  const handleLogout = () => {
    localStorage.removeItem('token');
    localStorage.removeItem('user');
    localStorage.removeItem('last_page');
    localStorage.removeItem('cv_analyzer_auth');
    setAuth({ token: null, user: null });
    addToast('Logged out successfully', 'info');
    navigate('/', { replace: true });
  };

  const handleViewApplications = (jobId: string, filter: string) => {
    navigate(`/applications?job_id=${encodeURIComponent(jobId)}&filter=${encodeURIComponent(filter)}`);
  };

  const handleOpenApplication = (jobId: string, applicationId: string) => {
    navigate(`/applications?job_id=${encodeURIComponent(jobId)}&app_id=${encodeURIComponent(applicationId)}`);
  };

  // Shared props passed to all protected pages
  const sharedAuth = { auth, addToast };

  return (
    <>
      <Routes>
        {/* ── Fully public: no auth required, no Layout ─────────────────── */}
        <Route
          path="/reset-password"
          element={
            <div className="min-h-screen bg-slate-50">
              <ResetPassword addToast={addToast} onSuccess={handleLogout} />
            </div>
          }
        />
        <Route
          path="/forgot-password"
          element={
            <div className="min-h-screen bg-slate-50">
              <ForgotPassword addToast={addToast} />
            </div>
          }
        />
        {/* Public job apply — accessible without login for both guests and logged-in users */}
        <Route
          path="/apply/:jobCode"
          element={<PublicJobApply addToast={addToast} />}
        />

        {/* ── Landing / login ───────────────────────────────────────────── */}
        <Route
          path="/login"
          element={
            auth.token ? (
              <Navigate to={defaultHome} replace />
            ) : (
              <div className="min-h-screen bg-slate-50">
                <AuthPage onLoginSuccess={handleLoginSuccess} addToast={addToast} />
              </div>
            )
          }
        />
        <Route
          path="/"
          element={
            auth.token ? (
              <Navigate to={defaultHome} replace />
            ) : (
              <LandingPage
                onGetStarted={() => navigate('/login?mode=register')}
                onSignIn={() => navigate('/login')}
              />
            )
          }
        />

        {/* ── Authenticated routes — all wrapped by Layout via Outlet ───── */}
        <Route element={<AuthedLayout auth={auth} onLogout={handleLogout} />}>
          {/* /dashboard — smart redirect */}
          <Route
            path="/dashboard"
            element={<Navigate to={defaultHome} replace />}
          />

          {/* Plan selection — accessible before a plan is chosen (no Layout wrapping needed) */}
          <Route
            path="/plan-selection"
            element={
              <PlanSelectionPage
                auth={auth}
                addToast={addToast}
                onUserUpdate={handleUserUpdate}
                onNavigate={navigate}
              />
            }
          />

          {/* Tenant routes — blocked for pending_plan_selection tenants */}
          <Route element={<TenantAccessGuard subscriptionStatus={auth.user?.subscription_status} />}>
          <Route
            path="/jobs"
            element={
              <JobsDashboard
                auth={auth}
                onViewDetails={id => navigate(`/jobs/${id}`)}
                onViewApplications={handleViewApplications}
                onAddJob={() => setIsAddJobOpen(true)}
                addToast={addToast}
              />
            }
          />
          <Route
            path="/jobs/:jobId"
            element={
              <JobDetailsRoute
                auth={auth}
                onViewApplications={handleViewApplications}
                onOpenApplication={handleOpenApplication}
                addToast={addToast}
              />
            }
          />
          <Route
            path="/applications"
            element={<ApplicationsRoute auth={auth} addToast={addToast} />}
          />
          <Route path="/settings" element={<Settings {...sharedAuth} />} />
          <Route path="/client-organizations" element={<ClientOrganizationsPage {...sharedAuth} />} />
          <Route
            path="/plan-usage"
            element={
              role === 'admin' ? (
                <PlanUsagePage {...sharedAuth} />
              ) : (
                <Navigate to={isSuperAdmin ? '/admin/dashboard' : '/jobs'} replace />
              )
            }
          />
          </Route>{/* end TenantAccessGuard */}

          {/* Super-admin only routes */}
          <Route element={<SuperAdminGuard isSuperAdmin={isSuperAdmin} />}>
            <Route path="/admin/dashboard" element={<AdminDashboard {...sharedAuth} />} />
            <Route path="/admin/users" element={<AdminUsers {...sharedAuth} />} />
            <Route path="/admin/subscription-plans" element={<SubscriptionPlansPage {...sharedAuth} />} />
            <Route path="/admin/tenants" element={<TenantSubscriptionsPage {...sharedAuth} />} />
            <Route path="/admin/platform-config" element={<PlatformConfigPage {...sharedAuth} />} />
            <Route path="/admin/platform-secrets" element={<PlatformSecretsPage {...sharedAuth} />} />
            <Route path="/admin/ai-prompts" element={<AIPromptsPage {...sharedAuth} />} />
            <Route path="/admin/audit-logs" element={<AuditLogs />} />
          </Route>

          {/* 404 inside layout */}
          <Route path="*" element={<NotFoundPage defaultHome={defaultHome} />} />
        </Route>

        {/* Catch-all for unauthenticated unknown paths */}
        <Route path="*" element={<Navigate to={auth.token ? defaultHome : '/login'} replace />} />
      </Routes>

      {isAddJobOpen && (
        <AddJobModal
          onClose={() => setIsAddJobOpen(false)}
          onSuccess={jobId => {
            setIsAddJobOpen(false);
            navigate(`/jobs/${jobId}`);
          }}
          user={auth.user}
          token={auth.token!}
          addToast={addToast}
        />
      )}

      <ToastContainer toasts={toasts} removeToast={removeToast} />
    </>
  );
};

// ── Root: BrowserRouter + language provider ────────────────────────────────
const App: React.FC = () => (
  <LanguageProvider>
    <BrowserRouter>
      <AppInner />
    </BrowserRouter>
  </LanguageProvider>
);

export default App;
