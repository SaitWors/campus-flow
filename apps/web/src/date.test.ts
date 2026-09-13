import {describe,it,expect} from 'vitest';
import {addDays,addMonths,fromIso,iso,monday,monthGrid,parity} from './date';
import type {Settings} from './types';
const settings={anchor_monday:'2026-08-31',anchor_parity:'odd'} as Settings;
describe('academic calendar',()=>{
 it('starts Monday and handles Sunday correctly',()=>expect(iso(monday(fromIso('2026-09-13')))).toBe('2026-09-07'));
 it('keeps semester parity independent of ISO week numbers',()=>{expect(parity(fromIso('2026-09-01'),settings)).toBe('odd');expect(parity(fromIso('2026-09-07'),settings)).toBe('even');expect(parity(fromIso('2026-09-14'),settings)).toBe('odd');});
 it('supports the opposite reference parity and preceding weeks',()=>{expect(parity(fromIso('2026-09-01'),{...settings,anchor_parity:'even'})).toBe('even');expect(parity(fromIso('2026-08-24'),settings)).toBe('even');});
 it('builds six complete weeks across month boundaries',()=>{const days=monthGrid(fromIso('2026-09-15'));expect(days).toHaveLength(42);expect(iso(days[0])).toBe('2026-08-31');expect(iso(days[41])).toBe('2026-10-11');});
 it('month navigation does not skip February from January 31',()=>expect(iso(addMonths(fromIso('2027-01-31'),1))).toBe('2027-02-01'));
 it('crosses year boundaries and leap days',()=>{expect(iso(addDays(fromIso('2026-12-31'),1))).toBe('2027-01-01');expect(iso(addDays(fromIso('2028-02-28'),1))).toBe('2028-02-29');});
});
