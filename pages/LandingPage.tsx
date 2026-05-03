
import React, { useState, useEffect, useRef } from 'react';

interface LandingPageProps {
  onGetStarted: () => void;
  onSignIn: () => void;
}

// Animated counter hook
function useCounter(target: number, duration: number = 1800, start: boolean = false) {
  const [count, setCount] = useState(0);
  useEffect(() => {
    if (!start) return;
    let startTime: number | null = null;
    const step = (timestamp: number) => {
      if (!startTime) startTime = timestamp;
      const progress = Math.min((timestamp - startTime) / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      setCount(Math.floor(eased * target));
      if (progress < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }, [target, duration, start]);
  return count;
}

// Intersection observer hook
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

// ── Icons ──────────────────────────────────────────────────────────────────

const BrainIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="w-7 h-7">
    <path d="M9.5 2a2.5 2.5 0 0 1 5 0v0a2.5 2.5 0 0 1 2.45 2H17a3 3 0 0 1 3 3v1a3 3 0 0 1-3 3h-.1a2.5 2.5 0 0 1-2.4 2H9.5a2.5 2.5 0 0 1-2.4-2H7a3 3 0 0 1-3-3V7a3 3 0 0 1 3-3h.05A2.5 2.5 0 0 1 9.5 2z"/>
    <path d="M12 6v6M9 9h6"/>
    <path d="M7 16a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2v-1H7v1z"/>
    <path d="M9 18v2M15 18v2"/>
  </svg>
);

const ChartIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="w-7 h-7">
    <path d="M3 3v18h18"/>
    <path d="M18 9l-5 5-3-3-5 5"/>
  </svg>
);

const MailIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="w-7 h-7">
    <rect x="2" y="4" width="20" height="16" rx="2"/>
    <path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>
  </svg>
);

const ShieldIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="w-7 h-7">
    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
    <path d="m9 12 2 2 4-4"/>
  </svg>
);

const UsersIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="w-7 h-7">
    <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/>
    <circle cx="9" cy="7" r="4"/>
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

// ── Feature Cards ──────────────────────────────────────────────────────────

const features = [
  {
    icon: <BrainIcon />,
    title: 'AI-Powered CV Analysis',
    tag: 'Core Engine',
    color: 'from-violet-500 to-purple-600',
    bg: 'bg-violet-50',
    border: 'border-violet-100',
    description:
      'Our Google Gemini-powered engine reads every CV the way a senior recruiter would — understanding context, not just keywords. It extracts skills, measures experience depth, validates education, and surfaces certifications automatically.',
    bullets: ['Natural language understanding', 'Multi-format CV support (PDF, DOC, email)', 'Structured data extraction in seconds'],
  },
  {
    icon: <ChartIcon />,
    title: 'Smart Candidate Scoring',
    tag: 'Intelligence',
    color: 'from-blue-500 to-cyan-500',
    bg: 'bg-blue-50',
    border: 'border-blue-100',
    description:
      'Every applicant receives a weighted score across 7 dimensions: Skills, Experience, Education, Certifications, Soft Skills, Domain Knowledge, and Custom Requirements — all tuned to your job\'s unique needs.',
    bullets: ['7-dimension scoring framework', 'Configurable scoring weights per job', 'Qualified / Partial / Rejected auto-decisions'],
  },
  {
    icon: <MailIcon />,
    title: 'Frictionless CV Ingestion',
    tag: 'Automation',
    color: 'from-emerald-500 to-teal-500',
    bg: 'bg-emerald-50',
    border: 'border-emerald-100',
    description:
      'No portals to log into, no uploads to manage. Candidates email their CVs and the platform handles the rest. Choose a dedicated platform inbox or forward from your existing HR email — both routes feed straight into analysis.',
    bullets: ['Dedicated inbox per job posting', 'Email forwarding from any address', 'Zero manual upload required'],
  },
  {
    icon: <ZapIcon />,
    title: 'Campaign Management',
    tag: 'Workflow',
    color: 'from-orange-500 to-amber-500',
    bg: 'bg-orange-50',
    border: 'border-orange-100',
    description:
      'Manage every open role as a structured campaign. Define the job, set closing dates, track live application counts, and flip a posting from Active to Closed in one click — all from a single dashboard.',
    bullets: ['Centralized jobs dashboard', 'Real-time application counters', 'Draft → Active → Closed lifecycle'],
  },
  {
    icon: <UsersIcon />,
    title: 'Team & Tenant Control',
    tag: 'Multi-Tenant',
    color: 'from-rose-500 to-pink-500',
    bg: 'bg-rose-50',
    border: 'border-rose-100',
    description:
      'Built for agencies and enterprises that run multiple hiring units. Each tenant gets an isolated workspace, their own jobs and candidates, and a dedicated admin — all managed under a single super-admin roof.',
    bullets: ['Isolated tenant workspaces', 'Role-based access (Admin / Member)', 'Suspend or activate tenants instantly'],
  },
  {
    icon: <ShieldIcon />,
    title: 'Interview Intelligence',
    tag: 'Decision Support',
    color: 'from-indigo-500 to-blue-600',
    bg: 'bg-indigo-50',
    border: 'border-indigo-100',
    description:
      'Go beyond scores. Each candidate analysis includes a structured interview guide: gap analysis, suggested focus areas, and tailored interview questions generated from the CV and job description.',
    bullets: ['Auto-generated interview questions', 'Identified skill gaps per candidate', 'Evaluation notes for hiring managers'],
  },
];

// ── Steps ──────────────────────────────────────────────────────────────────

const steps = [
  {
    num: '01',
    title: 'Post Your Job',
    desc: 'Create a campaign in minutes. Paste or write your job description and the AI automatically extracts scoring criteria — no manual configuration needed.',
    color: 'text-violet-600',
    bg: 'bg-violet-100',
  },
  {
    num: '02',
    title: 'Candidates Apply via Email',
    desc: 'Share the dedicated email address with candidates. Every CV that arrives is instantly picked up, parsed, and sent through the analysis pipeline.',
    color: 'text-blue-600',
    bg: 'bg-blue-100',
  },
  {
    num: '03',
    title: 'Review AI-Scored Shortlists',
    desc: 'Open your dashboard to ranked, scored, and decision-ready candidates. Dive into any application for a full breakdown, then schedule interviews armed with AI-generated questions.',
    color: 'text-emerald-600',
    bg: 'bg-emerald-100',
  },
];

// ── Pricing ────────────────────────────────────────────────────────────────

const plans = [
  {
    name: 'Starter',
    price: '49',
    period: '/mo',
    desc: 'For small teams starting their AI hiring journey.',
    highlight: false,
    features: [
      'Up to 3 active job campaigns',
      '150 CVs analyzed / month',
      'Platform email ingestion',
      '7-dimension scoring',
      'Email support',
    ],
    cta: 'Start Free Trial',
  },
  {
    name: 'Professional',
    price: '149',
    period: '/mo',
    desc: 'For growing teams with higher hiring volume.',
    highlight: true,
    badge: 'Most Popular',
    features: [
      'Unlimited active campaigns',
      '1,000 CVs analyzed / month',
      'Platform email + forwarding',
      'Custom scoring weights',
      'Interview intelligence reports',
      'Priority support',
    ],
    cta: 'Get Started',
  },
  {
    name: 'Enterprise',
    price: '399',
    period: '/mo',
    desc: 'For agencies and multi-unit organizations.',
    highlight: false,
    features: [
      'Everything in Professional',
      'Unlimited CVs',
      'Multi-tenant management',
      'Super-admin console',
      'Dedicated onboarding',
      'SLA & custom integrations',
    ],
    cta: 'Contact Sales',
  },
];

// ── Testimonials ───────────────────────────────────────────────────────────

const testimonials = [
  {
    quote: "We cut our shortlisting time from 3 days to 4 hours. The AI scores align almost perfectly with what our senior recruiters would decide — it's become indispensable.",
    name: "Sarah Al-Mansouri",
    role: "Head of Talent, TechCorp MENA",
    initials: "SA",
    color: "bg-violet-500",
  },
  {
    quote: "The email ingestion is brilliant. Candidates just email their CVs like they always did — nothing changes on their end, but on ours everything is instantly organized and scored.",
    name: "James Okonkwo",
    role: "Recruitment Manager, BuildRight Group",
    initials: "JO",
    color: "bg-blue-500",
  },
  {
    quote: "Running 12 clients through one platform, each totally isolated. The multi-tenant setup saved us from building our own infrastructure. Worth every penny.",
    name: "Priya Nair",
    role: "Director, HireFlow Agency",
    initials: "PN",
    color: "bg-emerald-500",
  },
];

// ── FAQ ────────────────────────────────────────────────────────────────────

const faqs = [
  {
    q: "How does CV ingestion actually work?",
    a: "You get a dedicated email address per job posting (or use email forwarding from your existing HR inbox). When a candidate emails their CV, our system automatically picks it up, extracts the document, and runs it through the AI analysis pipeline — no manual steps required.",
  },
  {
    q: "What file formats are supported?",
    a: "We support PDF, DOC, DOCX, and plain-text CVs sent as email attachments. The AI handles formatting variations gracefully, including tables, multi-column layouts, and non-standard structures.",
  },
  {
    q: "Can I customize how candidates are scored?",
    a: "Yes. Each job automatically generates scoring weights based on its description, but you can adjust the weight distribution across all 7 dimensions (Skills, Experience, Education, Certifications, Soft Skills, Domain Knowledge, Other Requirements) before or after analysis.",
  },
  {
    q: "Is there a free trial?",
    a: "Yes — the Starter plan includes a 14-day free trial with no credit card required. You get access to 3 campaigns and 50 CV analyses to validate the platform fits your workflow.",
  },
  {
    q: "How is data kept separate between tenants?",
    a: "Every tenant is fully isolated at the data level. Jobs, candidates, and analysis results are scoped to a tenant ID embedded in the authentication token. No cross-tenant data access is possible.",
  },
];

// ── Main Component ─────────────────────────────────────────────────────────

export const LandingPage: React.FC<LandingPageProps> = ({ onGetStarted, onSignIn }) => {
  const [openFaq, setOpenFaq] = useState<number | null>(null);
  const [scrolled, setScrolled] = useState(false);
  const statsRef = useInView(0.3);

  const cvCount = useCounter(50000, 2000, statsRef.inView);
  const timeCount = useCounter(85, 1600, statsRef.inView);
  const clientCount = useCounter(320, 1800, statsRef.inView);
  const accuracyCount = useCounter(94, 1600, statsRef.inView);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 30);
    window.addEventListener('scroll', onScroll);
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  const scrollTo = (id: string) => {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' });
  };

  return (
    <div className="min-h-screen bg-white" style={{ fontFamily: "'Inter', sans-serif" }}>

      {/* ── Navbar ── */}
      <nav
        className={`fixed top-0 left-0 right-0 z-50 transition-all duration-300 ${
          scrolled ? 'bg-white/95 backdrop-blur shadow-sm border-b border-gray-100' : 'bg-transparent'
        }`}
      >
        <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg flex items-center justify-center" style={{ background: 'linear-gradient(135deg, #6366f1, #2563eb)' }}>
              <svg viewBox="0 0 24 24" fill="white" className="w-5 h-5">
                <path d="M9 12h6m-6 4h6m2 5H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5.586a1 1 0 0 1 .707.293l5.414 5.414a1 1 0 0 1 .293.707V19a2 2 0 0 1-2 2z"/>
              </svg>
            </div>
            <span className="font-bold text-gray-900 text-lg tracking-tight">CV Analyzer</span>
          </div>

          <div className="hidden md:flex items-center gap-8">
            {['features', 'how-it-works', 'pricing', 'faq'].map(id => (
              <button
                key={id}
                onClick={() => scrollTo(id)}
                className="text-sm font-medium text-gray-600 hover:text-gray-900 capitalize transition-colors"
              >
                {id.replace('-', ' ')}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={onSignIn}
              className="text-sm font-semibold text-gray-700 hover:text-gray-900 transition-colors px-4 py-2"
            >
              Sign In
            </button>
            <button
              onClick={onGetStarted}
              className="text-sm font-semibold text-white px-5 py-2 rounded-xl transition-all hover:opacity-90 hover:shadow-lg"
              style={{ background: 'linear-gradient(135deg, #6366f1, #2563eb)' }}
            >
              Get Started Free
            </button>
          </div>
        </div>
      </nav>

      {/* ── Hero ── */}
      <section
        className="relative pt-32 pb-24 px-6 overflow-hidden"
        style={{
          background: 'linear-gradient(135deg, #0f0c29 0%, #1a1245 30%, #24243e 60%, #0f2027 100%)',
        }}
      >
        {/* Decorative blobs */}
        <div className="absolute top-20 left-1/4 w-96 h-96 rounded-full opacity-20 blur-3xl pointer-events-none"
          style={{ background: 'radial-gradient(circle, #6366f1, transparent)' }} />
        <div className="absolute bottom-0 right-1/4 w-80 h-80 rounded-full opacity-15 blur-3xl pointer-events-none"
          style={{ background: 'radial-gradient(circle, #2563eb, transparent)' }} />
        <div className="absolute top-40 right-10 w-60 h-60 rounded-full opacity-10 blur-2xl pointer-events-none"
          style={{ background: 'radial-gradient(circle, #a855f7, transparent)' }} />

        <div className="relative max-w-5xl mx-auto text-center">
          <div className="inline-flex items-center gap-2 bg-white/10 backdrop-blur border border-white/20 rounded-full px-4 py-2 mb-8">
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
            <span className="text-white/80 text-xs font-medium tracking-wide uppercase">AI-Powered Hiring Platform</span>
          </div>

          <h1 className="text-5xl md:text-7xl font-extrabold text-white leading-tight mb-6 tracking-tight">
            Hire Smarter.<br />
            <span style={{ backgroundImage: 'linear-gradient(90deg, #a78bfa, #60a5fa, #34d399)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent', backgroundClip: 'text' }}>
              10× Faster.
            </span>
          </h1>

          <p className="text-lg md:text-xl text-white/70 max-w-2xl mx-auto mb-10 leading-relaxed">
            Stop reading CVs manually. Our AI analyzes, scores, and ranks every applicant
            in seconds — so your team focuses on interviewing the best, not sorting the pile.
          </p>

          <div className="flex flex-col sm:flex-row items-center justify-center gap-4 mb-16">
            <button
              onClick={onGetStarted}
              className="group flex items-center gap-2 text-white font-bold px-8 py-4 rounded-2xl text-base transition-all hover:scale-105 hover:shadow-2xl"
              style={{ background: 'linear-gradient(135deg, #6366f1, #2563eb)', boxShadow: '0 8px 32px rgba(99,102,241,0.4)' }}
            >
              Start Free — No Card Needed
              <span className="transition-transform group-hover:translate-x-1"><ArrowRightIcon /></span>
            </button>
            <button
              onClick={() => scrollTo('how-it-works')}
              className="flex items-center gap-2 text-white/80 hover:text-white font-semibold px-8 py-4 rounded-2xl border border-white/20 hover:border-white/40 transition-all text-base"
            >
              See How It Works
            </button>
          </div>

          {/* Hero UI mockup */}
          <div className="relative max-w-4xl mx-auto">
            <div className="rounded-2xl border border-white/10 overflow-hidden shadow-2xl"
              style={{ background: 'rgba(255,255,255,0.05)', backdropFilter: 'blur(20px)' }}>
              {/* Window chrome */}
              <div className="flex items-center gap-2 px-4 py-3 border-b border-white/10" style={{ background: 'rgba(255,255,255,0.04)' }}>
                <div className="w-3 h-3 rounded-full bg-red-400/70" />
                <div className="w-3 h-3 rounded-full bg-amber-400/70" />
                <div className="w-3 h-3 rounded-full bg-emerald-400/70" />
                <span className="ml-3 text-white/30 text-xs font-mono">app.cvanalyzer.ai — Jobs Dashboard</span>
              </div>
              {/* Mock dashboard */}
              <div className="p-6 grid grid-cols-3 gap-4">
                {[
                  { label: 'Total Applications', val: '1,247', color: '#6366f1', pct: 78 },
                  { label: 'Qualified Candidates', val: '312', color: '#22c55e', pct: 45 },
                  { label: 'Avg. Score', val: '74%', color: '#3b82f6', pct: 74 },
                ].map(stat => (
                  <div key={stat.label} className="rounded-xl p-4 text-left" style={{ background: 'rgba(255,255,255,0.06)' }}>
                    <div className="text-white/50 text-xs mb-1">{stat.label}</div>
                    <div className="text-white text-2xl font-bold mb-2">{stat.val}</div>
                    <div className="h-1.5 rounded-full bg-white/10">
                      <div className="h-full rounded-full transition-all" style={{ width: `${stat.pct}%`, background: stat.color }} />
                    </div>
                  </div>
                ))}
                <div className="col-span-3 rounded-xl overflow-hidden" style={{ background: 'rgba(255,255,255,0.06)' }}>
                  <div className="px-4 py-3 border-b border-white/10 flex items-center justify-between">
                    <span className="text-white/70 text-sm font-semibold">Recent Applications</span>
                    <span className="text-xs text-white/40">Live</span>
                  </div>
                  <div className="divide-y divide-white/5">
                    {[
                      { name: 'Ahmed Al-Rashidi', role: 'Senior React Developer', score: 91, status: 'qualified' },
                      { name: 'Maria Santos', role: 'Full Stack Engineer', score: 78, status: 'qualified' },
                      { name: 'Liam O\'Brien', role: 'Frontend Developer', score: 52, status: 'partial' },
                    ].map(app => (
                      <div key={app.name} className="flex items-center gap-4 px-4 py-3">
                        <div className="w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold text-white flex-shrink-0"
                          style={{ background: 'linear-gradient(135deg, #6366f1, #2563eb)' }}>
                          {app.name.split(' ').map(n => n[0]).join('')}
                        </div>
                        <div className="flex-1 text-left min-w-0">
                          <div className="text-white/90 text-sm font-medium truncate">{app.name}</div>
                          <div className="text-white/40 text-xs truncate">{app.role}</div>
                        </div>
                        <div className="text-right flex-shrink-0">
                          <div className="text-white font-bold text-sm">{app.score}%</div>
                          <div className={`text-xs font-medium ${app.status === 'qualified' ? 'text-emerald-400' : 'text-amber-400'}`}>
                            {app.status}
                          </div>
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
          {[
            { val: cvCount.toLocaleString() + '+', label: 'CVs Analyzed', suffix: '' },
            { val: timeCount + '%', label: 'Faster Shortlisting', suffix: '' },
            { val: clientCount + '+', label: 'Companies Trust Us', suffix: '' },
            { val: accuracyCount + '%', label: 'Scoring Accuracy', suffix: '' },
          ].map(s => (
            <div key={s.label}>
              <div className="text-4xl md:text-5xl font-extrabold text-gray-900 tabular-nums">{s.val}</div>
              <div className="text-sm text-gray-500 mt-1 font-medium">{s.label}</div>
            </div>
          ))}
        </div>
      </section>

      {/* ── Features ── */}
      <section id="features" className="py-24 px-6" style={{ background: '#fafafa' }}>
        <div className="max-w-7xl mx-auto">
          <div className="text-center mb-16">
            <span className="inline-block text-xs font-bold uppercase tracking-widest text-indigo-600 mb-3">Platform Features</span>
            <h2 className="text-4xl md:text-5xl font-extrabold text-gray-900 mb-4 tracking-tight">
              Everything your hiring team needs
            </h2>
            <p className="text-lg text-gray-500 max-w-2xl mx-auto">
              From the moment a CV lands in an inbox to the moment you schedule an interview — fully automated and AI-driven.
            </p>
          </div>

          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
            {features.map((f) => (
              <div
                key={f.title}
                className={`group rounded-2xl border p-6 hover:shadow-xl transition-all duration-300 hover:-translate-y-1 bg-white ${f.border}`}
              >
                <div className={`w-12 h-12 rounded-xl ${f.bg} flex items-center justify-center mb-4`}>
                  <div className={`bg-gradient-to-br ${f.color} bg-clip-text`} style={{ color: 'transparent', backgroundImage: `linear-gradient(135deg, var(--tw-gradient-stops))` }}>
                    <div className={`text-gradient`} style={{ background: `linear-gradient(135deg, ${f.color.includes('violet') ? '#7c3aed, #6d28d9' : f.color.includes('blue') ? '#2563eb, #0891b2' : f.color.includes('emerald') ? '#059669, #0d9488' : f.color.includes('orange') ? '#ea580c, #d97706' : f.color.includes('rose') ? '#e11d48, #db2777' : '#4f46e5, #2563eb'})`, WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent', backgroundClip: 'text' }}>
                      {f.icon}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-[10px] font-black uppercase tracking-widest text-gray-400">{f.tag}</span>
                </div>
                <h3 className="text-lg font-bold text-gray-900 mb-2">{f.title}</h3>
                <p className="text-gray-500 text-sm leading-relaxed mb-4">{f.description}</p>
                <ul className="space-y-1.5">
                  {f.bullets.map(b => (
                    <li key={b} className="flex items-start gap-2 text-sm text-gray-600">
                      <span className="text-emerald-500 mt-0.5 flex-shrink-0"><CheckIcon /></span>
                      {b}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── How It Works ── */}
      <section id="how-it-works" className="py-24 px-6 bg-white">
        <div className="max-w-5xl mx-auto">
          <div className="text-center mb-16">
            <span className="inline-block text-xs font-bold uppercase tracking-widest text-indigo-600 mb-3">Simple Process</span>
            <h2 className="text-4xl md:text-5xl font-extrabold text-gray-900 mb-4 tracking-tight">
              Live in 3 steps
            </h2>
            <p className="text-lg text-gray-500 max-w-xl mx-auto">
              No complex integrations, no training your team on new tools. You're analyzing CVs the same day you sign up.
            </p>
          </div>

          <div className="relative">
            {/* Connector line */}
            <div className="absolute left-1/2 top-12 bottom-12 w-px bg-gradient-to-b from-violet-200 via-blue-200 to-emerald-200 hidden md:block -translate-x-1/2" />

            <div className="space-y-12">
              {steps.map((step, i) => (
                <div key={step.num} className={`flex flex-col ${i % 2 === 1 ? 'md:flex-row-reverse' : 'md:flex-row'} items-center gap-8 md:gap-12`}>
                  <div className="flex-1 text-center md:text-left">
                    <div className={`inline-flex items-center justify-center w-14 h-14 rounded-2xl ${step.bg} mb-4`}>
                      <span className={`text-2xl font-extrabold ${step.color}`}>{step.num}</span>
                    </div>
                    <h3 className="text-2xl font-bold text-gray-900 mb-3">{step.title}</h3>
                    <p className="text-gray-500 leading-relaxed max-w-md">{step.desc}</p>
                  </div>
                  <div className="flex-1">
                    <div className="rounded-2xl border border-gray-100 p-6 bg-gray-50 shadow-sm">
                      {i === 0 && (
                        <div className="space-y-3">
                          <div className="h-8 bg-indigo-100 rounded-lg w-3/4" />
                          <div className="h-4 bg-gray-200 rounded w-full" />
                          <div className="h-4 bg-gray-200 rounded w-5/6" />
                          <div className="h-4 bg-gray-200 rounded w-4/6" />
                          <div className="mt-4 flex items-center gap-2">
                            <div className="h-8 bg-indigo-500 rounded-lg flex-1 flex items-center justify-center">
                              <span className="text-white text-xs font-semibold">Create Campaign</span>
                            </div>
                          </div>
                        </div>
                      )}
                      {i === 1 && (
                        <div className="space-y-2">
                          {[
                            { from: 'ahmed@gmail.com', sub: 'Application – Senior Dev', time: '09:14' },
                            { from: 'maria.s@outlook.com', sub: 'Re: Job Posting CV attached', time: '09:31' },
                            { from: 'liam.o@yahoo.com', sub: 'CV for Frontend Role', time: '10:02' },
                          ].map(m => (
                            <div key={m.from} className="flex items-start gap-3 p-3 bg-white rounded-xl border border-gray-100">
                              <div className="w-7 h-7 rounded-full bg-blue-100 flex items-center justify-center text-xs font-bold text-blue-600 flex-shrink-0">
                                {m.from[0].toUpperCase()}
                              </div>
                              <div className="flex-1 min-w-0">
                                <div className="text-xs text-gray-500 truncate">{m.from}</div>
                                <div className="text-xs font-semibold text-gray-800 truncate">{m.sub}</div>
                              </div>
                              <div className="text-xs text-gray-400 flex-shrink-0">{m.time}</div>
                            </div>
                          ))}
                          <div className="text-center text-xs text-emerald-600 font-semibold pt-1">→ Auto-captured & queued</div>
                        </div>
                      )}
                      {i === 2 && (
                        <div className="space-y-2">
                          {[
                            { name: 'Ahmed Al-Rashidi', score: 91, status: 'Qualified', color: 'bg-emerald-100 text-emerald-700' },
                            { name: 'Maria Santos', score: 78, status: 'Qualified', color: 'bg-emerald-100 text-emerald-700' },
                            { name: 'Liam O\'Brien', score: 52, status: 'Partial', color: 'bg-amber-100 text-amber-700' },
                          ].map(c => (
                            <div key={c.name} className="flex items-center gap-3 p-3 bg-white rounded-xl border border-gray-100">
                              <div className="flex-1 min-w-0">
                                <div className="text-xs font-semibold text-gray-800">{c.name}</div>
                                <div className="h-1.5 bg-gray-100 rounded-full mt-1 w-24">
                                  <div className="h-full bg-indigo-500 rounded-full" style={{ width: `${c.score}%` }} />
                                </div>
                              </div>
                              <div className="text-sm font-bold text-gray-900">{c.score}%</div>
                              <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${c.color}`}>{c.status}</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* ── Testimonials ── */}
      <section className="py-24 px-6" style={{ background: 'linear-gradient(135deg, #f5f3ff, #eff6ff)' }}>
        <div className="max-w-6xl mx-auto">
          <div className="text-center mb-14">
            <span className="inline-block text-xs font-bold uppercase tracking-widest text-indigo-600 mb-3">Social Proof</span>
            <h2 className="text-4xl md:text-5xl font-extrabold text-gray-900 tracking-tight">Trusted by hiring teams</h2>
          </div>
          <div className="grid md:grid-cols-3 gap-6">
            {testimonials.map(t => (
              <div key={t.name} className="bg-white rounded-2xl p-6 shadow-sm border border-white hover:shadow-lg transition-shadow">
                <div className="flex gap-0.5 mb-4">
                  {[...Array(5)].map((_, i) => <StarIcon key={i} />)}
                </div>
                <p className="text-gray-700 leading-relaxed mb-6 text-sm">"{t.quote}"</p>
                <div className="flex items-center gap-3">
                  <div className={`w-10 h-10 rounded-full ${t.color} flex items-center justify-center text-white text-sm font-bold flex-shrink-0`}>
                    {t.initials}
                  </div>
                  <div>
                    <div className="font-semibold text-gray-900 text-sm">{t.name}</div>
                    <div className="text-gray-500 text-xs">{t.role}</div>
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
            <span className="inline-block text-xs font-bold uppercase tracking-widest text-indigo-600 mb-3">Pricing</span>
            <h2 className="text-4xl md:text-5xl font-extrabold text-gray-900 mb-4 tracking-tight">Simple, transparent pricing</h2>
            <p className="text-gray-500 text-lg">All plans include a 14-day free trial. No credit card required.</p>
          </div>

          <div className="grid md:grid-cols-3 gap-6 items-start">
            {plans.map(plan => (
              <div
                key={plan.name}
                className={`relative rounded-2xl p-8 border transition-all ${
                  plan.highlight
                    ? 'border-indigo-500 shadow-2xl shadow-indigo-100 scale-105'
                    : 'border-gray-200 hover:border-gray-300 hover:shadow-md'
                }`}
                style={plan.highlight ? { background: 'linear-gradient(135deg, #6366f1 0%, #4f46e5 100%)' } : { background: '#fff' }}
              >
                {plan.badge && (
                  <div className="absolute -top-3.5 left-1/2 -translate-x-1/2 bg-amber-400 text-amber-900 text-xs font-black uppercase tracking-widest px-4 py-1 rounded-full">
                    {plan.badge}
                  </div>
                )}
                <div className={`text-sm font-bold uppercase tracking-widest mb-1 ${plan.highlight ? 'text-indigo-200' : 'text-gray-500'}`}>
                  {plan.name}
                </div>
                <div className="flex items-end gap-1 mb-1">
                  <span className={`text-5xl font-extrabold ${plan.highlight ? 'text-white' : 'text-gray-900'}`}>${plan.price}</span>
                  <span className={`text-sm mb-2 ${plan.highlight ? 'text-indigo-200' : 'text-gray-400'}`}>{plan.period}</span>
                </div>
                <p className={`text-sm mb-6 ${plan.highlight ? 'text-indigo-200' : 'text-gray-500'}`}>{plan.desc}</p>
                <button
                  onClick={onGetStarted}
                  className={`w-full py-3 rounded-xl font-bold text-sm transition-all hover:opacity-90 mb-6 ${
                    plan.highlight
                      ? 'bg-white text-indigo-700 hover:shadow-lg'
                      : 'text-white hover:shadow-lg'
                  }`}
                  style={plan.highlight ? {} : { background: 'linear-gradient(135deg, #6366f1, #2563eb)' }}
                >
                  {plan.cta}
                </button>
                <ul className="space-y-3">
                  {plan.features.map(f => (
                    <li key={f} className={`flex items-start gap-2.5 text-sm ${plan.highlight ? 'text-indigo-100' : 'text-gray-600'}`}>
                      <span className={`flex-shrink-0 mt-0.5 ${plan.highlight ? 'text-emerald-300' : 'text-emerald-500'}`}><CheckIcon /></span>
                      {f}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── FAQ ── */}
      <section id="faq" className="py-24 px-6" style={{ background: '#fafafa' }}>
        <div className="max-w-3xl mx-auto">
          <div className="text-center mb-14">
            <span className="inline-block text-xs font-bold uppercase tracking-widest text-indigo-600 mb-3">FAQ</span>
            <h2 className="text-4xl font-extrabold text-gray-900 tracking-tight">Common questions</h2>
          </div>
          <div className="space-y-3">
            {faqs.map((faq, i) => (
              <div key={i} className="bg-white rounded-2xl border border-gray-100 overflow-hidden">
                <button
                  onClick={() => setOpenFaq(openFaq === i ? null : i)}
                  className="w-full flex items-center justify-between px-6 py-5 text-left"
                >
                  <span className="font-semibold text-gray-900 text-sm">{faq.q}</span>
                  <span className={`text-gray-400 transition-transform flex-shrink-0 ml-4 ${openFaq === i ? 'rotate-45' : ''}`}>
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="w-5 h-5">
                      <path d="M12 5v14M5 12h14"/>
                    </svg>
                  </span>
                </button>
                {openFaq === i && (
                  <div className="px-6 pb-5 text-sm text-gray-500 leading-relaxed border-t border-gray-50 pt-4">
                    {faq.a}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── CTA Banner ── */}
      <section className="py-24 px-6" style={{ background: 'linear-gradient(135deg, #0f0c29, #1a1245, #24243e)' }}>
        <div className="max-w-4xl mx-auto text-center">
          <h2 className="text-4xl md:text-5xl font-extrabold text-white mb-4 tracking-tight">
            Ready to transform your hiring?
          </h2>
          <p className="text-white/60 text-lg mb-10 max-w-xl mx-auto">
            Join 320+ companies using AI to find better candidates, faster. Start your free trial today.
          </p>
          <button
            onClick={onGetStarted}
            className="group inline-flex items-center gap-2 text-white font-bold px-10 py-4 rounded-2xl text-base transition-all hover:scale-105 hover:shadow-2xl"
            style={{ background: 'linear-gradient(135deg, #6366f1, #2563eb)', boxShadow: '0 8px 40px rgba(99,102,241,0.4)' }}
          >
            Start Free Trial
            <span className="transition-transform group-hover:translate-x-1"><ArrowRightIcon /></span>
          </button>
          <p className="text-white/30 text-xs mt-4">No credit card required · 14-day free trial · Cancel anytime</p>
        </div>
      </section>

      {/* ── Footer ── */}
      <footer className="bg-gray-950 py-12 px-6">
        <div className="max-w-7xl mx-auto">
          <div className="flex flex-col md:flex-row items-center justify-between gap-6">
            <div className="flex items-center gap-2">
              <div className="w-7 h-7 rounded-lg flex items-center justify-center" style={{ background: 'linear-gradient(135deg, #6366f1, #2563eb)' }}>
                <svg viewBox="0 0 24 24" fill="white" className="w-4 h-4">
                  <path d="M9 12h6m-6 4h6m2 5H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5.586a1 1 0 0 1 .707.293l5.414 5.414a1 1 0 0 1 .293.707V19a2 2 0 0 1-2 2z"/>
                </svg>
              </div>
              <span className="text-white font-bold">CV Analyzer</span>
            </div>
            <div className="flex gap-6 text-sm text-gray-500">
              {['Privacy Policy', 'Terms of Service', 'Contact'].map(link => (
                <a key={link} href="#" className="hover:text-gray-300 transition-colors">{link}</a>
              ))}
            </div>
            <div className="text-gray-600 text-sm">© 2026 CV Analyzer. All rights reserved.</div>
          </div>
        </div>
      </footer>
    </div>
  );
};
