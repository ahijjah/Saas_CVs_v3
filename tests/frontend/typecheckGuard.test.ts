import { readFileSync, readdirSync, statSync } from 'fs';
import path from 'path';
import { describe, expect, it } from 'vitest';

// Guards the zero-error type check (`npm run typecheck`). These fail if a change narrows the
// checked files, weakens the compiler options, or suppresses errors in source.
const root = path.resolve(__dirname, '../..');
const tsconfig = JSON.parse(readFileSync(path.join(root, 'tsconfig.json'), 'utf8')) as {
  include?: string[];
  files?: string[];
  exclude?: string[];
  compilerOptions: Record<string, unknown>;
};

const SOURCE_DIRS = ['pages', 'components', 'services', 'context', 'utils', 'constants'];
const SOURCE_FILES = ['App.tsx', 'index.tsx', 'config.ts', 'types.ts'];
const SUPPRESSION = /@ts-(ignore|nocheck|expect-error)\b/;

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) return walk(full);
    return /\.(ts|tsx)$/.test(name) ? [full] : [];
  });
}

describe('type-check configuration', () => {
  it('checks every source file (no include narrowing, no exclude, no explicit file list)', () => {
    expect(tsconfig.include).toBeUndefined();
    expect(tsconfig.files).toBeUndefined();
    expect(tsconfig.exclude).toBeUndefined();
  });

  it('does not weaken compiler strictness', () => {
    const co = tsconfig.compilerOptions;
    expect(co.strict).not.toBe(false);
    expect(co.noImplicitAny).not.toBe(false);
    expect(co.strictNullChecks).not.toBe(false);
    expect(co.suppressImplicitAnyIndexErrors).toBeUndefined();
    expect(co.noEmit).toBe(true);
  });

  it('exposes the zero-error check as an npm script', () => {
    const pkg = JSON.parse(readFileSync(path.join(root, 'package.json'), 'utf8')) as { scripts: Record<string, string> };
    expect(pkg.scripts.typecheck).toBe('tsc --noEmit');
  });
});

describe('source suppression', () => {
  it('contains no @ts-ignore, @ts-nocheck or @ts-expect-error comments', () => {
    const files = [
      ...SOURCE_DIRS.filter((d) => { try { return statSync(path.join(root, d)).isDirectory(); } catch { return false; } })
        .flatMap((d) => walk(path.join(root, d))),
      ...SOURCE_FILES.map((f) => path.join(root, f)),
    ];
    const hits = files.filter((f) => SUPPRESSION.test(readFileSync(f, 'utf8')));
    expect(hits.map((f) => path.relative(root, f))).toEqual([]);
  });
});
