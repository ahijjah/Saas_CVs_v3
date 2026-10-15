// The one place that turns a validation finding into words. The codes are the BACKEND's (services/requirements_v2/validation.py); the params are
// the numbers the backend attaches to them, and the local validator (utils/requirementsV2.ts validateDraft) attaches the same ones, so both
// sources read the same. Nothing here decides whether something is wrong: it only says what is wrong, with the actual and expected values.
import { fmt, Strings } from './i18n';

export type IssueParams = Record<string, string | number | null | undefined>;

export interface IssueView {
  code: string;
  message?: string;
  category?: string | null;
  itemName?: string | null;        // the wording of the item, when the finding names one
  params?: IssueParams | null;
}

export interface IssueContext {
  s: Strings;
  categoryName(c: string): string;
}

const signed = (n: number) => (n > 0 ? `+${n}` : `${n}`);
const weightText = (s: Strings, w: unknown) => (typeof w === 'number' ? String(w) : s.valueMissing);

// The numbers each message needs. A finding that arrives without them (an older server, or a bare message) is shown with the server's own words.
const NEEDS: Record<string, string[]> = {
  required_weights_total: ['total', 'difference'],
  category_weights_total: ['total', 'difference'],
  required_weight_invalid: ['weight'],
  category_weight_not_positive: ['weight'],
  category_weight_without_required_items: ['weight'],
  too_many_required_items: ['n', 'max'],
};

export function issueText(issue: IssueView, ctx: IssueContext): string {
  const { s } = ctx;
  const p = issue.params ?? {};
  const complete = (NEEDS[issue.code] ?? []).every(k => k in p);
  if (!complete && issue.message && issue.code in NEEDS)
    return fmt(s.issue_server_other, { code: issue.code, message: issue.message });
  const category = issue.category ? ctx.categoryName(issue.category) : '';
  const item = issue.itemName?.trim() || s.itemText;
  const num = (v: unknown) => (typeof v === 'number' ? v : Number(v ?? 0));
  const vars = { category, item, total: num(p.total), difference: signed(num(p.difference)), n: num(p.n), max: num(p.max), weight: weightText(s, p.weight) };
  switch (issue.code) {
    case 'not_whole_number': return fmt(s.issue_not_whole_number, { field: String(p.field ?? ''), value: String(p.value ?? '') });
    case 'required_weights_total': return fmt(s.issue_required_weights_total, vars);
    case 'category_weights_total': return fmt(s.issue_category_weights_total, vars);
    case 'required_weight_invalid': return fmt(s.issue_required_weight_invalid, vars);
    case 'category_weight_not_positive': return fmt(s.issue_category_weight_not_positive, vars);
    case 'category_weight_without_required_items': return fmt(s.issue_category_weight_without_required_items, vars);
    case 'too_many_required_items': return fmt(s.issue_too_many_required_items, vars);
    case 'bad_category_weight': return fmt(s.issue_bad_category_weight, vars);
    case 'preferred_item_has_weight': return fmt(s.issue_preferred_item_has_weight, vars);
    case 'bad_weight_type': return fmt(s.issue_bad_weight_type, vars);
    case 'empty_text': return fmt(s.issue_empty_text, vars);
    case 'bad_alternatives': return fmt(s.issue_bad_alternatives, vars);
    case 'bad_experience': return fmt(s.issue_bad_experience, vars);
    default:
      return issue.message
        ? fmt(s.issue_server_other, { code: issue.code, message: issue.message })
        : fmt(s.issue_fallback, { message: issue.code });
  }
}
