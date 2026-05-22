
export interface JobDescriptionQualityResult {
  score: number;
  state: 'insufficient' | 'needs_improvement' | 'ready';
  issues: string[];
  suggestions: string[];
  signals: {
    word_count: number;
    sentence_count: number;
    has_responsibilities: boolean;
    has_requirements: boolean;
    has_job_context: boolean;
    has_placeholder_text: boolean;
    has_repetition: boolean;
    language_hint: 'en' | 'ar' | 'mixed' | 'unknown';
  };
}

// ── Placeholder / test text patterns ──────────────────────────────────────────
const PLACEHOLDER_ANCHORED = /^(test\d*|sample|demo|placeholder|temp|hello|hi|n\/a|na|tbd|todo|lorem|ipsum|required|job\s*description|اختبار|تجربة|مثال|نموذج|نص\s*تجريبي)\.?$/i;
const REPEAT_CHAR_RE = /(.)\1{3,}/; // 4+ identical consecutive chars

// ── English keyword patterns ───────────────────────────────────────────────────
const EN_RESPONSIBILITIES = /\b(responsibilities|duties|tasks?|manage[sd]?|manages|managing|develop[sed]?|maintain[sed]?|support[sed]?|assist[sed]?|coordinate[sd]?|lead[s]?|leading|ensure[sd]?|provides?|creat[eing]+|design[ings]+|implement[ings]+|analyz[eing]+|analys[eing]+|monitor[ings]+|review[ings]+|deliver[ings]+|build[ings]+|writ[eing]+|operat[eing]+|handle[sd]?|process[eing]+|prepar[eing]+|report[ings]+|overseeing?|execut[eing]+|perform[ings]+|conduct[ings]+|train[ings]+|respond[ings]+|maintain[ings]+)\b/i;

const EN_REQUIREMENTS = /\b(requirements?|skills?|qualifications?|experience|education|degree|bachelor|master|diploma|certificate|proficiency|knowledge|familiar(?:ity)?|fluent|fluency|expertise|background|ability|abilities|able\s+to|must[\s-]have|should[\s-]have|preferred|minimum|required|ideally)\b/i;

const EN_JOB_CONTEXT = /\b(position|role|job|candidate|applicant|team|department|company|organization|office|client|customer|products?|services?|projects?|industry|sector|firm|employer|vacancy|hire|hiring|recruit|workforce|retail|stores?|shops?|bank|hospital|school|factory|warehouse|hotel|restaurant|engineer(?:ing)?|developer|manager|analyst|designer|consultant|coordinator|specialist|administrator|director|officer|executive|accountant|nurse|teacher|driver|technician)\b/i;

const EN_OPEN_ROLE = /\b(open\s+to\s+all|no\s+(?:specific\s+)?(?:degree|experience|qualification)|all\s+backgrounds?|training\s+(?:will\s+be\s+)?(?:provided|given)|no\s+prior\s+experience|entry[\s-]level\s+welcome|fresh\s+graduates?\s+(?:are\s+)?welcome|suitable\s+for\s+all|anyone\s+can\s+apply)\b/i;

// ── Arabic keyword patterns ────────────────────────────────────────────────────
const AR_RESPONSIBILITIES = /(?:مسؤوليات|مهام|واجبات|إدارة|تطوير|صيانة|دعم|مساعدة|تنسيق|قيادة|تنفيذ|تصميم|مراجعة|تحليل|تقديم|إعداد|متابعة|تشغيل|بناء|ضمان|الإشراف|تحقيق|القيام\s+بـ?|معالجة|تدريب\s+الفريق)/;

const AR_REQUIREMENTS = /(?:متطلبات|مهارات|مؤهلات|خبرة|تعليم|شهادة|معرفة|يجب|مطلوب|إجادة|إلمام|كفاءة|خلفية|قدرة|حاصل\s+على|بكالوريوس|ماجستير|دبلوم|ثانوية|تقنية)/;

const AR_JOB_CONTEXT = /(?:منصب|دور|وظيفة|مرشح|فريق|قسم|شركة|مؤسسة|مكتب|عميل|خدمة|مشروع|صناعة|قطاع|توظيف|عمل|متجر|مستودع|مستشفى|مدرسة|بنك|فندق|مصنع)/;

const AR_OPEN_ROLE = /(?:مفتوح\s+لجميع\s+الخلفيات|لا\s+يشترط\s+خبرة|سيتم\s+توفير\s+التدريب|لا\s+تشترط\s+شهادة|التدريب\s+سيتم|جميع\s+الخلفيات|مناسب\s+للجميع)/;

// ── Helpers ────────────────────────────────────────────────────────────────────

function detectLanguage(text: string): 'en' | 'ar' | 'mixed' | 'unknown' {
  const ar = (text.match(/[؀-ۿ]/g) ?? []).length;
  const en = (text.match(/[a-zA-Z]/g) ?? []).length;
  if (ar === 0 && en === 0) return 'unknown';
  if (ar > 0 && en === 0) return 'ar';
  if (en > 0 && ar === 0) return 'en';
  return 'mixed';
}

function countWords(text: string): number {
  return text.trim().split(/\s+/).filter(w => w.length > 0).length;
}

function countSentences(text: string): number {
  return text.split(/[.!?؟\n]+/).filter(s => s.trim().length > 3).length;
}

function checkRepetition(text: string): boolean {
  if (REPEAT_CHAR_RE.test(text)) return true;
  const words = text.toLowerCase().split(/\s+/).filter(w => w.length > 3);
  if (words.length < 5) return false;
  const freq: Record<string, number> = {};
  for (const w of words) freq[w] = (freq[w] ?? 0) + 1;
  const maxFreq = Math.max(...Object.values(freq));
  return maxFreq / words.length > 0.35;
}

function checkPlaceholder(text: string): boolean {
  const norm = text.trim().toLowerCase();
  if (PLACEHOLDER_ANCHORED.test(norm)) return true;
  // All words are the same (e.g. "aaaa aaaa aaaa")
  const words = norm.split(/\s+/).filter(Boolean);
  if (words.length > 0 && words.every(w => w === words[0])) return true;
  return false;
}

// ── Main export ────────────────────────────────────────────────────────────────

export function evaluateJobDescriptionQuality(description: string): JobDescriptionQualityResult {
  const text = (description ?? '').trim();
  const issues: string[] = [];
  const suggestions: string[] = [];

  if (!text) {
    return {
      score: 0,
      state: 'insufficient',
      issues: ['Job description is empty.'],
      suggestions: ['Add responsibilities, required skills, qualifications, and experience expectations.'],
      signals: { word_count: 0, sentence_count: 0, has_responsibilities: false, has_requirements: false, has_job_context: false, has_placeholder_text: false, has_repetition: false, language_hint: 'unknown' },
    };
  }

  const wordCount      = countWords(text);
  const sentenceCount  = countSentences(text);
  const langHint       = detectLanguage(text);
  const hasPlaceholder = checkPlaceholder(text);
  const hasRepetition  = checkRepetition(text);

  if (hasPlaceholder) {
    return {
      score: 0,
      state: 'insufficient',
      issues: ['The description appears to be placeholder or test text.'],
      suggestions: ['Replace with a real job description including responsibilities, skills, and qualifications.'],
      signals: { word_count: wordCount, sentence_count: sentenceCount, has_responsibilities: false, has_requirements: false, has_job_context: false, has_placeholder_text: true, has_repetition: hasRepetition, language_hint: langHint },
    };
  }

  const hasResponsibilities = EN_RESPONSIBILITIES.test(text) || AR_RESPONSIBILITIES.test(text);
  const hasRequirements     = EN_REQUIREMENTS.test(text)     || AR_REQUIREMENTS.test(text);
  const hasJobContext       = EN_JOB_CONTEXT.test(text)      || AR_JOB_CONTEXT.test(text);
  const hasOpenRole         = EN_OPEN_ROLE.test(text)        || AR_OPEN_ROLE.test(text);

  let score = 0;

  // Word count (max 50 pts)
  if (wordCount >= 120)     score += 50;
  else if (wordCount >= 61) score += 40;
  else if (wordCount >= 31) score += 25;
  else if (wordCount >= 11) score += 15;

  // Sentence structure (max 10 pts)
  if (sentenceCount >= 3)     score += 10;
  else if (sentenceCount >= 1) score += 5;

  // Content signals
  if (hasResponsibilities) score += 20;  // +20
  if (hasRequirements)     score += 20;  // +20
  if (hasJobContext)       score += 10;  // +10
  if (hasOpenRole)         score += 25;  // +25 (open-role descriptions are valid even if short)

  // Penalty
  if (hasRepetition) score -= 25;

  score = Math.max(0, Math.min(100, score));

  // ── Issues ──
  if (wordCount < 20)                              issues.push('Description is too short.');
  if (hasRepetition)                               issues.push('The text contains excessive repetition.');
  if (!hasResponsibilities && !hasOpenRole)        issues.push('No responsibilities or duties mentioned.');
  if (!hasRequirements && !hasOpenRole)            issues.push('No skills, qualifications, or experience requirements mentioned.');
  if (!hasJobContext)                              issues.push('No clear job context detected (role, team, industry, etc.).');

  // ── Suggestions ──
  if (wordCount < 50)                             suggestions.push('Expand to at least 50 words for better AI analysis.');
  if (!hasResponsibilities && !hasOpenRole)       suggestions.push('Add key responsibilities and daily duties.');
  if (!hasRequirements && !hasOpenRole)           suggestions.push('Add required skills, qualifications, or experience level.');
  if (!hasJobContext)                             suggestions.push('Mention the team, department, industry, or company context.');
  if (hasOpenRole && !hasResponsibilities)        suggestions.push('Consider adding what tasks or training the role involves.');
  if (issues.length === 0)                        suggestions.push('Description looks good and is ready for AI analysis.');

  const state: JobDescriptionQualityResult['state'] =
    score < 40 ? 'insufficient' : score < 70 ? 'needs_improvement' : 'ready';

  return {
    score,
    state,
    issues,
    suggestions,
    signals: {
      word_count: wordCount,
      sentence_count: sentenceCount,
      has_responsibilities: hasResponsibilities,
      has_requirements: hasRequirements,
      has_job_context: hasJobContext,
      has_placeholder_text: hasPlaceholder,
      has_repetition: hasRepetition,
      language_hint: langHint,
    },
  };
}

// ── Job title validation ───────────────────────────────────────────────────────

const TITLE_PLACEHOLDER = /^(test\d*|sample|demo|placeholder|temp|hello|hi|n\/a|na|tbd|todo|job|new\s+job|title|position|vacancy|role|post|opening|اختبار|تجربة|مثال|نموذج|وظيفة\s*جديدة|منصب|دور)\.?$/i;
const TITLE_SYMBOLS_ONLY = /^[\d\s\W]+$/;

const TITLE_BAD_MESSAGE = 'Please enter a real job title, such as Sales Assistant, Accountant, Driver, or HR Manager.';
const TITLE_BAD_MESSAGE_AR = 'يرجى إدخال مسمى وظيفي حقيقي، مثل: مساعد مبيعات، محاسب، سائق، أو مدير موارد بشرية.';

export interface TitleValidationResult {
  valid: boolean;
  message?: string;
}

export function validateJobTitle(title: string, lang: 'en' | 'ar' = 'en'): TitleValidationResult {
  const text = (title ?? '').trim();
  const badMsg = lang === 'ar' ? TITLE_BAD_MESSAGE_AR : TITLE_BAD_MESSAGE;

  if (!text) return { valid: false, message: lang === 'ar' ? 'المسمى الوظيفي مطلوب.' : 'Job title is required.' };
  if (text.length < 2) return { valid: false, message: badMsg };
  if (TITLE_PLACEHOLDER.test(text)) return { valid: false, message: badMsg };
  if (TITLE_SYMBOLS_ONLY.test(text)) return { valid: false, message: badMsg };

  const words = text.split(/\s+/).filter(Boolean);
  if (words.length > 0 && words.every(w => w.toLowerCase() === words[0].toLowerCase())) {
    return { valid: false, message: badMsg };
  }

  return { valid: true };
}
