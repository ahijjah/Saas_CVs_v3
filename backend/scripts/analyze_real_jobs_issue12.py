#!/usr/bin/env python3
"""
Analyze Issue #12: Real key_responsibilities impact on actual jobs in database.

Usage (from production server):
  docker exec -it <container_id> python -m backend.scripts.analyze_real_jobs_issue12
"""

import asyncio
import sys
import os
from uuid import UUID

# Setup path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncpg
from services.llm_criteria_mapper import _flatten_criteria

async def main():
    # Connect to database
    # Connection details from environment or hardcoded defaults
    db_url = os.environ.get('DATABASE_URL', 'postgresql://cv_app:CHANGE_ME_IN_ENV@localhost/cv_analyzer')

    try:
        conn = await asyncpg.connect(db_url)
    except Exception as e:
        print(f"ERROR: Cannot connect to database: {e}")
        print(f"Expected DATABASE_URL env var or default postgres://cv_app:...@localhost/cv_analyzer")
        sys.exit(1)

    try:
        # Step 1: Get jobs JOB-2026-0088 through JOB-2026-0095
        # These are actual job_id UUIDs; we need to query by title pattern or direct ID
        # For now, assume they're stored; if not found, note it
        print("=" * 80)
        print("ISSUE #12 ANALYSIS: Real Job Criteria Inflation")
        print("=" * 80)

        # Try fetching by job ID patterns (if the IDs match a naming scheme)
        # Otherwise, fetch all jobs and filter
        jobs_to_analyze = []

        # Get all jobs (later filter by ID pattern or candidate names)
        all_jobs = await conn.fetch("""
            SELECT job_id, title, analysis_json
            FROM cv_analyzer.job_criteria
            WHERE analysis_json IS NOT NULL
            ORDER BY created_at DESC
            LIMIT 100
        """)

        if not all_jobs:
            print("ERROR: No jobs found with analysis_json in database")
            await conn.close()
            sys.exit(1)

        # Step 2: Get applications for specific candidates
        candidates = ['Mahmoud', 'Hamza', 'Amr', 'Basmala', 'Sabrine', 'Ahmad']
        candidate_jobs = {}

        for candidate in candidates:
            rows = await conn.fetch("""
                SELECT DISTINCT jc.job_id, j.title, jc.analysis_json
                FROM cv_analyzer.applications a
                JOIN cv_analyzer.jobs j ON a.job_id = j.job_id
                JOIN cv_analyzer.job_criteria jc ON j.job_id = jc.job_id
                WHERE a.candidate_name ILIKE %s
                AND jc.analysis_json IS NOT NULL
            """, f"%{candidate}%")

            if rows:
                for row in rows:
                    job_id = str(row['job_id'])
                    if job_id not in candidate_jobs:
                        candidate_jobs[job_id] = {
                            'title': row['title'],
                            'analysis_json': row['analysis_json'],
                            'candidates': []
                        }
                    candidate_jobs[job_id]['candidates'].append(candidate)

        # Combine: jobs from JOB-2026-* pattern + candidate jobs
        all_to_analyze = {}

        for job in all_jobs:
            job_id = str(job['job_id'])
            title = job['title']

            # Include if it matches JOB-2026-00** pattern OR if it's a candidate job
            if 'JOB-2026-00' in title or job_id in candidate_jobs:
                all_to_analyze[job_id] = {
                    'title': title,
                    'analysis_json': job['analysis_json'],
                    'candidates': candidate_jobs.get(job_id, {}).get('candidates', [])
                }

        if not all_to_analyze:
            print(f"No jobs matching JOB-2026-00** pattern or candidate jobs found.")
            print(f"Found {len(all_jobs)} jobs total with analysis_json.")
            print(f"Candidate match results: {candidate_jobs}")
            await conn.close()
            sys.exit(1)

        # Step 3: Analyze each job
        print(f"\nFound {len(all_to_analyze)} jobs to analyze\n")
        print("JOB_TITLE | TOTAL_CRITERIA | KEY_RESP_COUNT | KEY_RESP_% | SKILLS | EXP_MIN_YRS | EXP_ROLES | EXP_KEY_RESP | EDUCATION | CANDIDATES")
        print("-" * 150)

        total_all = 0
        total_key_resp = 0

        for job_id, job_data in sorted(all_to_analyze.items(), key=lambda x: x[1]['title']):
            title = job_data['title']
            analysis_json = job_data['analysis_json']
            candidates_str = ', '.join(job_data['candidates']) if job_data['candidates'] else '—'

            if not analysis_json:
                continue

            # Call real _flatten_criteria
            try:
                criteria = _flatten_criteria(analysis_json)
            except Exception as e:
                print(f"ERROR analyzing {title}: {e}")
                continue

            # Count by type
            total = len(criteria)
            key_resp_count = 0
            counts_by_dim = {
                'skills': 0,
                'experience_min_years': 0,
                'experience_roles': 0,
                'experience_key_resp': 0,
                'education': 0,
            }

            for c in criteria:
                dim = c['dimension']
                text = c['text']

                if dim == 'skills':
                    counts_by_dim['skills'] += 1
                elif dim == 'experience':
                    if 'Minimum' in text and 'years' in text:
                        counts_by_dim['experience_min_years'] += 1
                    elif 'Minimum education' not in text:
                        # Try to distinguish roles vs key_responsibilities
                        # Check against the actual structure
                        exp_data = analysis_json.get('experience', {})
                        roles = exp_data.get('relevant_roles', [])
                        if any(text == role for role in roles):
                            counts_by_dim['experience_roles'] += 1
                        else:
                            counts_by_dim['experience_key_resp'] += 1
                            key_resp_count += 1
                elif dim == 'education':
                    counts_by_dim['education'] += 1

            pct = (key_resp_count / total * 100) if total > 0 else 0

            # Compact output line
            print(f"{title:30s} | {total:14d} | {key_resp_count:14d} | {pct:9.1f}% | {counts_by_dim['skills']:6d} | {counts_by_dim['experience_min_years']:11d} | {counts_by_dim['experience_roles']:9d} | {counts_by_dim['experience_key_resp']:12d} | {counts_by_dim['education']:9d} | {candidates_str}")

            total_all += total
            total_key_resp += key_resp_count

        print("-" * 150)
        overall_pct = (total_key_resp / total_all * 100) if total_all > 0 else 0
        print(f"{'AGGREGATE':30s} | {total_all:14d} | {total_key_resp:14d} | {overall_pct:9.1f}%")
        print("=" * 80)

    finally:
        await conn.close()

if __name__ == '__main__':
    asyncio.run(main())
