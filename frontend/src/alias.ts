/** Spreadsheet-column-style bijective numbering: 0->A, 25->Z, 26->AA, 27->AB, ... */
export function nextAlias(index: number): string {
  let n = index + 1
  let alias = ''
  while (n > 0) {
    n -= 1
    alias = String.fromCharCode(65 + (n % 26)) + alias
    n = Math.floor(n / 26)
  }
  return alias
}
