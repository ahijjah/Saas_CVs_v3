
import React, { useState, useEffect } from 'react';
import { AuthState, User, ApplicationFilter } from './types';
import { AuthPage } from './pages/Auth';
import { Layout } from './components/Layout';
import { JobsDashboard } from './pages/JobsDashboard';
import { JobDetails } from './pages/JobDetails';
import { ApplicationsList } from './pages/ApplicationsList';
import { AddJobModal } from './components/AddJobModal';
import { ToastContainer, ToastType } from './components/Toast';

const App: React.FC = () => {
  // Navigation State
  const [currentPage, setCurrentPage] = useState<string>('jobs');
  const [selectedJobCode, setSelectedJobCode] = useState<string | null>(null);
  const [appFilter, setAppFilter] = useState<ApplicationFilter>('all');
  const [isAddJobOpen, setIsAddJobOpen] = useState(false);

  // Auth State - initialized from localStorage once
  const [auth, setAuth] = useState<AuthState>(() => {
    try {
      const saved = localStorage.getItem('cv_analyzer_auth');
      return saved ? JSON.parse(saved) : { token: null, user: null };
    } catch (e) {
      return { token: null, user: null };
    }
  });

  // UI State: Toasts
  const [toasts, setToasts] = useState<{ id: number; message: string; type: ToastType }[]>([]);
  const addToast = (message: string, type: ToastType) => {
    const id = Date.now();
    setToasts((prev) => [...prev, { id, message, type }]);
  };
  const removeToast = (id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  };

  // Keep localStorage in sync with auth state
  useEffect(() => {
    if (auth.token) {
      localStorage.setItem('cv_analyzer_auth', JSON.stringify(auth));
    } else {
      localStorage.removeItem('cv_analyzer_auth');
    }
  }, [auth]);

  const handleLoginSuccess = (token: string, user: User) => {
    setAuth({ token, user });
    addToast("Welcome back!", "success");
  };

  const handleLogout = () => {
    // Clear state triggers useEffect which clears localStorage
    setAuth({ token: null, user: null });
    // Reset internal navigation state
    setCurrentPage('jobs');
    setSelectedJobCode(null);
    addToast("Logged out successfully", "info");
  };

  // Auth Guard: If no token, always show AuthPage
  if (!auth.token) {
    return (
      <div className="min-h-screen bg-slate-50">
        <AuthPage onLoginSuccess={handleLoginSuccess} addToast={addToast} />
        <ToastContainer toasts={toasts} removeToast={removeToast} />
      </div>
    );
  }

  // Render Page Content
  const renderContent = () => {
    switch (currentPage) {
      case 'jobs':
        return (
          <JobsDashboard 
            auth={auth} 
            onViewDetails={(code) => {
              setSelectedJobCode(code);
              setCurrentPage('job-details');
            }}
            onViewApplications={(code, filter) => {
              setSelectedJobCode(code);
              setAppFilter(filter as ApplicationFilter);
              setCurrentPage('applications');
            }}
            onAddJob={() => setIsAddJobOpen(true)}
            addToast={addToast}
          />
        );
      case 'job-details':
        if (!selectedJobCode) {
          setCurrentPage('jobs');
          return null;
        }
        return (
          <JobDetails 
            jobCode={selectedJobCode!} 
            auth={auth} 
            onBack={() => setCurrentPage('jobs')}
            addToast={addToast}
          />
        );
      case 'applications':
        if (!selectedJobCode) {
          setCurrentPage('jobs');
          return null;
        }
        return (
          <ApplicationsList 
            jobCode={selectedJobCode!} 
            initialFilter={appFilter}
            auth={auth} 
            onBack={() => setCurrentPage('jobs')}
            addToast={addToast}
          />
        );
      default:
        return (
          <div className="flex flex-col items-center justify-center h-full py-12">
            <h3 className="text-lg font-medium text-textMain">Page not found</h3>
            <button 
              onClick={() => setCurrentPage('jobs')}
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
        onLogout={handleLogout} 
        currentPage={currentPage} 
        onNavigate={setCurrentPage}
      >
        {renderContent()}
      </Layout>

      {isAddJobOpen && (
        <AddJobModal 
          onClose={() => setIsAddJobOpen(false)} 
          onSuccess={() => {
            setIsAddJobOpen(false);
            setCurrentPage('jobs');
          }}
          token={auth.token!}
          addToast={addToast}
        />
      )}

      <ToastContainer toasts={toasts} removeToast={removeToast} />
    </div>
  );
};

export default App;
