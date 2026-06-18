export function log(level, message) {
  const ts = new Date().toISOString();
  const prefix = {
    info:  '  INFO',
    warn:  '  WARN',
    error: ' ERROR',
  }[level] || ' INFO';
  console.log(`[${ts}] ${prefix} | ${message}`);
}
