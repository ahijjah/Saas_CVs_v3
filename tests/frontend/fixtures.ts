import type { RequirementsView } from '../../services/requirementsApi';

export const ids = { py: 'req_aaaaaaaaaaaa', sql: 'req_bbbbbbbbbbbb' };

export function view(overrides: Partial<RequirementsView> & { review?: Partial<RequirementsView['review']> } = {}): RequirementsView {
  const base: RequirementsView = {
    job_id: 'job-1',
    requirements_revision: 3,
    schema_version: 2,
    document: {
      schema_version: 2,
      categories: {
        skills: {
          weight: 100,
          items: [
            { id: ids.py, text: 'Python', importance: 'required', weight: 50, origin: 'stated',
              source_text: 'Python and SQL are required', alternatives: null, experience: null },
            { id: ids.sql, text: 'SQL', importance: 'required', weight: 50, origin: 'stated',
              source_text: 'Python and SQL are required', alternatives: null, experience: null },
          ],
        },
        experience: { weight: 0, items: [] },
        education: { weight: 0, items: [] },
        certifications: { weight: 0, items: [] },
        soft_skills: { weight: 0, items: [] },
        domain_knowledge: { weight: 0, items: [] },
        other_requirements: { weight: 0, items: [] },
      },
    },
    review: {
      readiness: { state: 'ready', can_proceed: true, scoring_mode: 'weighted', reasons: [] },
      confirmation: { status: 'not_required', confirmed_at: null },
      classification: { acknowledgment_required: true, open_warnings: [] },
      structure: { pending_item_ids: [] },
      structural: { status: 'valid', open_findings: [] },
      ...(overrides.review ?? {}),
    },
  };
  return { ...base, ...overrides, review: base.review };
}
