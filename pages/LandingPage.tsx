
import React, { useState, useEffect, useRef } from 'react';

interface LandingPageProps {
  onGetStarted: () => void;
  onSignIn: () => void;
}

function useCounter(target: number, duration = 1800, start = false) {
  const [count, setCount] = useState(0);
  useEffect(() => {
    if (!start) return;
    let startTime: number | null = null;
    const step = (ts: number) => {
      if (!startTime) startTime = ts;
      const p = Math.min((ts - startTime) / duration, 1);
      setCount(Math.floor((1 - Math.pow(1 - p, 3)) * target));
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }, [target, duration, start]);
  return count;
}

function useInView(threshold = 0.2) {
  const ref = useRef<HTMLDivElement>(null);
  const [inView, setInView] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const obs = new IntersectionObserver(([e]) => { if (e.isIntersecting) setInView(true); }, { threshold });
    obs.observe(el);
    return () => obs.disconnect();
  }, [threshold]);
  return { ref, inView };
}

// ── Icons ─────────────────────────────────────────────────────────────────────

const BrainIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="w-7 h-7">
    <path d="M9.5 2a2.5 2.5 0 0 1 5 0v0a2.5 2.5 0 0 1 2.45 2H17a3 3 0 0 1 3 3v1a3 3 0 0 1-3 3h-.1a2.5 2.5 0 0 1-2.4 2H9.5a2.5 2.5 0 0 1-2.4-2H7a3 3 0 0 1-3-3V7a3 3 0 0 1 3-3h.05A2.5 2.5 0 0 1 9.5 2z"/>
    <path d="M12 6v6M9 9h6"/><path d="M7 16a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2v-1H7v1z"/><path d="M9 18v2M15 18v2"/>
  </svg>
);
const ChartIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="w-7 h-7">
    <path d="M3 3v18h18"/><path d="M18 9l-5 5-3-3-5 5"/>
  </svg>
);
const MailIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="w-7 h-7">
    <rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>
  </svg>
);
const ShieldIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="w-7 h-7">
    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/>
  </svg>
);
const UsersIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="w-7 h-7">
    <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/>
    <path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>
  </svg>
);
const ZapIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="w-7 h-7">
    <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
  </svg>
);
const CheckIcon = () => (
  <svg viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
    <path fillRule="evenodd" d="M16.707 5.293a1 1 0 0 1 0 1.414l-8 8a1 1 0 0 1-1.414 0l-4-4a1 1 0 0 1 1.414-1.414L8 12.586l7.293-7.293a1 1 0 0 1 1.414 0z" clipRule="evenodd"/>
  </svg>
);
const ArrowRightIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-5 h-5">
    <path d="M5 12h14M12 5l7 7-7 7"/>
  </svg>
);
const StarIcon = () => (
  <svg viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4 text-amber-400">
    <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/>
  </svg>
);
const GlobeIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-4 h-4">
    <circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
  </svg>
);
const MenuIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-6 h-6">
    <line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/>
  </svg>
);
const XIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-6 h-6">
    <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
  </svg>
);

// ── Shared visual config ───────────────────────────────────────────────────────

const featureIcons = [<BrainIcon />, <ChartIcon />, <MailIcon />, <ZapIcon />, <UsersIcon />, <ShieldIcon />];
const featureColors = [
  { bg: 'bg-violet-50', border: 'border-violet-100', cs: '#7c3aed', ce: '#6d28d9', impact: 'bg-violet-100 text-violet-700' },
  { bg: 'bg-blue-50',   border: 'border-blue-100',   cs: '#2563eb', ce: '#0891b2', impact: 'bg-blue-100 text-blue-700'   },
  { bg: 'bg-emerald-50',border: 'border-emerald-100',cs: '#059669', ce: '#0d9488', impact: 'bg-emerald-100 text-emerald-700' },
  { bg: 'bg-orange-50', border: 'border-orange-100', cs: '#ea580c', ce: '#d97706', impact: 'bg-orange-100 text-orange-700' },
  { bg: 'bg-rose-50',   border: 'border-rose-100',   cs: '#e11d48', ce: '#db2777', impact: 'bg-rose-100 text-rose-700'   },
  { bg: 'bg-indigo-50', border: 'border-indigo-100', cs: '#4f46e5', ce: '#2563eb', impact: 'bg-indigo-100 text-indigo-700' },
];

const stepColors = [
  { bg: '#7c3aed', tag: 'bg-violet-100 text-violet-700' },
  { bg: '#2563eb', tag: 'bg-blue-100 text-blue-700'     },
  { bg: '#4f46e5', tag: 'bg-indigo-100 text-indigo-700' },
  { bg: '#059669', tag: 'bg-emerald-100 text-emerald-700' },
];

// ── Translations ───────────────────────────────────────────────────────────────

const T = {
  en: {
    dir: 'ltr' as const,
    font: "'Inter', sans-serif",
    langBtn: 'عربي',

    navLinks: ['Features', 'How It Works', 'Pricing', 'FAQ'],
    navIds:   ['features', 'how-it-works', 'pricing', 'faq'],
    signIn:       'Sign In',
    getStarted:   'Get Started Free',
    startFreeTrial: 'Start Free Trial',

    badge:    'AI-Powered Hiring Platform',
    heroH1:   'Hire Smarter.',
    heroH2:   '10× Faster.',
    heroSub:  'Stop reading CVs manually. Our AI analyzes, scores, and ranks every applicant in seconds — so your team focuses on interviewing the best, not sorting the pile.',
    heroMetrics: [
      { icon: '⚡', text: 'Screening: days → hours' },
      { icon: '🌍', text: 'AI analysis in 40+ languages' },
      { icon: '🏆', text: 'Ranked shortlist in minutes' },
    ],
    cta1:     'Start Free — No Card Needed',
    cta2:     'See How It Works',

    mockTitle:  'app.cvanalyzer.ai — Jobs Dashboard',
    mockLive:   'Live',
    mockRecent: 'Recent Applications',
    mockStats: [
      { label: 'Total Applications',   val: '1,247', pct: 78, color: '#6366f1' },
      { label: 'Qualified Candidates', val: '312',   pct: 45, color: '#22c55e' },
      { label: 'Avg. Score',           val: '74%',   pct: 74, color: '#3b82f6' },
    ],
    mockApps: [
      { name: 'Ahmed Al-Rashidi', role: 'Senior React Developer', score: 91, status: 'qualified' },
      { name: 'Maria Santos',     role: 'Full Stack Engineer',    score: 78, status: 'qualified' },
      { name: "Liam O'Brien",     role: 'Frontend Developer',     score: 52, status: 'partial'   },
    ],

    statsLabels: ['CVs Analyzed', 'Faster Shortlisting', 'Companies Trust Us', 'Scoring Accuracy'],

    featuresTag:   'Platform Features',
    featuresTitle: 'Everything your hiring team needs',
    featuresSub:   'From the moment a CV lands in an inbox to the moment you schedule an interview — fully automated and AI-driven.',
    features: [
      { title: 'AI-Powered CV Analysis',    tag: 'Core Engine',     impact: 'Cut review time by 90%',      desc: 'Our Google Gemini-powered engine reads every CV the way a senior recruiter would — understanding context, not just keywords. It extracts skills, measures experience depth, validates education, and surfaces certifications automatically.', bullets: ['Natural language understanding', 'Multi-format CV support (PDF, DOC, email)', 'Structured data extraction in seconds'] },
      { title: 'Smart Candidate Scoring',   tag: 'Intelligence',    impact: 'Rank 100 CVs in under 2 min', desc: 'Every applicant receives a weighted score across 7 dimensions: Skills, Experience, Education, Certifications, Soft Skills, Domain Knowledge, and Custom Requirements. All weights are tuned to your job\'s unique needs.', bullets: ['7-dimension scoring framework', 'Configurable weights per job', 'Qualified / Partial / Rejected auto-decisions'] },
      { title: 'Frictionless CV Ingestion', tag: 'Automation',      impact: 'Zero setup required',         desc: 'No portals to log into, no uploads to manage. Candidates email their CVs and the platform handles the rest — choose a dedicated inbox or forward from your existing HR email.', bullets: ['Dedicated inbox per job posting', 'Email forwarding from any address', 'Zero manual upload required'] },
      { title: 'Campaign Management',       tag: 'Workflow',        impact: 'All roles, one dashboard',    desc: 'Manage every open role as a structured campaign. Define the job, set closing dates, track live application counts, and flip a posting from Active to Closed in one click.', bullets: ['Centralized jobs dashboard', 'Real-time application counters', 'Draft → Active → Closed lifecycle'] },
      { title: 'Team & Tenant Control',     tag: 'Multi-Tenant',    impact: 'Built for agencies',          desc: 'Built for agencies and enterprises that run multiple hiring units. Each tenant gets an isolated workspace, their own jobs and candidates, and a dedicated admin — all under one roof.', bullets: ['Isolated tenant workspaces', 'Role-based access (Admin / Member)', 'Suspend or activate tenants instantly'] },
      { title: 'Interview Intelligence',    tag: 'Decision Support', impact: 'Ready-made interview guide', desc: 'Go beyond scores. Each candidate analysis includes a structured interview guide: gap analysis, suggested focus areas, and tailored interview questions generated from the CV and job description.', bullets: ['Auto-generated interview questions', 'Identified skill gaps per candidate', 'Evaluation notes for hiring managers'] },
    ],

    stepsTag:   'How It Works',
    stepsTitle: 'From job post to hire in 4 steps',
    stepsSub:   "No complex integrations, no training your team on new tools. You're analyzing CVs the same day you sign up.",
    steps: [
      { icon: '📋', title: 'Post Your Job',           desc: 'Paste your job description and AI automatically extracts scoring criteria — skills, experience, education, and custom requirements. Setup takes minutes.', tags: ['AI Criteria', 'Custom Weights'] },
      { icon: '🔗', title: 'Share & Collect',         desc: 'Distribute a job email alias or public apply link. Candidates apply by emailing their CV or filling a short form — zero friction on their end.', tags: ['Email Alias', 'Apply Link'] },
      { icon: '🤖', title: 'AI Scores Everyone',      desc: 'Every CV is automatically captured, parsed, and ranked across 7 dimensions in seconds. No manual uploads, no missed applications.', tags: ['Gemini AI', '7-Dim Score', 'Auto-Rank'] },
      { icon: '🏆', title: 'Hire the Best',           desc: 'Open your dashboard to a ranked shortlist with full breakdowns, gap analysis, and AI-generated interview questions ready to use.', tags: ['Ranked Shortlist', 'Interview Guide'] },
    ],

    midCtaTag:   'Get Started Today',
    midCtaTitle: 'Start analyzing CVs in minutes',
    midCtaSub:   'No integrations to configure. No training required. Paste your job description and you\'re live.',
    midCtaBtn1:  'Start Free Trial',
    midCtaBtn2:  'See How It Works',
    midCtaFine:  '14-day free trial · No credit card · Cancel anytime',

    whoTag:   'Built For',
    whoTitle: 'Who is this for?',
    whoSub:   'From boutique agencies to enterprise HR departments — CV Analyzer fits any hiring operation.',
    personas: [
      { icon: '👥', title: 'In-House HR Teams',          desc: 'Slash screening time and let your recruiters focus on conversations, not CV piles.' },
      { icon: '🏢', title: 'Staffing Agencies',           desc: 'Manage unlimited client pipelines from one platform with full tenant isolation.' },
      { icon: '🏛️', title: 'Enterprise Organizations',    desc: 'RBAC roles, multi-team control, and audit-ready hiring workflows at scale.' },
      { icon: '⚖️', title: 'Government & Public Sector',  desc: 'Structured, criteria-driven evaluation for transparent and compliant hiring.' },
      { icon: '🎓', title: 'University & Graduate Programs', desc: 'Handle high application volumes for competitive graduate programs with ease.' },
      { icon: '⚡', title: 'High-Volume Recruiters',      desc: 'Process thousands of CVs per month without adding headcount or complexity.' },
    ],

    testimonialsTag:   'Social Proof',
    testimonialsTitle: 'Trusted by hiring teams',
    testimonials: [
      { quote: "We cut our shortlisting time from 3 days to 4 hours. The AI scores align almost perfectly with what our senior recruiters would decide — it's become indispensable.", name: 'Sarah Al-Mansouri', role: 'Head of Talent, TechCorp MENA',        initials: 'SA', color: 'bg-violet-500' },
      { quote: "The email ingestion is brilliant. Candidates just email their CVs like they always did — nothing changes on their end, but on ours everything is instantly organized and scored.", name: 'James Okonkwo', role: 'Recruitment Manager, BuildRight Group', initials: 'JO', color: 'bg-blue-500'   },
      { quote: "Running 12 clients through one platform, each totally isolated. The multi-tenant setup saved us from building our own infrastructure. Worth every penny.", name: 'Priya Nair',       role: 'Director, HireFlow Agency',             initials: 'PN', color: 'bg-emerald-500' },
    ],

    pricingTag:   'Pricing',
    pricingTitle: 'Simple, transparent pricing',
    pricingSub:   'Start free. No credit card required.',
    pricingEnterprise: { name: 'Enterprise', price: 'Custom', badge: '', desc: 'For agencies and multi-unit organizations.', cta: 'Contact Us', features: ['Everything in Professional', 'Unlimited CVs & campaigns', 'Multi-tenant management', 'Dedicated onboarding', 'SLA & custom integrations'] },

    faqTag:   'FAQ',
    faqTitle: 'Common questions',
    faqs: [
      { q: 'How does CV ingestion actually work?',           a: 'You get a dedicated email address per job posting (or use email forwarding from your existing HR inbox). When a candidate emails their CV, our system automatically picks it up, extracts the document, and runs it through the AI analysis pipeline — no manual steps required.' },
      { q: 'What file formats are supported?',               a: 'We support PDF, DOC, DOCX, and plain-text CVs sent as email attachments. The AI handles formatting variations gracefully, including tables, multi-column layouts, and non-standard structures.' },
      { q: 'Can I customize how candidates are scored?',     a: 'Yes. Each job automatically generates scoring weights based on its description, but you can adjust the weight distribution across all 7 dimensions (Skills, Experience, Education, Certifications, Soft Skills, Domain Knowledge, Other Requirements) before or after analysis.' },
      { q: 'Is there a free trial?',                         a: 'Yes — the Starter plan includes a 14-day free trial with no credit card required. You get access to 3 campaigns and 50 CV analyses to validate the platform fits your workflow.' },
      { q: 'How is data kept separate between tenants?',     a: 'Every tenant is fully isolated at the data level. Jobs, candidates, and analysis results are scoped to a tenant ID embedded in the authentication token. No cross-tenant data access is possible.' },
    ],

    ctaTitle:   'Ready to transform your hiring?',
    ctaSub:     'Join 320+ companies using AI to find better candidates, faster. Start your free trial today.',
    ctaBtn:     'Start Free Trial',
    ctaFine:    'No credit card required · 14-day free trial · Cancel anytime',

    footerLinks: ['Privacy Policy', 'Terms of Service', 'Contact'],
    footerCopy:  '© 2026 CV Analyzer. All rights reserved.',
  },

  ar: {
    dir: 'rtl' as const,
    font: "'Cairo', sans-serif",
    langBtn: 'EN',

    navLinks: ['الميزات', 'كيف يعمل', 'الأسعار', 'الأسئلة الشائعة'],
    navIds:   ['features', 'how-it-works', 'pricing', 'faq'],
    signIn:       'تسجيل الدخول',
    getStarted:   'ابدأ مجاناً',
    startFreeTrial: 'ابدأ التجربة المجانية',

    badge:   'منصة التوظيف بالذكاء الاصطناعي',
    heroH1:  'وظّف بذكاء.',
    heroH2:  'أسرع 10 أضعاف.',
    heroSub: 'توقف عن قراءة السير الذاتية يدوياً. يحلل ذكاؤنا الاصطناعي ويصنّف كل مرشح في ثوانٍ — حتى يتركز فريقك على مقابلة الأفضل، لا على فرز الملفات.',
    heroMetrics: [
      { icon: '⚡', text: 'الفرز: من أيام إلى ساعات' },
      { icon: '🌍', text: 'تحليل بالذكاء الاصطناعي بـ 40+ لغة' },
      { icon: '🏆', text: 'قائمة مختصرة مرتبة في دقائق' },
    ],
    cta1:    'ابدأ مجاناً — بلا بطاقة ائتمان',
    cta2:    'شاهد كيف يعمل',

    mockTitle:  'app.cvanalyzer.ai — لوحة الوظائف',
    mockLive:   'مباشر',
    mockRecent: 'أحدث الطلبات',
    mockStats: [
      { label: 'إجمالي الطلبات',      val: '1,247', pct: 78, color: '#6366f1' },
      { label: 'المرشحون المؤهلون',   val: '312',   pct: 45, color: '#22c55e' },
      { label: 'متوسط الدرجة',        val: '74%',   pct: 74, color: '#3b82f6' },
    ],
    mockApps: [
      { name: 'Ahmed Al-Rashidi', role: 'Senior React Developer', score: 91, status: 'مؤهل'  },
      { name: 'Maria Santos',     role: 'Full Stack Engineer',    score: 78, status: 'مؤهل'  },
      { name: "Liam O'Brien",     role: 'Frontend Developer',     score: 52, status: 'جزئي'  },
    ],

    statsLabels: ['سيرة ذاتية محللة', 'أسرع في الاختيار', 'شركة تثق بنا', 'دقة التقييم'],

    featuresTag:   'ميزات المنصة',
    featuresTitle: 'كل ما يحتاجه فريق التوظيف لديك',
    featuresSub:   'من لحظة وصول السيرة الذاتية حتى موعد المقابلة — أتمتة كاملة بالذكاء الاصطناعي.',
    features: [
      { title: 'تحليل السيرة الذاتية بالذكاء الاصطناعي', tag: 'المحرك الأساسي',   impact: 'وفّر 90% من وقت المراجعة',    desc: 'يقرأ محركنا المدعوم بـ Google Gemini كل سيرة ذاتية كما يفعل المجنّد الخبير — يفهم السياق لا الكلمات فقط. يستخرج المهارات، ويقيس عمق الخبرة، ويتحقق من التعليم، ويرصد الشهادات تلقائياً.', bullets: ['فهم اللغة الطبيعية', 'دعم صيغ متعددة (PDF, DOC, بريد)', 'استخراج البيانات في ثوانٍ'] },
      { title: 'تقييم المرشحين الذكي',                   tag: 'الذكاء',            impact: 'رتّب 100 سيرة في دقيقتين',   desc: 'يحصل كل مرشح على درجة موزونة عبر 7 محاور: المهارات، الخبرة، التعليم، الشهادات، المهارات الشخصية، المعرفة المجالية، والمتطلبات المخصصة. الأوزان مُعدّلة لاحتياجات وظيفتك.', bullets: ['إطار تقييم 7 محاور', 'أوزان تقييم قابلة للتخصيص', 'قرارات تلقائية: مؤهل / جزئي / مرفوض'] },
      { title: 'استقبال السيرة الذاتية بسلاسة',          tag: 'الأتمتة',           impact: 'لا إعداد مطلوب',              desc: 'لا بوابات للدخول، لا رفع يدوي. يرسل المرشحون سيرهم الذاتية بالبريد الإلكتروني وتتولى المنصة الباقي — اختر صندوق بريد مخصص أو أعد توجيه بريدك الحالي.', bullets: ['صندوق بريد مخصص لكل وظيفة', 'إعادة توجيه من أي عنوان', 'صفر رفع يدوي'] },
      { title: 'إدارة الحملات الوظيفية',                 tag: 'سير العمل',         impact: 'كل الوظائف، لوحة واحدة',    desc: 'أدِر كل وظيفة مفتوحة كحملة منظمة. حدد الوظيفة، ضع تاريخ الإغلاق، تتبع عدد الطلبات، وحوّل الإعلان من نشط إلى مغلق بنقرة واحدة.', bullets: ['لوحة تحكم مركزية للوظائف', 'عدادات طلبات فورية', 'دورة حياة: مسودة ← نشط ← مغلق'] },
      { title: 'إدارة الفريق والمستأجرين',               tag: 'متعدد المستأجرين',  impact: 'مصمم للوكالات',              desc: 'مصمم للوكالات والمؤسسات التي تدير وحدات توظيف متعددة. يحصل كل مستأجر على مساحة عمل معزولة، وظائفه ومرشحيه الخاصة، ومسؤول مخصص — كل ذلك تحت سقف واحد.', bullets: ['مساحات عمل معزولة لكل مستأجر', 'صلاحيات الوصول (مشرف / عضو)', 'تعليق أو تفعيل المستأجرين فوراً'] },
      { title: 'ذكاء المقابلات',                          tag: 'دعم القرار',        impact: 'دليل مقابلة جاهز فوراً',     desc: 'تجاوز الدرجات. يتضمن كل تحليل مرشح دليلاً منظماً للمقابلة: تحليل الفجوات، مناطق التركيز المقترحة، وأسئلة مقابلة مخصصة مولّدة من السيرة الذاتية والوصف الوظيفي.', bullets: ['أسئلة مقابلة مولّدة تلقائياً', 'فجوات المهارات المحددة لكل مرشح', 'ملاحظات تقييم لمديري التوظيف'] },
    ],

    stepsTag:   'كيف يعمل',
    stepsTitle: 'من نشر الوظيفة إلى التوظيف في 4 خطوات',
    stepsSub:   'لا تكاملات معقدة، لا تدريب على أدوات جديدة. ستحلل السير الذاتية في نفس يوم التسجيل.',
    steps: [
      { icon: '📋', title: 'انشر وظيفتك',               desc: 'الصق وصف وظيفتك ويستخرج الذكاء الاصطناعي معايير التقييم تلقائياً — المهارات، الخبرة، التعليم، والمتطلبات المخصصة. الإعداد يستغرق دقائق.', tags: ['معايير AI', 'أوزان مخصصة'] },
      { icon: '🔗', title: 'شارك واجمع الطلبات',        desc: 'وزّع بريد الوظيفة المخصص أو رابط التقديم العام. يتقدم المرشحون ببساطة بإرسال سيرتهم بالبريد أو ملء نموذج قصير — بلا أي احتكاك من جهتهم.', tags: ['بريد مخصص', 'رابط تقديم'] },
      { icon: '🤖', title: 'الذكاء الاصطناعي يقيّم الجميع', desc: 'كل سيرة ذاتية تُلتقط تلقائياً وتُحلَّل وتُرتَّب عبر 7 محاور في ثوانٍ. بلا رفع يدوي ولا طلبات مفقودة.', tags: ['Gemini AI', 'تقييم 7 محاور', 'ترتيب تلقائي'] },
      { icon: '🏆', title: 'وظّف الأفضل',               desc: 'افتح لوحة التحكم لتجد قائمة مختصرة مرتبة مع تحليل كامل وتحديد للفجوات وأسئلة مقابلة مولّدة بالذكاء الاصطناعي جاهزة للاستخدام.', tags: ['قائمة مرتبة', 'دليل المقابلة'] },
    ],

    midCtaTag:   'ابدأ اليوم',
    midCtaTitle: 'ابدأ تحليل السير الذاتية في دقائق',
    midCtaSub:   'لا تكاملات للإعداد، لا تدريب مطلوب. الصق وصف وظيفتك وأنت جاهز.',
    midCtaBtn1:  'ابدأ التجربة المجانية',
    midCtaBtn2:  'شاهد كيف يعمل',
    midCtaFine:  '14 يوم تجريبي · بلا بطاقة ائتمانية · إلغاء في أي وقت',

    whoTag:   'مصمم لـ',
    whoTitle: 'من يستفيد من المنصة؟',
    whoSub:   'من الوكالات الصغيرة إلى أقسام الموارد البشرية في الشركات الكبرى — CV Analyzer يتناسب مع أي عملية توظيف.',
    personas: [
      { icon: '👥', title: 'فرق الموارد البشرية',           desc: 'قلّص وقت الفرز وامنح فريقك وقتاً للمحادثات لا لقراءة السير الذاتية.' },
      { icon: '🏢', title: 'وكالات التوظيف',                desc: 'أدِر مسارات عملاء غير محدودة من منصة واحدة مع عزل كامل لكل عميل.' },
      { icon: '🏛️', title: 'المؤسسات الكبرى',               desc: 'صلاحيات وصول متعددة وتحكم في الفرق وسير عمل توظيف قابلة للتدقيق.' },
      { icon: '⚖️', title: 'الجهات الحكومية',               desc: 'تقييم منظم قائم على المعايير لعملية توظيف شفافة ومتوافقة مع اللوائح.' },
      { icon: '🎓', title: 'الجامعات والبرامج الأكاديمية',  desc: 'تعامل مع أعداد كبيرة من طلبات البرامج التنافسية بكل سهولة.' },
      { icon: '⚡', title: 'التوظيف بحجم كبير',             desc: 'عالج آلاف السير الذاتية شهرياً دون الحاجة لزيادة عدد الموظفين.' },
    ],

    testimonialsTag:   'آراء العملاء',
    testimonialsTitle: 'موثوق من فرق التوظيف',
    testimonials: [
      { quote: 'قلّصنا وقت الاختيار من 3 أيام إلى 4 ساعات. درجات الذكاء الاصطناعي تتوافق تقريباً مع ما يقرره مجنّدونا الخبراء — أصبح لا غنى عنه.', name: 'سارة المنصوري', role: 'رئيسة المواهب، TechCorp MENA',        initials: 'سم', color: 'bg-violet-500' },
      { quote: 'الاستقبال بالبريد الإلكتروني ذكي جداً. المرشحون يرسلون سيرهم الذاتية كما اعتادوا — لا شيء يتغير من جهتهم، لكن من جهتنا كل شيء منظم ومقيّم فوراً.', name: 'جيمس أوكونكو', role: 'مدير التوظيف، BuildRight Group',       initials: 'جأ', color: 'bg-blue-500'   },
      { quote: 'إدارة 12 عميلاً عبر منصة واحدة، كل منها معزول تماماً. وفّرت لنا البنية متعددة المستأجرين بناء بنيتنا التحتية الخاصة. تستحق كل فلس.',               name: 'بريا ناير',    role: 'مديرة، HireFlow Agency',               initials: 'بن', color: 'bg-emerald-500' },
    ],

    pricingTag:   'الأسعار',
    pricingTitle: 'أسعار بسيطة وشفافة',
    pricingSub:   'ابدأ مجاناً. لا بطاقة ائتمانية مطلوبة.',
    pricingEnterprise: { name: 'المؤسسات / خطة مخصصة', price: 'مخصص', badge: '', desc: 'للوكالات والمنظمات متعددة الوحدات. حدود مخصصة وتأهيل مخصص واتفاقية مستوى الخدمة.', cta: 'تواصل معنا', features: ['كل ما في الخطة الاحترافية', 'سير ذاتية وحملات غير محدودة', 'إدارة متعددة المستأجرين', 'تأهيل مخصص', 'اتفاقية مستوى الخدمة وتكاملات مخصصة'] },

    faqTag:   'الأسئلة الشائعة',
    faqTitle: 'أسئلة شائعة',
    faqs: [
      { q: 'كيف يعمل استقبال السيرة الذاتية فعلياً؟',        a: 'تحصل على عنوان بريد إلكتروني مخصص لكل إعلان وظيفي (أو استخدم إعادة التوجيه من صندوق بريد الموارد البشرية الحالي). عندما يرسل المرشح سيرته الذاتية، يلتقطها نظامنا تلقائياً، ويستخرج المستند، ويمرره عبر خط تحليل الذكاء الاصطناعي — بلا خطوات يدوية.' },
      { q: 'ما صيغ الملفات المدعومة؟',                        a: 'ندعم PDF, DOC, DOCX، والسير الذاتية النصية المرسلة كمرفقات بريد إلكتروني. يتعامل الذكاء الاصطناعي بمرونة مع التنسيقات المختلفة، بما فيها الجداول والتخطيطات متعددة الأعمدة والهياكل غير القياسية.' },
      { q: 'هل يمكنني تخصيص طريقة تقييم المرشحين؟',          a: 'نعم. تولّد كل وظيفة أوزان تقييم تلقائياً استناداً إلى وصفها، لكن يمكنك تعديل توزيع الوزن عبر المحاور السبعة (المهارات، الخبرة، التعليم، الشهادات، المهارات الشخصية، المعرفة المجالية، المتطلبات الأخرى) قبل التحليل أو بعده.' },
      { q: 'هل هناك تجربة مجانية؟',                           a: 'نعم — تشمل خطة المبتدئين تجربة مجانية لمدة 14 يوماً بلا بطاقة ائتمانية. تحصل على 3 حملات و50 تحليل سيرة ذاتية للتحقق من ملاءمة المنصة لسير عملك.' },
      { q: 'كيف تُبقى البيانات منفصلة بين المستأجرين؟',      a: 'كل مستأجر معزول تماماً على مستوى البيانات. الوظائف والمرشحون ونتائج التحليل مرتبطة بمعرّف المستأجر المضمن في رمز المصادقة. لا يمكن الوصول إلى بيانات مستأجر آخر.' },
    ],

    ctaTitle: 'هل أنت مستعد لتحويل عملية التوظيف؟',
    ctaSub:   'انضم إلى أكثر من 320 شركة تستخدم الذكاء الاصطناعي للعثور على مرشحين أفضل، بشكل أسرع. ابدأ تجربتك المجانية اليوم.',
    ctaBtn:   'ابدأ التجربة المجانية',
    ctaFine:  'لا بطاقة ائتمانية · تجربة مجانية 14 يوماً · إلغاء في أي وقت',

    footerLinks: ['سياسة الخصوصية', 'شروط الخدمة', 'تواصل معنا'],
    footerCopy:  '© 2026 CV Analyzer. جميع الحقوق محفوظة.',
  },
};

// ── Component ─────────────────────────────────────────────────────────────────

interface ApiPlan {
  plan_id: string; plan_code: string; plan_name: string;
  description: string | null; monthly_price: number;
  trial_days: number; max_campaigns: number;
  max_processed_cvs_per_month: number; max_users: number;
  features: { feature_key: string; feature_name: string; value_type: string; value_boolean: boolean | null; value_number: number | null; value_text: string | null }[];
}

const PLAN_GRADIENTS = ['from-slate-500 to-slate-600', 'from-sky-500 to-blue-600', 'from-violet-500 to-purple-600'];

const PLAN_NAME_AR: Record<string, string> = {
  trial:        'التجربة المجانية',
  starter:      'البداية',
  professional: 'الاحترافي',
  enterprise:   'المؤسسات',
};
function localPlanName(code: string, name: string, lang: 'en' | 'ar'): string {
  return lang === 'ar' ? (PLAN_NAME_AR[code] ?? name) : name;
}

export const LandingPage: React.FC<LandingPageProps> = ({ onGetStarted, onSignIn }) => {
  const [lang, setLang] = useState<'en' | 'ar'>('en');
  const [openFaq, setOpenFaq] = useState<number | null>(null);
  const [scrolled, setScrolled] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [apiPlans, setApiPlans] = useState<ApiPlan[]>([]);
  const t = T[lang];
  const isAr = lang === 'ar';
  const statsRef = useInView(0.3);

  const SUBSCRIPTION_PUBLIC_URL = 'http://72.62.31.221:8000/subscription/plans';
  useEffect(() => {
    fetch(SUBSCRIPTION_PUBLIC_URL)
      .then((r) => r.ok ? r.json() : Promise.reject())
      .then((d) => { if (d.plans?.length) setApiPlans(d.plans); })
      .catch(() => {});
  }, []);

  const cv      = useCounter(50000, 2000, statsRef.inView);
  const time    = useCounter(85,    1600, statsRef.inView);
  const clients = useCounter(320,   1800, statsRef.inView);
  const acc     = useCounter(94,    1600, statsRef.inView);
  const statVals = [cv.toLocaleString() + '+', time + '%', clients + '+', acc + '%'];

  useEffect(() => {
    const h = () => setScrolled(window.scrollY > 30);
    window.addEventListener('scroll', h);
    return () => window.removeEventListener('scroll', h);
  }, []);

  useEffect(() => {
    document.documentElement.lang = lang === 'ar' ? 'ar' : 'en';
    document.documentElement.dir  = lang === 'ar' ? 'rtl' : 'ltr';
    return () => {
      document.documentElement.lang = 'en';
      document.documentElement.dir  = 'ltr';
    };
  }, [lang]);

  // Close mobile menu on scroll
  useEffect(() => {
    if (mobileMenuOpen) setMobileMenuOpen(false);
  }, [scrolled]);

  const scrollTo = (id: string) => {
    setMobileMenuOpen(false);
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' });
  };

  const Arrow = () => (
    <span className={`inline-flex transition-transform group-hover:translate-x-1 ${isAr ? 'rotate-180' : ''}`}>
      <ArrowRightIcon />
    </span>
  );

  return (
    <div dir={t.dir} style={{ fontFamily: t.font }} className="min-h-screen bg-white">

      {/* ── Navbar ── */}
      <nav className={`fixed top-0 left-0 right-0 z-50 transition-all duration-300 ${scrolled ? 'bg-white/95 backdrop-blur shadow-sm border-b border-gray-100' : 'bg-transparent'}`}>
        <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">

          {/* Logo */}
          <div className="flex items-center gap-2 flex-shrink-0">
            <div className="w-8 h-8 rounded-lg flex items-center justify-center" style={{ background: 'linear-gradient(135deg,#6366f1,#2563eb)' }}>
              <svg viewBox="0 0 24 24" fill="white" className="w-5 h-5"><path d="M9 12h6m-6 4h6m2 5H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5.586a1 1 0 0 1 .707.293l5.414 5.414a1 1 0 0 1 .293.707V19a2 2 0 0 1-2 2z"/></svg>
            </div>
            <span className="font-bold text-gray-900 text-lg tracking-tight">CV Analyzer</span>
          </div>

          {/* Desktop nav links */}
          <div className="hidden md:flex items-center gap-7">
            {t.navLinks.slice(0, 3).map((label, i) => (
              <button key={label} onClick={() => scrollTo(t.navIds[i])} className="text-sm font-medium text-gray-600 hover:text-gray-900 transition-colors">
                {label}
              </button>
            ))}
          </div>

          {/* Desktop CTAs + lang toggle */}
          <div className="hidden md:flex items-center gap-2">
            <button
              onClick={() => setLang(isAr ? 'en' : 'ar')}
              className="flex items-center gap-1.5 text-sm font-semibold px-3 py-1.5 rounded-lg border border-gray-200 hover:border-indigo-400 text-gray-600 hover:text-indigo-600 transition-all"
            >
              <GlobeIcon />
              {t.langBtn}
            </button>
            <button onClick={onSignIn} className="text-sm font-semibold text-gray-700 hover:text-gray-900 transition-colors px-3 py-2">
              {t.signIn}
            </button>
            <button onClick={onGetStarted} className="text-sm font-semibold text-white px-5 py-2 rounded-xl transition-all hover:opacity-90 hover:shadow-lg" style={{ background: 'linear-gradient(135deg,#6366f1,#2563eb)' }}>
              {t.startFreeTrial}
            </button>
          </div>

          {/* Mobile: lang + hamburger */}
          <div className="flex md:hidden items-center gap-2">
            <button
              onClick={() => setLang(isAr ? 'en' : 'ar')}
              className="flex items-center gap-1 text-xs font-semibold px-2 py-1.5 rounded-lg border border-gray-200 text-gray-600 hover:border-indigo-400 hover:text-indigo-600 transition-all"
            >
              <GlobeIcon />
              {t.langBtn}
            </button>
            <button
              onClick={() => setMobileMenuOpen(o => !o)}
              className={`p-2 rounded-lg transition-colors ${scrolled ? 'text-gray-700 hover:bg-gray-100' : 'text-white hover:bg-white/10'}`}
              aria-label="Toggle menu"
            >
              {mobileMenuOpen ? <XIcon /> : <MenuIcon />}
            </button>
          </div>
        </div>

        {/* Mobile menu drawer */}
        {mobileMenuOpen && (
          <div className="md:hidden bg-white border-t border-gray-100 shadow-lg">
            <div className="px-6 py-4 space-y-1">
              {t.navLinks.map((label, i) => (
                <button key={label} onClick={() => scrollTo(t.navIds[i])} className="w-full text-start px-3 py-2.5 text-sm font-medium text-gray-700 hover:text-indigo-600 hover:bg-indigo-50 rounded-lg transition-colors">
                  {label}
                </button>
              ))}
              <div className="pt-3 pb-1 border-t border-gray-100 flex flex-col gap-2">
                <button onClick={() => { setMobileMenuOpen(false); onSignIn(); }} className="w-full text-center px-4 py-2.5 text-sm font-semibold text-gray-700 border border-gray-200 rounded-xl hover:border-gray-300 transition-colors">
                  {t.signIn}
                </button>
                <button onClick={() => { setMobileMenuOpen(false); onGetStarted(); }} className="w-full text-center px-4 py-2.5 text-sm font-bold text-white rounded-xl transition-all hover:opacity-90" style={{ background: 'linear-gradient(135deg,#6366f1,#2563eb)' }}>
                  {t.startFreeTrial}
                </button>
              </div>
            </div>
          </div>
        )}
      </nav>

      {/* ── Hero ── */}
      <section className="relative pt-32 pb-24 px-6 overflow-hidden" style={{ background: 'linear-gradient(135deg,#0f0c29 0%,#1a1245 30%,#24243e 60%,#0f2027 100%)' }}>
        <div className="absolute top-20 left-1/4 w-96 h-96 rounded-full opacity-20 blur-3xl pointer-events-none" style={{ background: 'radial-gradient(circle,#6366f1,transparent)' }} />
        <div className="absolute bottom-0 right-1/4 w-80 h-80 rounded-full opacity-15 blur-3xl pointer-events-none" style={{ background: 'radial-gradient(circle,#2563eb,transparent)' }} />
        <div className="absolute top-40 right-10 w-60 h-60 rounded-full opacity-10 blur-2xl pointer-events-none" style={{ background: 'radial-gradient(circle,#a855f7,transparent)' }} />

        <div className="relative max-w-5xl mx-auto text-center">
          <div className="inline-flex items-center gap-2 bg-white/10 backdrop-blur border border-white/20 rounded-full px-4 py-2 mb-8">
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
            <span className="text-white/80 text-xs font-semibold tracking-wide uppercase">{t.badge}</span>
          </div>

          <h1 className="text-5xl md:text-7xl font-extrabold text-white leading-tight mb-6 tracking-tight">
            {t.heroH1}<br />
            <span style={{ backgroundImage: 'linear-gradient(90deg,#a78bfa,#60a5fa,#34d399)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent', backgroundClip: 'text' }}>
              {t.heroH2}
            </span>
          </h1>

          <p className="text-lg md:text-xl text-white/70 max-w-2xl mx-auto mb-8 leading-relaxed">{t.heroSub}</p>

          {/* Impact metric badges */}
          <div className="flex flex-wrap items-center justify-center gap-3 mb-10">
            {t.heroMetrics.map((m, i) => (
              <div key={i} className="flex items-center gap-2 bg-white/10 backdrop-blur border border-white/15 rounded-full px-4 py-2">
                <span className="text-base">{m.icon}</span>
                <span className="text-white/85 text-sm font-medium">{m.text}</span>
              </div>
            ))}
          </div>

          <div className="flex flex-col sm:flex-row items-center justify-center gap-4 mb-16">
            <button onClick={onGetStarted} className="group flex items-center gap-2 text-white font-bold px-8 py-4 rounded-2xl text-base transition-all hover:scale-105 hover:shadow-2xl" style={{ background: 'linear-gradient(135deg,#6366f1,#2563eb)', boxShadow: '0 8px 32px rgba(99,102,241,0.4)' }}>
              {t.cta1}<Arrow />
            </button>
            <button onClick={() => scrollTo('how-it-works')} className="flex items-center gap-2 text-white/80 hover:text-white font-semibold px-8 py-4 rounded-2xl border border-white/20 hover:border-white/40 transition-all text-base">
              {t.cta2}
            </button>
          </div>

          {/* Dashboard mockup */}
          <div className="relative max-w-4xl mx-auto">
            <div className="rounded-2xl border border-white/10 overflow-hidden shadow-2xl" style={{ background: 'rgba(255,255,255,0.05)', backdropFilter: 'blur(20px)' }}>
              <div className="flex items-center gap-2 px-4 py-3 border-b border-white/10" style={{ background: 'rgba(255,255,255,0.04)' }}>
                <div className="w-3 h-3 rounded-full bg-red-400/70" /><div className="w-3 h-3 rounded-full bg-amber-400/70" /><div className="w-3 h-3 rounded-full bg-emerald-400/70" />
                <span className="ml-3 text-white/30 text-xs font-mono">{t.mockTitle}</span>
              </div>
              <div className="p-6 grid grid-cols-3 gap-4">
                {t.mockStats.map(s => (
                  <div key={s.label} className="rounded-xl p-4 text-left" style={{ background: 'rgba(255,255,255,0.06)' }}>
                    <div className="text-white/50 text-xs mb-1">{s.label}</div>
                    <div className="text-white text-2xl font-bold mb-2">{s.val}</div>
                    <div className="h-1.5 rounded-full bg-white/10"><div className="h-full rounded-full" style={{ width: `${s.pct}%`, background: s.color }} /></div>
                  </div>
                ))}
                <div className="col-span-3 rounded-xl overflow-hidden" style={{ background: 'rgba(255,255,255,0.06)' }}>
                  <div className="px-4 py-3 border-b border-white/10 flex items-center justify-between">
                    <span className="text-white/70 text-sm font-semibold">{t.mockRecent}</span>
                    <span className="text-xs text-white/40">{t.mockLive}</span>
                  </div>
                  <div className="divide-y divide-white/5">
                    {t.mockApps.map(app => (
                      <div key={app.name} className="flex items-center gap-4 px-4 py-3">
                        <div className="w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold text-white flex-shrink-0" style={{ background: 'linear-gradient(135deg,#6366f1,#2563eb)' }}>
                          {app.name.split(' ').map(n => n[0]).join('')}
                        </div>
                        <div className="flex-1 min-w-0 text-left">
                          <div className="text-white/90 text-sm font-medium truncate">{app.name}</div>
                          <div className="text-white/40 text-xs truncate">{app.role}</div>
                        </div>
                        <div className="text-right flex-shrink-0">
                          <div className="text-white font-bold text-sm">{app.score}%</div>
                          <div className={`text-xs font-medium ${app.status === 'qualified' || app.status === 'مؤهل' ? 'text-emerald-400' : 'text-amber-400'}`}>{app.status}</div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ── Stats ── */}
      <section ref={statsRef.ref} className="py-16 px-6 border-b border-gray-100">
        <div className="max-w-5xl mx-auto grid grid-cols-2 md:grid-cols-4 gap-8 text-center">
          {statVals.map((val, i) => (
            <div key={i}>
              <div className="text-4xl md:text-5xl font-extrabold text-gray-900 tabular-nums">{val}</div>
              <div className="text-sm text-gray-500 mt-1 font-medium">{t.statsLabels[i]}</div>
            </div>
          ))}
        </div>
      </section>

      {/* ── Features ── */}
      <section id="features" className="py-24 px-6" style={{ background: '#fafafa' }}>
        <div className="max-w-7xl mx-auto">
          <div className="text-center mb-16">
            <span className="inline-block text-xs font-bold uppercase tracking-widest text-indigo-600 mb-3">{t.featuresTag}</span>
            <h2 className="text-4xl md:text-5xl font-extrabold text-gray-900 mb-4 tracking-tight">{t.featuresTitle}</h2>
            <p className="text-lg text-gray-500 max-w-2xl mx-auto">{t.featuresSub}</p>
          </div>
          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
            {t.features.map((f, i) => {
              const c = featureColors[i];
              return (
                <div key={f.title} className={`group rounded-2xl border p-6 hover:shadow-xl transition-all duration-300 hover:-translate-y-1 bg-white ${c.border}`}>
                  <div className="flex items-start justify-between mb-4">
                    <div className={`w-13 h-13 w-12 h-12 rounded-xl ${c.bg} flex items-center justify-center flex-shrink-0`}>
                      <div style={{ color: c.cs }}>{featureIcons[i]}</div>
                    </div>
                    <span className={`text-xs font-bold px-2.5 py-1 rounded-full ${c.impact}`}>{f.impact}</span>
                  </div>
                  <span className="text-[10px] font-black uppercase tracking-widest text-gray-400">{f.tag}</span>
                  <h3 className="text-lg font-bold text-gray-900 mt-1 mb-2">{f.title}</h3>
                  <p className="text-gray-500 text-sm leading-relaxed mb-4">{f.desc}</p>
                  <ul className="space-y-1.5">
                    {f.bullets.map(b => (
                      <li key={b} className="flex items-start gap-2 text-sm text-gray-600">
                        <span className="text-emerald-500 mt-0.5 flex-shrink-0"><CheckIcon /></span>{b}
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })}
          </div>
        </div>
      </section>

      {/* ── How It Works ── */}
      <section id="how-it-works" className="py-24 px-6 bg-white">
        <div className="max-w-6xl mx-auto">
          <div className="text-center mb-16">
            <span className="inline-block text-xs font-bold uppercase tracking-widest text-indigo-600 mb-3">{t.stepsTag}</span>
            <h2 className="text-4xl md:text-5xl font-extrabold text-gray-900 mb-4 tracking-tight">{t.stepsTitle}</h2>
            <p className="text-lg text-gray-500 max-w-xl mx-auto">{t.stepsSub}</p>
          </div>

          <div className="relative grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-8 lg:gap-6">
            {/* Gradient connector line — desktop only */}
            <div className="hidden lg:block absolute top-8 left-[12.5%] right-[12.5%] h-0.5 bg-gradient-to-r from-violet-300 via-blue-300 via-indigo-300 to-emerald-300 z-0" />

            {t.steps.map((step, i) => {
              const sc = stepColors[i];
              return (
                <div key={i} className="relative z-10 flex flex-col items-center">
                  {/* Numbered circle */}
                  <div
                    className="w-16 h-16 rounded-full flex items-center justify-center text-white text-xl font-black shadow-lg ring-4 ring-white mb-6 flex-shrink-0"
                    style={{ background: sc.bg }}
                  >
                    {i + 1}
                  </div>
                  {/* Card */}
                  <div className="w-full text-center bg-white rounded-2xl border border-gray-100 shadow-sm p-6 hover:shadow-md transition-shadow">
                    <div className="text-3xl mb-3">{step.icon}</div>
                    <h3 className="font-bold text-gray-900 mb-2 text-base">{step.title}</h3>
                    <p className="text-gray-500 text-sm leading-relaxed mb-4">{step.desc}</p>
                    <div className="flex flex-wrap gap-1.5 justify-center">
                      {step.tags.map(tag => (
                        <span key={tag} className={`text-xs font-semibold px-2.5 py-1 rounded-full ${sc.tag}`}>{tag}</span>
                      ))}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </section>

      {/* ── Mid-page CTA ── */}
      <section className="py-20 px-6" style={{ background: 'linear-gradient(135deg,#f5f3ff,#eff6ff,#ecfdf5)' }}>
        <div className="max-w-3xl mx-auto text-center">
          <span className="inline-block text-xs font-bold uppercase tracking-widest text-indigo-600 mb-4">{t.midCtaTag}</span>
          <h2 className="text-3xl md:text-4xl font-extrabold text-gray-900 mb-4 tracking-tight">{t.midCtaTitle}</h2>
          <p className="text-gray-500 text-lg mb-8 max-w-xl mx-auto">{t.midCtaSub}</p>
          <div className="flex flex-col sm:flex-row items-center justify-center gap-3 mb-4">
            <button onClick={onGetStarted} className="group flex items-center gap-2 text-white font-bold px-8 py-3.5 rounded-2xl text-sm transition-all hover:scale-105 hover:shadow-xl" style={{ background: 'linear-gradient(135deg,#6366f1,#2563eb)', boxShadow: '0 6px 24px rgba(99,102,241,0.35)' }}>
              {t.midCtaBtn1}<Arrow />
            </button>
            <button onClick={() => scrollTo('how-it-works')} className="flex items-center gap-2 text-gray-600 hover:text-gray-900 font-semibold px-8 py-3.5 rounded-2xl border border-gray-200 hover:border-gray-300 transition-all text-sm bg-white">
              {t.midCtaBtn2}
            </button>
          </div>
          <p className="text-gray-400 text-xs">{t.midCtaFine}</p>
        </div>
      </section>

      {/* ── Who Is This For ── */}
      <section className="py-24 px-6 bg-white">
        <div className="max-w-6xl mx-auto">
          <div className="text-center mb-14">
            <span className="inline-block text-xs font-bold uppercase tracking-widest text-indigo-600 mb-3">{t.whoTag}</span>
            <h2 className="text-4xl md:text-5xl font-extrabold text-gray-900 mb-4 tracking-tight">{t.whoTitle}</h2>
            <p className="text-lg text-gray-500 max-w-2xl mx-auto">{t.whoSub}</p>
          </div>
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
            {t.personas.map((p, i) => (
              <div key={i} className="group flex items-start gap-4 p-6 rounded-2xl border border-gray-100 hover:border-indigo-100 hover:shadow-md hover:-translate-y-0.5 transition-all duration-200 bg-white">
                <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-indigo-50 to-blue-50 border border-indigo-100 flex items-center justify-center text-2xl flex-shrink-0">
                  {p.icon}
                </div>
                <div>
                  <h3 className="font-bold text-gray-900 mb-1 text-sm">{p.title}</h3>
                  <p className="text-gray-500 text-sm leading-relaxed">{p.desc}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Testimonials ── */}
      <section className="py-24 px-6" style={{ background: 'linear-gradient(135deg,#f5f3ff,#eff6ff)' }}>
        <div className="max-w-6xl mx-auto">
          <div className="text-center mb-14">
            <span className="inline-block text-xs font-bold uppercase tracking-widest text-indigo-600 mb-3">{t.testimonialsTag}</span>
            <h2 className="text-4xl md:text-5xl font-extrabold text-gray-900 tracking-tight">{t.testimonialsTitle}</h2>
          </div>
          <div className="grid md:grid-cols-3 gap-6">
            {t.testimonials.map(tst => (
              <div key={tst.name} className="bg-white rounded-2xl p-6 shadow-sm border border-white hover:shadow-lg transition-shadow">
                <div className="flex gap-0.5 mb-4">{[...Array(5)].map((_, i) => <StarIcon key={i} />)}</div>
                <p className="text-gray-700 leading-relaxed mb-6 text-sm">"{tst.quote}"</p>
                <div className="flex items-center gap-3">
                  <div className={`w-10 h-10 rounded-full ${tst.color} flex items-center justify-center text-white text-sm font-bold flex-shrink-0`}>{tst.initials}</div>
                  <div>
                    <div className="font-semibold text-gray-900 text-sm">{tst.name}</div>
                    <div className="text-gray-500 text-xs">{tst.role}</div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Pricing ── */}
      <section id="pricing" className="py-24 px-6 bg-white">
        <div className="max-w-6xl mx-auto">
          <div className="text-center mb-14">
            <span className="inline-block text-xs font-bold uppercase tracking-widest text-indigo-600 mb-3">{t.pricingTag}</span>
            <h2 className="text-4xl md:text-5xl font-extrabold text-gray-900 mb-4 tracking-tight">{t.pricingTitle}</h2>
            <p className="text-gray-500 text-lg">{t.pricingSub}</p>
          </div>
          <div className="grid sm:grid-cols-2 xl:grid-cols-4 gap-6 items-start">
            {/* Dynamic plans from API (Trial, Starter, Professional) — enterprise excluded */}
            {apiPlans.filter(p => p.plan_code !== 'enterprise').map((plan, idx) => {
              const isHighlight = plan.plan_code === 'professional';
              const isTrial = plan.plan_code === 'trial';
              const gradient = PLAN_GRADIENTS[idx % PLAN_GRADIENTS.length];
              const cta = isTrial
                ? (lang === 'ar' ? 'ابدأ التجربة المجانية' : 'Start Free Trial')
                : (lang === 'ar' ? 'اشترك الآن' : 'Subscribe');
              const priceDisplay = plan.monthly_price === 0
                ? (lang === 'ar' ? 'مجاني' : 'Free')
                : `$${plan.monthly_price}`;
              return (
                <div key={plan.plan_id} className={`relative rounded-2xl border overflow-hidden transition-all flex flex-col ${isHighlight ? 'border-indigo-500 shadow-2xl shadow-indigo-100' : 'border-gray-200 hover:border-gray-300 hover:shadow-md'}`}>
                  {isHighlight && (
                    <div className="absolute -top-3.5 left-1/2 -translate-x-1/2 bg-amber-400 text-amber-900 text-xs font-black uppercase tracking-widest px-4 py-1 rounded-full whitespace-nowrap z-10">
                      {lang === 'ar' ? 'الأكثر شيوعاً' : 'Most Popular'}
                    </div>
                  )}
                  <div className={`bg-gradient-to-br ${gradient} p-6 text-white`}>
                    <div className="text-sm font-black uppercase tracking-widest opacity-80 mb-1">{localPlanName(plan.plan_code, plan.plan_name, lang)}</div>
                    <div className="flex items-end gap-1">
                      <span className="text-4xl font-extrabold">{priceDisplay}</span>
                      {plan.monthly_price > 0 && <span className="text-sm opacity-70 mb-1">{lang === 'ar' ? '/شهر' : '/mo'}</span>}
                    </div>
                    {isTrial && <div className="text-xs mt-1 opacity-80">{plan.trial_days} {lang === 'ar' ? 'يوم تجريبي' : 'day trial'}</div>}
                  </div>
                  <div className="p-6 flex-1 flex flex-col bg-white">
                    {plan.description && <p className="text-sm text-gray-500 mb-4">{plan.description}</p>}
                    <ul className="space-y-2 flex-1 mb-6">
                      {[
                        { k: 'monthly_cv_processing', suffix: lang === 'ar' ? ' سيرة/شهر' : ' CVs/mo' },
                        { k: 'active_job_campaigns',  suffix: lang === 'ar' ? ' حملة' : ' campaigns' },
                        { k: 'number_of_users',       suffix: lang === 'ar' ? ' مستخدم' : ' users' },
                      ].map(({ k, suffix }) => {
                        const feat = plan.features.find((f) => f.feature_key === k);
                        if (!feat) return null;
                        return (
                          <li key={k} className="flex items-center gap-2 text-sm text-gray-600">
                            <span className="text-emerald-500 flex-shrink-0"><CheckIcon /></span>
                            <strong>{feat.value_number?.toLocaleString()}</strong>{suffix}
                          </li>
                        );
                      })}
                      {plan.features.filter((f) => f.value_type === 'boolean' && f.value_boolean).slice(0, 3).map((f) => (
                        <li key={f.feature_key} className="flex items-center gap-2 text-sm text-gray-600">
                          <span className="text-emerald-500 flex-shrink-0"><CheckIcon /></span>{f.feature_name}
                        </li>
                      ))}
                    </ul>
                    <button onClick={onGetStarted}
                      className="w-full py-3 rounded-xl font-bold text-sm transition-all hover:opacity-90 hover:shadow-lg text-white"
                      style={{ background: 'linear-gradient(135deg,#6366f1,#2563eb)' }}>
                      {cta}
                    </button>
                  </div>
                </div>
              );
            })}

            {/* Enterprise — always static "Contact Us" */}
            {(() => {
              const ep = t.pricingEnterprise;
              return (
                <div className="relative rounded-2xl border border-gray-200 hover:border-gray-300 hover:shadow-md overflow-hidden flex flex-col transition-all">
                  <div className="bg-gradient-to-br from-amber-500 to-orange-600 p-6 text-white">
                    <div className="text-sm font-black uppercase tracking-widest opacity-80 mb-1">{ep.name}</div>
                    <div className="text-4xl font-extrabold">{ep.price}</div>
                  </div>
                  <div className="p-6 flex-1 flex flex-col bg-white">
                    <p className="text-sm text-gray-500 mb-4">{ep.desc}</p>
                    <ul className="space-y-2 flex-1 mb-6">
                      {ep.features.map((f) => (
                        <li key={f} className="flex items-center gap-2 text-sm text-gray-600">
                          <span className="text-emerald-500 flex-shrink-0"><CheckIcon /></span>{f}
                        </li>
                      ))}
                    </ul>
                    <a href="mailto:sales@cvanalyzer.ai"
                      className="w-full py-3 rounded-xl font-bold text-sm text-white text-center block transition-all hover:opacity-90 hover:shadow-lg"
                      style={{ background: 'linear-gradient(135deg,#f59e0b,#ea580c)' }}>
                      {ep.cta}
                    </a>
                  </div>
                </div>
              );
            })()}

            {/* Fallback if API hasn't loaded yet */}
            {apiPlans.length === 0 && (
              <>
                {[
                  { name: 'Trial', price: 'Free', cta: lang === 'ar' ? 'ابدأ التجربة' : 'Start Free Trial', g: PLAN_GRADIENTS[0] },
                  { name: 'Starter', price: '$49', cta: lang === 'ar' ? 'اشترك' : 'Subscribe', g: PLAN_GRADIENTS[1] },
                  { name: 'Professional', price: '$149', cta: lang === 'ar' ? 'اشترك' : 'Subscribe', g: PLAN_GRADIENTS[2] },
                ].map((fp) => (
                  <div key={fp.name} className="rounded-2xl border border-gray-200 overflow-hidden flex flex-col animate-pulse">
                    <div className={`bg-gradient-to-br ${fp.g} p-6 text-white`}>
                      <div className="text-sm font-black uppercase tracking-widest opacity-80">{fp.name}</div>
                      <div className="text-4xl font-extrabold mt-1">{fp.price}</div>
                    </div>
                    <div className="p-6 flex-1 flex flex-col bg-white">
                      <div className="space-y-2 flex-1 mb-6">
                        {[...Array(4)].map((_, i) => <div key={i} className="h-4 bg-gray-100 rounded" />)}
                      </div>
                      <button onClick={onGetStarted} className="w-full py-3 rounded-xl font-bold text-sm text-white" style={{ background: 'linear-gradient(135deg,#6366f1,#2563eb)' }}>{fp.cta}</button>
                    </div>
                  </div>
                ))}
              </>
            )}
          </div>
        </div>
      </section>

      {/* ── FAQ ── */}
      <section id="faq" className="py-24 px-6" style={{ background: '#fafafa' }}>
        <div className="max-w-3xl mx-auto">
          <div className="text-center mb-14">
            <span className="inline-block text-xs font-bold uppercase tracking-widest text-indigo-600 mb-3">{t.faqTag}</span>
            <h2 className="text-4xl font-extrabold text-gray-900 tracking-tight">{t.faqTitle}</h2>
          </div>
          <div className="space-y-3">
            {t.faqs.map((faq, i) => (
              <div key={i} className="bg-white rounded-2xl border border-gray-100 overflow-hidden">
                <button onClick={() => setOpenFaq(openFaq === i ? null : i)} className="w-full flex items-center justify-between px-6 py-5 text-start">
                  <span className="font-semibold text-gray-900 text-sm">{faq.q}</span>
                  <span className={`text-gray-400 transition-transform flex-shrink-0 mx-4 ${openFaq === i ? 'rotate-45' : ''}`}>
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="w-5 h-5"><path d="M12 5v14M5 12h14"/></svg>
                  </span>
                </button>
                {openFaq === i && <div className="px-6 pb-5 text-sm text-gray-500 leading-relaxed border-t border-gray-50 pt-4">{faq.a}</div>}
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── CTA Banner ── */}
      <section className="py-24 px-6" style={{ background: 'linear-gradient(135deg,#0f0c29,#1a1245,#24243e)' }}>
        <div className="max-w-4xl mx-auto text-center">
          <h2 className="text-4xl md:text-5xl font-extrabold text-white mb-4 tracking-tight">{t.ctaTitle}</h2>
          <p className="text-white/60 text-lg mb-10 max-w-xl mx-auto">{t.ctaSub}</p>
          <button onClick={onGetStarted} className="group inline-flex items-center gap-2 text-white font-bold px-10 py-4 rounded-2xl text-base transition-all hover:scale-105 hover:shadow-2xl"
            style={{ background: 'linear-gradient(135deg,#6366f1,#2563eb)', boxShadow: '0 8px 40px rgba(99,102,241,0.4)' }}>
            {t.ctaBtn}<Arrow />
          </button>
          <p className="text-white/30 text-xs mt-4">{t.ctaFine}</p>
        </div>
      </section>

      {/* ── Footer ── */}
      <footer className="bg-gray-950 py-12 px-6">
        <div className="max-w-7xl mx-auto flex flex-col md:flex-row items-center justify-between gap-6">
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0" style={{ background: 'linear-gradient(135deg,#6366f1,#2563eb)' }}>
              <svg viewBox="0 0 24 24" fill="white" className="w-4 h-4"><path d="M9 12h6m-6 4h6m2 5H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5.586a1 1 0 0 1 .707.293l5.414 5.414a1 1 0 0 1 .293.707V19a2 2 0 0 1-2 2z"/></svg>
            </div>
            <span className="text-white font-bold">CV Analyzer</span>
          </div>
          <div className="flex gap-6 text-sm text-gray-500">
            {t.footerLinks.map(l => <a key={l} href="#" className="hover:text-gray-300 transition-colors">{l}</a>)}
          </div>
          <div className="text-gray-600 text-sm">{t.footerCopy}</div>
        </div>
      </footer>
    </div>
  );
};
