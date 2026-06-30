/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // HIG semantic — backed by CSS variables in index.css.
        // Light/dark switch happens at the variable layer, so component
        // classes never need a dark: prefix for these tokens.
        bg: 'rgb(var(--bg) / <alpha-value>)',
        'bg-secondary': 'rgb(var(--bg-secondary) / <alpha-value>)',
        'bg-tertiary': 'rgb(var(--bg-tertiary) / <alpha-value>)',
        'bg-elevated': 'rgb(var(--bg-elevated) / <alpha-value>)',
        surface: 'rgb(var(--surface) / <alpha-value>)',
        'surface-hover': 'rgb(var(--surface-hover) / <alpha-value>)',

        border: 'rgb(var(--border) / <alpha-value>)',
        'border-strong': 'rgb(var(--border-strong) / <alpha-value>)',
        separator: 'rgb(var(--separator) / <alpha-value>)',

        'text-primary': 'rgb(var(--text-primary) / <alpha-value>)',
        'text-secondary': 'rgb(var(--text-secondary) / <alpha-value>)',
        'text-tertiary': 'rgb(var(--text-tertiary) / <alpha-value>)',
        'text-quaternary': 'rgb(var(--text-quaternary) / <alpha-value>)',

        // HIG fill colors (translucent grays for inline buttons / chips)
        fill: 'rgb(var(--fill) / <alpha-value>)',
        'fill-secondary': 'rgb(var(--fill-secondary) / <alpha-value>)',
        'fill-tertiary': 'rgb(var(--fill-tertiary) / <alpha-value>)',
        'fill-quaternary': 'rgb(var(--fill-quaternary) / <alpha-value>)',

        accent: 'rgb(var(--accent) / <alpha-value>)',
        'accent-hover': 'rgb(var(--accent-hover) / <alpha-value>)',
        'accent-subtle': 'rgb(var(--accent-subtle) / <alpha-value>)',
        'accent-fg': 'rgb(var(--accent-fg) / <alpha-value>)',

        positive: 'rgb(var(--positive) / <alpha-value>)',
        warning: 'rgb(var(--warning) / <alpha-value>)',
        negative: 'rgb(var(--negative) / <alpha-value>)',
        info: 'rgb(var(--info) / <alpha-value>)',
      },
      fontFamily: {
        // SF Pro is loaded via @font-face in index.css. Native Apple devices
        // get the system fonts directly; everywhere else falls back through
        // a safe stack ending in -apple-system → Inter → system-ui.
        sans: [
          'SF Pro Text',
          '-apple-system',
          'BlinkMacSystemFont',
          'Inter',
          'PingFang SC',
          'Microsoft YaHei',
          'system-ui',
          'sans-serif',
        ],
        display: [
          'SF Pro Display',
          'SF Pro Text',
          '-apple-system',
          'BlinkMacSystemFont',
          'Inter',
          'system-ui',
          'sans-serif',
        ],
        mono: [
          'SF Mono',
          'JetBrains Mono',
          'Menlo',
          'Monaco',
          'Consolas',
          'monospace',
        ],
      },
      // HIG type ramp (size / line-height / letter-spacing in pt → px)
      fontSize: {
        'caption-2': ['11px', { lineHeight: '13px', letterSpacing: '0.06px' }],
        'caption-1': ['12px', { lineHeight: '16px', letterSpacing: '0' }],
        footnote: ['13px', { lineHeight: '18px', letterSpacing: '-0.08px' }],
        subhead: ['15px', { lineHeight: '20px', letterSpacing: '-0.24px' }],
        callout: ['16px', { lineHeight: '21px', letterSpacing: '-0.32px' }],
        body: ['17px', { lineHeight: '22px', letterSpacing: '-0.41px' }],
        headline: ['17px', { lineHeight: '22px', letterSpacing: '-0.41px', fontWeight: '600' }],
        'title-3': ['20px', { lineHeight: '25px', letterSpacing: '-0.45px' }],
        'title-2': ['22px', { lineHeight: '28px', letterSpacing: '-0.5px' }],
        'title-1': ['28px', { lineHeight: '34px', letterSpacing: '-0.6px' }],
        'large-title': ['34px', { lineHeight: '41px', letterSpacing: '-0.8px' }],
      },
      // HIG continuous-corner approximation
      borderRadius: {
        none: '0',
        xs: '4px',
        sm: '6px',
        DEFAULT: '8px',
        md: '10px',
        lg: '12px',
        xl: '14px',
        '2xl': '18px',
        '3xl': '22px',
        full: '9999px',
      },
      boxShadow: {
        // HIG floating-element shadow scale
        xs: '0 1px 2px rgb(0 0 0 / 0.04)',
        sm: '0 1px 3px rgb(0 0 0 / 0.06), 0 1px 2px rgb(0 0 0 / 0.04)',
        DEFAULT: '0 4px 8px rgb(0 0 0 / 0.06), 0 2px 4px rgb(0 0 0 / 0.04)',
        md: '0 8px 16px rgb(0 0 0 / 0.08), 0 4px 8px rgb(0 0 0 / 0.04)',
        lg: '0 16px 32px rgb(0 0 0 / 0.10), 0 8px 16px rgb(0 0 0 / 0.06)',
        xl: '0 24px 48px rgb(0 0 0 / 0.12), 0 12px 24px rgb(0 0 0 / 0.08)',
        // Inner highlight for sheets / elevated cards (HIG glass effect)
        'inset-hairline': 'inset 0 0 0 0.5px rgb(255 255 255 / 0.12)',
        focus: '0 0 0 3px rgb(var(--accent) / 0.28)',
      },
      backdropBlur: {
        // HIG materials map: thin / regular / thick / chrome
        thin: '12px',
        regular: '20px',
        thick: '40px',
        chrome: '50px',
      },
      transitionTimingFunction: {
        // Apple's standard ease curves
        standard: 'cubic-bezier(0.4, 0, 0.2, 1)',
        emphasized: 'cubic-bezier(0.2, 0, 0, 1)',
        spring: 'cubic-bezier(0.34, 1.56, 0.64, 1)',
      },
      keyframes: {
        fadeIn: {
          from: { opacity: '0', transform: 'translateY(4px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        slideIn: {
          from: { opacity: '0', transform: 'translateX(8px)' },
          to: { opacity: '1', transform: 'translateX(0)' },
        },
        drawerIn: {
          from: { transform: 'translateX(100%)' },
          to: { transform: 'translateX(0)' },
        },
        overlayIn: {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
        dialogIn: {
          from: { opacity: '0', transform: 'translateY(8px) scale(0.96)' },
          to: { opacity: '1', transform: 'translateY(0) scale(1)' },
        },
        toastIn: {
          from: { opacity: '0', transform: 'translateY(-8px) scale(0.96)' },
          to: { opacity: '1', transform: 'translateY(0) scale(1)' },
        },
        popoverIn: {
          from: { opacity: '0', transform: 'translateY(-4px) scale(0.98)' },
          to: { opacity: '1', transform: 'translateY(0) scale(1)' },
        },
        spin: { to: { transform: 'rotate(360deg)' } },
        pulse: {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.4' },
        },
        shimmer: {
          '0%': { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
      },
      animation: {
        'fade-in': 'fadeIn 0.24s cubic-bezier(0.2, 0, 0, 1) forwards',
        'slide-in': 'slideIn 0.2s cubic-bezier(0.2, 0, 0, 1) forwards',
        'drawer-in': 'drawerIn 0.32s cubic-bezier(0.32, 0.72, 0, 1) forwards',
        'overlay-in': 'overlayIn 0.2s ease-out forwards',
        'dialog-in': 'dialogIn 0.24s cubic-bezier(0.32, 0.72, 0, 1) forwards',
        'toast-in': 'toastIn 0.22s cubic-bezier(0.32, 0.72, 0, 1) forwards',
        'popover-in': 'popoverIn 0.16s cubic-bezier(0.32, 0.72, 0, 1) forwards',
        spin: 'spin 0.8s linear infinite',
        pulse: 'pulse 1.5s ease-in-out infinite',
        shimmer: 'shimmer 1.5s ease-in-out infinite',
      },
    },
  },
  plugins: [],
}
