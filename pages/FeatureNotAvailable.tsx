
import React from 'react';
import { useNavigate } from 'react-router-dom';

interface FeatureNotAvailableProps {
  moduleName: string;
  description?: string;
  returnPath?: string;
  returnLabel?: string;
}

export const FeatureNotAvailable: React.FC<FeatureNotAvailableProps> = ({
  moduleName,
  description,
  returnPath = '/dashboard',
  returnLabel = 'Back to Dashboard',
}) => {
  const navigate = useNavigate();

  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] text-center px-6">
      <div className="w-16 h-16 bg-slate-100 rounded-2xl flex items-center justify-center mb-6">
        <svg className="w-8 h-8 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5"
            d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
        </svg>
      </div>

      <h2 className="text-xl font-black text-textMain mb-2">{moduleName} Not Available</h2>
      <p className="text-sm text-textMuted max-w-md mb-1">
        {description || `${moduleName} is not enabled for your organisation.`}
      </p>
      <p className="text-xs text-textMuted mb-8">
        Contact your platform administrator to enable this module.
      </p>

      <button
        onClick={() => navigate(returnPath)}
        className="text-sm font-bold px-6 py-2.5 bg-primary text-white rounded-xl hover:bg-primary/90 transition-colors"
      >
        {returnLabel}
      </button>
    </div>
  );
};
