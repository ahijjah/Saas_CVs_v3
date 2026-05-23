
import React, { createContext, useContext, useState, useCallback } from 'react';

interface PageTitleCtx {
  /** Null means "derive from URL"; a string overrides the Layout header title. */
  pageTitle: string | null;
  setPageTitle: (title: string | null) => void;
}

const PageTitleContext = createContext<PageTitleCtx>({
  pageTitle: null,
  setPageTitle: () => {},
});

export const PageTitleProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [pageTitle, setPageTitleState] = useState<string | null>(null);
  const setPageTitle = useCallback((title: string | null) => setPageTitleState(title), []);
  return (
    <PageTitleContext.Provider value={{ pageTitle, setPageTitle }}>
      {children}
    </PageTitleContext.Provider>
  );
};

export const usePageTitle = () => useContext(PageTitleContext);
