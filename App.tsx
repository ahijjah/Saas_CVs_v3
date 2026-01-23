
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

  // Auth State
  const [auth, setAuth] = useState<AuthState>(() => {
    const saved = localStorage.getItem('cv_analyzer_auth');
    return saved ? JSON.parse(saved) : { token: null, user: null };
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

  useEffect(() => {
    localStorage.setItem('cv_analyzer_auth', JSON.stringify(auth));
  }, [auth]);

  const handleLoginSuccess = (token: string, user: User) => {
    setAuth({ token, user });
    addToast("Welcome back!", "success");
  };

  const handleLogout = () => {
    setAuth({ token: null, user: null });
    localStorage.removeItem('cv_analyzer_auth');
    setCurrentPage('jobs');
    addToast("Logged out successfully", "info");
  };

  // Auth Guard
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
        return (
          <JobDetails 
            jobCode={selectedJobCode!} 
            auth={auth} 
            onBack={() => setCurrentPage('jobs')}
            addToast={addToast}
          />
        );
      case 'applications':
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
        return <div>Page not found</div>;
    }
  };

  return (
    <div className="min-h-screen">
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
