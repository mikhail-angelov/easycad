// Inline SVG icons (no icon-library dependency). 1em, currentColor.

interface Props {
  size?: number
}

function svg(size: number, children: preact.ComponentChildren) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="1.8"
      stroke-linecap="round"
      stroke-linejoin="round"
      aria-hidden="true"
    >
      {children}
    </svg>
  )
}

export const IconSave = ({ size = 18 }: Props) =>
  svg(size, (
    <>
      <path d="M12 3v11" />
      <path d="M8 11l4 4 4-4" />
      <path d="M4 17v2a2 2 0 002 2h12a2 2 0 002-2v-2" />
    </>
  ))

export const IconLoad = ({ size = 18 }: Props) =>
  svg(size, (
    <>
      <path d="M12 21V10" />
      <path d="M8 14l4-4 4 4" />
      <path d="M4 7V5a2 2 0 012-2h12a2 2 0 012 2v2" />
    </>
  ))

export const IconNew = ({ size = 18 }: Props) =>
  svg(size, (
    <>
      <rect x="4" y="3" width="16" height="18" rx="2" />
      <path d="M12 8v8" />
      <path d="M8 12h8" />
    </>
  ))

export const IconUser = ({ size = 18 }: Props) =>
  svg(size, (
    <>
      <circle cx="12" cy="8" r="4" />
      <path d="M4 21a8 8 0 0116 0" />
    </>
  ))

export const IconGithub = ({ size = 18 }: Props) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
    <path d="M12 .5C5.37.5 0 5.87 0 12.5c0 5.3 3.44 9.8 8.21 11.39.6.11.82-.26.82-.58v-2.03c-3.34.73-4.04-1.61-4.04-1.61-.55-1.39-1.34-1.76-1.34-1.76-1.09-.75.08-.73.08-.73 1.21.09 1.84 1.24 1.84 1.24 1.07 1.84 2.81 1.31 3.5 1 .11-.78.42-1.31.76-1.61-2.67-.3-5.47-1.34-5.47-5.96 0-1.32.47-2.39 1.24-3.23-.12-.31-.54-1.53.12-3.18 0 0 1.01-.32 3.3 1.23a11.5 11.5 0 016 0c2.29-1.55 3.3-1.23 3.3-1.23.66 1.65.24 2.87.12 3.18.77.84 1.24 1.91 1.24 3.23 0 4.63-2.8 5.65-5.48 5.95.43.37.81 1.1.81 2.22v3.29c0 .32.22.7.83.58A12.01 12.01 0 0024 12.5C24 5.87 18.63.5 12 .5z" />
  </svg>
)
