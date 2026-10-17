export function parseAndFormat(input, indent = 2) {
  if (input.length > 10 * 1024 * 1024) throw new Error("JSON must be 10 MB or smaller");
  if (!input.trim()) throw new Error("Enter JSON or open a file");
  const value = JSON.parse(input);
  return JSON.stringify(value, null, indent);
}
