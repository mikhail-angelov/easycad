type Translate = (key: string, params?: Record<string, string | number>) => string

export type GeometryStats = {
  size: [string, string, string]
  solids: string
  faces: string
  edges: string
}

// The worker owns this stable, English machine-readable format. The UI consumes
// only its numeric facts and renders all human-facing labels through i18n.
export function parseGeometryInfo(info: string | null): GeometryStats | null {
  if (!info) return null
  const size = info.match(/Size:\s*([\d.]+)\s*x\s*([\d.]+)\s*x\s*([\d.]+)\s*mm/i)
  const topology = info.match(/Topology:\s*(\d+)\s+solid\(s\),\s*(\d+)\s+faces,\s*(\d+)\s+edges/i)
  if (!size || !topology) return null
  return { size: [size[1], size[2], size[3]], solids: topology[1], faces: topology[2], edges: topology[3] }
}

export function formatGeometryInfo(info: string | null, t: Translate): string {
  const stats = parseGeometryInfo(info)
  if (!stats) return t('geometry.unavailable')
  const [x, y, z] = stats.size
  return [
    `${t('geometry.size')}: ${x} × ${y} × ${z} ${t('geometry.mm')}`,
    `${t('geometry.topology')}: ${t('geometry.solids', { n: stats.solids })}, ${t('geometry.faces', { n: stats.faces })}, ${t('geometry.edges', { n: stats.edges })}`,
  ].join(' · ')
}
