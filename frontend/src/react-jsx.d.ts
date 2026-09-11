declare namespace JSX {
  type Element = any
  interface IntrinsicAttributes {
    key?: any
  }
  interface IntrinsicElements {
    [elemName: string]: any
  }
}

declare module 'react' {
  export type ComponentType<P = any> = (props: P) => JSX.Element | null
  export type ReactNode = any
  export function useEffect(effect: () => void | (() => void), deps?: any[]): void
  export function useMemo<T>(factory: () => T, deps?: any[]): T
  export function useRef<T = any>(initialValue?: T): { current: T }
  export function useState<T = any>(initialValue: T | (() => T)): [T, (value: T | ((previous: T) => T)) => void]
}

declare module 'react/jsx-runtime' {
  export namespace JSX {
    type Element = any
    interface IntrinsicAttributes {
      key?: any
    }
    interface IntrinsicElements {
      [elemName: string]: any
    }
  }
  export function jsx(type: any, props: any, key?: any): JSX.Element
  export function jsxs(type: any, props: any, key?: any): JSX.Element
  export const Fragment: any
}
