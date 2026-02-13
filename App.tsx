
import React, { useState, useEffect } from 'react';
import { AuthState, User, ApplicationFilter } from './types';
import { AuthPage } from './pages/Auth';
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
import { ToastContainer, ToastType } from './components/Toast';

const App: React.FC = () => {
  // Auth State - initialized from individual localStorage keys
  const [auth, setAuth] = useState<AuthState>(() => {
    try {
      const token = localStorage.getItem('token');
      const userStr = localStorage.getItem('user');
      const user = userStr ? JSON.parse(userStr) : null;
      return { token, user };
    } catch (e) {
      return { token: null, user: null };
    }
  });

  const isSuperAdmin = auth.user?.role?.toLowerCase() === 'super_admin';
  
  // Set initial page based on role
  const [currentPage, setCurrentPage] = useState<string>(() => {
    const saved = localStorage.getItem('last_page');
    if (saved) return saved;
    return auth.user?.role?.toLowerCase() === 'super_admin' ? 'admin-dashboard' : 'jobs';
  });

  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [appFilter, setAppFilter] = useState<ApplicationFilter>('all');
  const [isAddJobOpen, setIsAddJobOpen] = useState(false);

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
    setAuth({ token, user });
    addToast("Welcome back!", "success");
    // Routing Logic: Super Admin goes to admin dashboard
    if (user.role?.toLowerCase() === 'super_admin') {
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
  };

  const handleViewApplications = (id: string, filter: string) => {
    setSelectedJobId(id);
    setAppFilter(filter as ApplicationFilter);
    setCurrentPage('applications');
  };

  // Handle simple routing
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
    return (
      <div className="min-h-screen bg-slate-50">
        <AuthPage onLoginSuccess={handleLoginSuccess} addToast={addToast} />
        <ToastContainer toasts={toasts} removeToast={removeToast} />
      </div>
    );
  }

  const renderContent = () => {
    // Access Control: If not super_admin but trying to see admin pages, redirect
    if (!isSuperAdmin && (currentPage === 'admin-dashboard' || currentPage === 'admin-users')) {
      setCurrentPage('jobs');
      return null;
    }

    switch (currentPage) {
      case 'admin-dashboard':
        return <AdminDashboard auth={auth} addToast={addToast} />;
      case 'admin-users':
        return <AdminUsers auth={auth} addToast={addToast} />;
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
        return (
          <div className="flex flex-col items-center justify-center h-full py-12 text-center">
            <h3 className="text-lg font-medium text-textMain">Page not found</h3>
            <button 
              onClick={() => setCurrentPage(isSuperAdmin ? 'admin-dashboard' : 'jobs')}
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

export default App;
