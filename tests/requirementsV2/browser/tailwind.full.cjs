// Tailwind build for the FULL application when the CDN script is unavailable (same colour tokens as index.html's inline config).
module.exports = {
  content: ['../../../pages/**/*.tsx', '../../../components/**/*.tsx', '../../../App.tsx', '../../../context/**/*.tsx', '../../../utils/**/*.ts'],
  theme: { extend: { colors: {
    primary: '#2563EB', primaryDark: '#1E40AF', background: '#F8FAFC', card: '#FFFFFF', border: '#E5E7EB', textMain: '#111827',
    textMuted: '#6B7280', success: '#16A34A', warning: '#F59E0B', error: '#DC2626' } } },
};
