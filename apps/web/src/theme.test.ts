import {describe,it,expect} from 'vitest';
import {themes,parseTheme,resolveTheme,nextTheme} from './theme';

describe('saved and system themes',()=>{
 it('restores supported themes and handles stale preferences',()=>{
  for(const theme of themes) expect(parseTheme(theme)).toBe(theme);
  expect(parseTheme('unknown')).toBe('system');
 });
 it('follows OS changes only for the system preference',()=>{
  expect(resolveTheme('system',true)).toBe('dark');
  expect(resolveTheme('system',false)).toBe('light');
  for(const theme of themes.filter(t=>t!=='system')){
   expect(resolveTheme(theme,true)).toBe(theme);
   expect(resolveTheme(theme,false)).toBe(theme);
  }
 });
 it('cycles through all five themes',()=>{
  let theme=themes[0];const visited=[];
  for(let i=0;i<themes.length;i++){visited.push(theme);theme=nextTheme(theme);}
  expect(visited).toEqual(themes);expect(theme).toBe('light');
 });
});
