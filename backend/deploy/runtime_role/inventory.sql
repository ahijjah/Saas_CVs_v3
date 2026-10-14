-- Read-only inventory of the privilege model of the cv_analyzer schema. Safe to run on production (SELECT only).
-- Run:  psql -U <admin> -d cv_analyzer_prod -f inventory.sql > inventory_<TS>.txt
\pset pager off
\echo '== roles (privileged attributes)'
SELECT rolname, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolinherit, rolcanlogin
  FROM pg_roles WHERE rolname NOT LIKE 'pg\_%' ORDER BY rolname;
\echo '== role memberships (who can assume which role)'
SELECT m.rolname AS member, r.rolname AS role, am.admin_option
  FROM pg_auth_members am JOIN pg_roles m ON m.oid = am.member JOIN pg_roles r ON r.oid = am.roleid
  WHERE m.rolname NOT LIKE 'pg\_%' ORDER BY 1, 2;
\echo '== schema owner and table owners (owners bypass RLS unless FORCE, and may alter/drop)'
SELECT n.nspname, pg_get_userbyid(n.nspowner) AS owner FROM pg_namespace n WHERE n.nspname = 'cv_analyzer';
SELECT pg_get_userbyid(c.relowner) AS owner, count(*) AS objects, string_agg(c.relkind::text, '' ORDER BY c.relkind) AS kinds
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'cv_analyzer' GROUP BY 1 ORDER BY 1;
\echo '== RLS: enabled / forced per table'
SELECT c.relname, c.relrowsecurity AS rls_enabled, c.relforcerowsecurity AS rls_forced, pg_get_userbyid(c.relowner) AS owner
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname = 'cv_analyzer' AND c.relkind = 'r' ORDER BY c.relname;
\echo '== RLS policies (roles and expressions)'
SELECT tablename, policyname, cmd, roles, qual, with_check FROM pg_policies WHERE schemaname = 'cv_analyzer' ORDER BY tablename, policyname;
\echo '== privileges on tables granted to any role other than the owner'
SELECT table_name, grantee, string_agg(privilege_type, ',' ORDER BY privilege_type) AS privileges
  FROM information_schema.role_table_grants WHERE table_schema = 'cv_analyzer' AND grantee NOT IN (
    SELECT pg_get_userbyid(c.relowner) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'cv_analyzer' AND c.relkind = 'r' LIMIT 1)
  GROUP BY 1, 2 ORDER BY 1, 2;
\echo '== functions and their owners and security mode (SECURITY DEFINER runs as the owner)'
SELECT p.proname, pg_get_userbyid(p.proowner) AS owner, p.prosecdef AS security_definer
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'cv_analyzer' ORDER BY 1;
\echo '== triggers on the schema (non-internal)'
SELECT c.relname, t.tgname, t.tgenabled FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
  JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'cv_analyzer' AND NOT t.tgisinternal ORDER BY 1, 2;
\echo '== database-level privileges'
SELECT datname, pg_get_userbyid(datdba) AS owner FROM pg_database WHERE datname NOT LIKE 'template%' ORDER BY 1;
SELECT has_schema_privilege('public', 'cv_analyzer', 'CREATE') AS public_can_create_in_cv_analyzer;
