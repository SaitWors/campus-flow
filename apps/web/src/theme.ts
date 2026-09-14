import type {Theme} from './types';

export const themes: readonly Theme[] = ['light','dark','black','ultra-black','system'];
export function parseTheme(value:string):Theme {
 return themes.includes(value as Theme) ? value as Theme : 'system';
}
export function resolveTheme(theme:Theme,systemDark:boolean):Exclude<Theme,'system'> {
 return theme==='system' ? (systemDark?'dark':'light') : theme;
}
export function nextTheme(theme:Theme):Theme {
 return themes[(themes.indexOf(theme)+1)%themes.length];
}
