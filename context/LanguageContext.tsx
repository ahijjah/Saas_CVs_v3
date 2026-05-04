
import React, { createContext, useContext, useState, useEffect } from 'react';

type Lang = 'en' | 'ar';

interface LangCtx {
  lang: Lang;
  setLang: (l: Lang) => void;
  isAr: boolean;
}

const LanguageContext = createContext<LangCtx>({ lang: 'en', setLang: () => {}, isAr: false });

export const LanguageProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [lang, setLangState] = useState<Lang>(() => (localStorage.getItem('app_lang') as Lang) || 'en');

  const setLang = (l: Lang) => {
    setLangState(l);
    localStorage.setItem('app_lang', l);
  };

  useEffect(() => {
    const isAr = lang === 'ar';
    document.documentElement.lang = isAr ? 'ar' : 'en';
    document.documentElement.dir  = isAr ? 'rtl' : 'ltr';
    document.body.style.fontFamily = isAr ? "'Cairo', sans-serif" : "'Inter', sans-serif";
  }, [lang]);

  return (
    <LanguageContext.Provider value={{ lang, setLang, isAr: lang === 'ar' }}>
      {children}
    </LanguageContext.Provider>
  );
};

export const useLanguage = () => useContext(LanguageContext);
