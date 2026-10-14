-- Read-only inventory of the privilege model of the cv_analyzer schema. SELECT statements only: it changes nothing, sets no role,
-- and reads no password or secret (pg_authid is not queried; rolpassword is not selected).
--
-- Run from the VPS, in the repository's backend directory (the psql variables come from the postgres container's own environment):
--   docker compose exec -T postgres sh -c 'psql -X -q -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f -' < deploy/runtime_role/inventory.sql > inventory_$(date +%Y%m%d%H%M).txt
-- Read the output as: roles, then who owns what (owners bypass RLS unless FORCE), then the exact grants from pg_class.relacl (not
-- information_schema, which shows only grants the connecting role can see), then RLS state, policies and the tenant tables without RLS.
\pset pager off
\echo '== connecting role and its attributes'
SELECT current_user AS connected_as, rolsuper, rolbypassrls, rolinherit FROM pg_roles WHERE rolname = current_user;
\echo '== roles (privileged attributes; passwords are not read)'
SELECT rolname, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolinherit, rolcanlogin, rolreplication
  FROM pg_roles WHERE rolname NOT LIKE 'pg\_%' ORDER BY rolname;
\echo '== role memberships (a member can assume the role it belongs to)'
SELECT m.rolname AS member, r.rolname AS role, am.admin_option
  FROM pg_auth_members am JOIN pg_roles m ON m.oid = am.member JOIN pg_roles r ON r.oid = am.roleid
  WHERE m.rolname NOT LIKE 'pg\_%' ORDER BY 1, 2;
\echo '== schema and object owners (owners bypass RLS unless FORCE, and may alter or drop)'
SELECT n.nspname, pg_get_userbyid(n.nspowner) AS schema_owner, n.nspacl::text AS schema_acl
  FROM pg_namespace n WHERE n.nspname = 'cv_analyzer';
SELECT pg_get_userbyid(c.relowner) AS owner, c.relkind, count(*) AS objects
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'cv_analyzer' GROUP BY 1, 2 ORDER BY 1, 2;
\echo '== exact table, view and sequence privileges (pg_class.relacl), every grantee other than the owner'
SELECT c.relname, CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END AS grantee,
       pg_get_userbyid(a.grantor) AS grantor, a.privilege_type, a.is_grantable
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  CROSS JOIN LATERAL aclexplode(c.relacl) a
  WHERE n.nspname = 'cv_analyzer' AND c.relkind IN ('r', 'v', 'S', 'p') AND c.relacl IS NOT NULL AND a.grantee <> c.relowner
  ORDER BY 1, 2, 4;
\echo '== function privileges and security mode (SECURITY DEFINER runs as the owner)'
SELECT p.proname, pg_get_userbyid(p.proowner) AS owner, p.prosecdef AS security_definer,
       CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END AS grantee, a.privilege_type
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
  LEFT JOIN LATERAL aclexplode(p.proacl) a ON a.grantee <> p.proowner
  WHERE n.nspname = 'cv_analyzer' ORDER BY 1, 4;
\echo '== RLS: enabled and forced per table'
SELECT c.relname, c.relrowsecurity AS rls_enabled, c.relforcerowsecurity AS rls_forced, pg_get_userbyid(c.relowner) AS owner
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname = 'cv_analyzer' AND c.relkind = 'r' ORDER BY c.relname;
\echo '== tables with a tenant_id column and NO row-level security (the isolation gap, live)'
SELECT c.relname
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped
  WHERE n.nspname = 'cv_analyzer' AND c.relkind = 'r' AND NOT c.relrowsecurity ORDER BY 1;
\echo '== RLS policies (roles, expressions; the settings they read are the ones to check against the application)'
SELECT tablename, policyname, cmd, roles, qual, with_check FROM pg_policies WHERE schemaname = 'cv_analyzer' ORDER BY tablename, policyname;
\echo '== triggers on the schema (non-internal)'
SELECT c.relname, t.tgname, t.tgenabled FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
  JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'cv_analyzer' AND NOT t.tgisinternal ORDER BY 1, 2;
\echo '== database owners and public schema creation'
SELECT datname, pg_get_userbyid(datdba) AS owner FROM pg_database WHERE datname NOT LIKE 'template%' ORDER BY 1;
SELECT has_schema_privilege('public', 'cv_analyzer', 'CREATE') AS public_can_create_in_cv_analyzer;
