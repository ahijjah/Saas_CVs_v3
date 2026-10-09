// SYNTHETIC fixtures shaped like the backend responses. No live jobs, no network.
import type { RequirementsView } from '../../services/requirementsV2Api';
import { CATEGORIES, ServerDocument, ServerItem } from '../../utils/requirementsV2';

export const item = (id: string, text: string, importance: 'required' | 'preferred', weight: number | null, extra: Partial<ServerItem> = {}): ServerItem => ({
  id, text, importance, weight, origin: 'stated', source_text: text, alternatives: null, experience: null, ...extra,
});

export const doc = (cats: Partial<Record<string, { weight: number; items: ServerItem[] }>>): ServerDocument => ({
  schema_version: 2,
  categories: Object.fromEntries(CATEGORIES.map(c => [c, cats[c] ?? { weight: 0, items: [] }])) as ServerDocument['categories'],
});

export const weightedDoc = (): ServerDocument => doc({
  skills: { weight: 60, items: [
    item('req_py', 'Python', 'required', 50),
    item('req_sql', 'SQL or PostgreSQL', 'required', 50, { alternatives: ['SQL', 'PostgreSQL'], source_text: 'Knowledge of SQL or PostgreSQL' }),
    item('req_docker', 'Docker', 'preferred', null),
  ] },
  experience: { weight: 40, items: [
    item('req_exp', '4 years as a Maintenance Planner', 'required', 100, { experience: { subject: 'Maintenance Planner', min_years: 4 } }),
    item('req_resp', 'Coordinate shutdown planning', 'preferred', null, { origin: 'from_responsibilities', source_text: 'Responsible for coordinating shutdown planning' }),
  ] },
});

export const makeView = (d: ServerDocument = weightedDoc(), over: Partial<RequirementsView> = {}): RequirementsView => ({
  job_id: '00000000-0000-0000-0000-0000000000aa', revision: 3, requirements: d, original: JSON.parse(JSON.stringify(d)),
  edited_categories: Object.fromEntries(CATEGORIES.map(c => [c, false])),
  readiness: { state: 'ready', scoring_mode: 'weighted', can_proceed: true, reasons: [], open_warning_ids: [], unresolved_warning_ids: [], structure_review_item_ids: [] },
  classification_warnings: [], classification_policy: { key: 'job_analysis.require_classification_acknowledgment', require_acknowledgment: true },
  preferred_only_confirmation: { confirmed: false },
  structure_review: { needs_review_item_ids: [], items: [
    { item_id: 'req_sql', category: 'skills', state: 'original', record: null },
    { item_id: 'req_exp', category: 'experience', state: 'original', record: null },
  ] },
  similarity_warnings: [], similarity_method: 'lexical_v1', can_edit: true, ...over,
});

export const emptyView = (): RequirementsView => makeView(doc({}), {
  readiness: { state: 'needs_items', scoring_mode: null, can_proceed: false, open_warning_ids: [], unresolved_warning_ids: [], structure_review_item_ids: [],
    reasons: [{ code: 'no_items', message: 'There are no requirements.', category: null, item_id: null }] },
  structure_review: { needs_review_item_ids: [], items: [] },
});

export const preferredOnlyView = (confirmed = false): RequirementsView => makeView(doc({
  skills: { weight: 0, items: [item('req_a', 'Docker is a plus', 'preferred', null), item('req_b', 'Kubernetes exposure', 'preferred', null)] },
}), confirmed
  ? { readiness: { state: 'ready', scoring_mode: 'none', can_proceed: true, reasons: [], open_warning_ids: [], unresolved_warning_ids: [], structure_review_item_ids: [] },
      preferred_only_confirmation: { confirmed: true, user_id: 'u-9', confirmed_at: '2026-03-03T10:00:00+00:00' }, structure_review: { needs_review_item_ids: [], items: [] } }
  : { readiness: { state: 'needs_confirmation', scoring_mode: null, can_proceed: false, open_warning_ids: [], unresolved_warning_ids: [], structure_review_item_ids: [],
      reasons: [{ code: 'preferred_only_unconfirmed', message: 'Only preferred items exist.', category: null, item_id: null }] },
      structure_review: { needs_review_item_ids: [], items: [] } });

export const warningView = (requireAck = true): RequirementsView => makeView(weightedDoc(), {
  readiness: { state: requireAck ? 'needs_classification_review' : 'ready', scoring_mode: requireAck ? null : 'weighted', can_proceed: !requireAck,
    reasons: requireAck ? [{ code: 'classification_warning_unresolved', message: 'x', category: 'skills', item_id: 'req_docker' }] : [],
    open_warning_ids: ['req_docker:preferred_cue_missing'], unresolved_warning_ids: ['req_docker:preferred_cue_missing'], structure_review_item_ids: [] },
  classification_warnings: [{ id: 'req_docker:preferred_cue_missing', code: 'preferred_cue_missing', item_id: 'req_docker', category: 'skills', message: 'm',
    evidence: { code: 'preferred_cue_missing', cue: null, source_text: 'Docker' }, state: 'unresolved', acknowledgment: null }],
  classification_policy: { key: 'job_analysis.require_classification_acknowledgment', require_acknowledgment: requireAck },
  similarity_warnings: [{ id: 'req_py~req_resp', kind: 'similar_requirement', same_category: false, differences: ['numbers'],
    items: [{ item_id: 'req_py', category: 'skills', importance: 'required' }, { item_id: 'req_resp', category: 'experience', importance: 'preferred' }] },
    { id: 'req_sql~req_docker', kind: 'possible_duplicate', same_category: true, differences: [],
    items: [{ item_id: 'req_sql', category: 'skills', importance: 'required' }, { item_id: 'req_docker', category: 'skills', importance: 'preferred' }] }],
});

export const structureView = (): RequirementsView => {
  const d = weightedDoc();
  d.categories.experience.items[0].text = '7 years as a Maintenance Planner';
  return makeView(d, {
    original: weightedDoc(),
    readiness: { state: 'needs_structure_review', scoring_mode: null, can_proceed: false, open_warning_ids: [], unresolved_warning_ids: [], structure_review_item_ids: ['req_exp'],
      reasons: [{ code: 'structure_review_pending', message: 'pending', category: 'experience', item_id: 'req_exp' }] },
    structure_review: { needs_review_item_ids: ['req_exp'], items: [
      { item_id: 'req_sql', category: 'skills', state: 'original', record: null },
      { item_id: 'req_exp', category: 'experience', state: 'needs_review', record: null }] },
    edited_categories: { ...Object.fromEntries(CATEGORIES.map(c => [c, false])), experience: true },
  });
};

export const invalidWeightsView = (): RequirementsView => {
  const d = weightedDoc();
  d.categories.skills.items[0].weight = 30;     // 30 + 50 != 100
  return makeView(d, { readiness: { state: 'needs_review', scoring_mode: null, can_proceed: false, open_warning_ids: [], unresolved_warning_ids: [], structure_review_item_ids: [],
    reasons: [{ code: 'required_weights_total', message: 'Required item weights total 80%, not 100%.', category: 'skills', item_id: null }] } });
};

export const apiError = (status: number, detail: Record<string, unknown>) => Object.assign(new Error(String(detail.message ?? 'error')), { status, data: { detail } });
