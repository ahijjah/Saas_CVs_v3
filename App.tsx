
import React, { useState, useEffect } from 'react';
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
import { PlatformSecretsPage } from './pages/PlatformSecrets';
import { AIPromptsPage } from './pages/AIPrompts';
import AuditLogs from './pages/AuditLogs';
import { ToastContainer, ToastType } from './components/Toast';

/**
 * Helper to decode JWT payload without external libraries.
 */
function decodeJwtPayload(token: string) {
  try {
    const payload = token.split('.')[1];
    const json = atob(payload.replace(/-/g, '+').replace(/_/g, '/'));
    return JSON.parse(json);
  } catch {
    return null;
  }
}

const AppInner: React.FC = () => {
  // Auth State - initialized from individual localStorage keys and JWT decoding
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

          // Try to get cached user metadata for tenant names etc, but trust token for role
          const userStr = localStorage.getItem('user');
          const cachedUser = userStr ? JSON.parse(userStr) : {};

          return {
            token,
            user: { 
              ...cachedUser,
              role, 
              tenant_id, 
              email, 
              sub 
            }
          };
        }
      }
      return { token: null, user: null };
    } catch (e) {
      return { token: null, user: null };
    }
  });

  const isSuperAdmin = auth.user?.role?.toLowerCase() === 'super_admin';
  
  // Set initial page based on role and persistence
  const [currentPage, setCurrentPage] = useState<string>(() => {
    const saved = localStorage.getItem('last_page');
    if (saved) return saved;
    return isSuperAdmin ? 'admin-dashboard' : 'jobs';
  });

  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [appFilter, setAppFilter] = useState<ApplicationFilter>('all');
  const [isAddJobOpen, setIsAddJobOpen] = useState(false);
  const [showAuth, setShowAuth] = useState(false);

  const [toasts, setToasts] = useState<{ id: number; message: string; type: ToastType }[]>([]);
  
  const addToast = (message: string, type: ToastType) => {
    const id = Date.now();
    setToasts((prev) => [...prev, { id, message, type }]);
  };
  
  const removeToast = (id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  };

  // Sync state to storage
  useEffect(() => {
    if (auth.token && auth.user) {
      localStorage.setItem('token', auth.token);
      localStorage.setItem('user', JSON.stringify(auth.user));
    } else {
      localStorage.removeItem('token');
      localStorage.removeItem('user');
    }
  }, [auth]);

  // Remember page
  useEffect(() => {
    localStorage.setItem('last_page', currentPage);
  }, [currentPage]);

  const handleLoginSuccess = (token: string, user: User) => {
    // Decode token to ensure we have the most authoritative role
    const payload = decodeJwtPayload(token);
    const role = String(payload?.role ?? user.role ?? '').toLowerCase();
    
    const updatedUser = {
      ...user,
      role,
      tenant_id: payload?.tenant_id || user.tenant_id,
      email: payload?.email || user.email
    };

    setAuth({ token, user: updatedUser });
    addToast("Welcome back!", "success");

    // Routing Logic based on normalized role
    if (role === 'super_admin') {
      setCurrentPage('admin-dashboard');
    } else {
      setCurrentPage('jobs');
    }
  };

  const handleLogout = () => {
    localStorage.removeItem('token');
    localStorage.removeItem('user');
    localStorage.removeItem('last_page');
    localStorage.removeItem('cv_analyzer_auth');
    setAuth({ token: null, user: null });
    setCurrentPage('jobs');
    setSelectedJobId(null);
    setShowAuth(false);
  };

  const handleViewApplications = (id: string, filter: string) => {
    setSelectedJobId(id);
    setAppFilter(filter as ApplicationFilter);
    setCurrentPage('applications');
  };

  // Handle simple routing for external links
  const path = window.location.pathname;
  if (path === '/reset-password') {
    return (
      <div className="min-h-screen bg-slate-50">
        <ResetPassword 
          addToast={addToast} 
          onSuccess={handleLogout} 
        />
        <ToastContainer toasts={toasts} removeToast={removeToast} />
      </div>
    );
  }

  if (path === '/forgot-password') {
    return (
      <div className="min-h-screen bg-slate-50">
        <ForgotPassword addToast={addToast} />
        <ToastContainer toasts={toasts} removeToast={removeToast} />
      </div>
    );
  }

  if (!auth.token) {
    if (!showAuth) {
      return (
        <LandingPage
          onGetStarted={() => setShowAuth(true)}
          onSignIn={() => setShowAuth(true)}
        />
      );
    }
    return (
      <div className="min-h-screen bg-slate-50">
        <AuthPage onLoginSuccess={handleLoginSuccess} addToast={addToast} />
        <ToastContainer toasts={toasts} removeToast={removeToast} />
      </div>
    );
  }

  const renderContent = () => {
    const role = (auth.user?.role || '').toLowerCase();

    // Access Control: Protect super_admin pages
    const superAdminPages = [
      'admin-dashboard', 'admin-users',
      'admin-platform-config', 'admin-subscription-plans', 'admin-tenant-subscriptions',
      'admin-platform-secrets', 'admin-ai-prompts', 'admin-audit-logs',
    ];
    if (role !== 'super_admin' && superAdminPages.includes(currentPage)) {
      setCurrentPage('jobs');
      return null;
    }

    switch (currentPage) {
      case 'admin-dashboard':
        return <AdminDashboard auth={auth} addToast={addToast} />;
      case 'admin-users':
        return <AdminUsers auth={auth} addToast={addToast} />;
      case 'admin-platform-config':
        return <PlatformConfigPage auth={auth} addToast={addToast} />;
      case 'admin-subscription-plans':
        return <SubscriptionPlansPage auth={auth} addToast={addToast} />;
      case 'admin-tenant-subscriptions':
        return <TenantSubscriptionsPage auth={auth} addToast={addToast} />;
      case 'admin-platform-secrets':
        return <PlatformSecretsPage auth={auth} addToast={addToast} />;
      case 'admin-ai-prompts':
        return <AIPromptsPage auth={auth} addToast={addToast} />;
      case 'admin-audit-logs':
        return <AuditLogs />;
      case 'jobs':
        return (
          <JobsDashboard 
            auth={auth} 
            onViewDetails={(id) => {
              setSelectedJobId(id);
              setCurrentPage('job-details');
            }}
            onViewApplications={handleViewApplications}
            onAddJob={() => setIsAddJobOpen(true)}
            addToast={addToast}
          />
        );
      case 'job-details':
        if (!selectedJobId) {
          setCurrentPage('jobs');
          return null;
        }
        return (
          <JobDetails 
            jobId={selectedJobId!} 
            auth={auth} 
            onBack={() => setCurrentPage('jobs')}
            onViewApplications={handleViewApplications}
            addToast={addToast}
          />
        );
      case 'applications':
        if (!selectedJobId) {
          setCurrentPage('jobs');
          return null;
        }
        return (
          <ApplicationsList 
            jobId={selectedJobId!} 
            initialFilter={appFilter}
            auth={auth} 
            onBack={() => setCurrentPage('jobs')}
            addToast={addToast}
          />
        );
      case 'settings':
        return (
          <Settings 
            auth={auth}
            addToast={addToast}
          />
        );
      default:
        // Default redirect based on role
        return (
          <div className="flex flex-col items-center justify-center h-full py-12 text-center">
            <h3 className="text-lg font-medium text-textMain">Page not found</h3>
            <button 
              onClick={() => setCurrentPage(role === 'super_admin' ? 'admin-dashboard' : 'jobs')}
              className="mt-4 text-primary hover:underline"
            >
              Return to dashboard
            </button>
          </div>
        );
    }
  };

  return (
    <div className="min-h-screen bg-background">
      <Layout 
        user={auth.user} 
        onLogout={() => {
          handleLogout();
          addToast("Logged out successfully", "info");
        }} 
        currentPage={currentPage} 
        onNavigate={setCurrentPage}
      >
        {renderContent()}
      </Layout>

      {isAddJobOpen && (
        <AddJobModal 
          onClose={() => setIsAddJobOpen(false)} 
          onSuccess={(jobId) => {
            setIsAddJobOpen(false);
            setSelectedJobId(jobId);
            setCurrentPage('job-details');
          }}
          user={auth.user}
          token={auth.token!}
          addToast={addToast}
        />
      )}

      <ToastContainer toasts={toasts} removeToast={removeToast} />
    </div>
  );
};

const App: React.FC = () => (
  <LanguageProvider>
    <AppInner />
  </LanguageProvider>
);

export default App;
