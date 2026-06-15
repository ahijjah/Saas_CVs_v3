
import React, { createContext, useContext, useEffect, useState } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';

interface FeatureState {
  aiRecruitment: boolean;
  atsManagement: boolean;
  loaded: boolean;
}

const DEFAULT: FeatureState = {
  aiRecruitment: true,
  atsManagement: true,
  loaded: false,
};

const FeatureContext = createContext<FeatureState>(DEFAULT);

interface FeatureProviderProps {
  token: string | null;
  children: React.ReactNode;
}

export const FeatureProvider: React.FC<FeatureProviderProps> = ({ token, children }) => {
  const [state, setState] = useState<FeatureState>(DEFAULT);

  useEffect(() => {
    if (!token) {
      setState(DEFAULT);
      return;
    }

    apiService
      .get(WEBHOOK_CONFIG.TENANT_MODULES_URL, {}, token)
      .then((data: { ai_recruitment: boolean; ats_management: boolean }) => {
        setState({
          aiRecruitment:  data.ai_recruitment  ?? true,
          atsManagement:  data.ats_management  ?? true,
          loaded: true,
        });
      })
      .catch(() => {
        // Fail open — all features enabled on error (backward compatibility)
        setState({ aiRecruitment: true, atsManagement: true, loaded: true });
      });
  }, [token]);

  return (
    <FeatureContext.Provider value={state}>
      {children}
    </FeatureContext.Provider>
  );
};

export const useFeatures = (): FeatureState => useContext(FeatureContext);
