import {describe,it,expect} from 'vitest';
import {botStatus,filterBots,botError,type ManagedBot} from './bots';

const now=Date.parse('2026-10-15T21:00:00Z');
const bot:ManagedBot={id:'1',name:'Guild helper',platform:'discord',image:'ghcr.io/saitwors/bot@sha256:'+'a'.repeat(64),desired_state:'running',observed_state:'running',revision:2,observed_revision:2,last_error:'',last_logs:'',last_checked:new Date(now-20000).toISOString()};

describe('bot control panel state',()=>{
 it('only calls a recent, confirmed matching revision running',()=>{
  expect(botStatus(bot,now)).toBe('running');
  expect(botStatus({...bot,last_checked:new Date(now-100000).toISOString()},now)).toBe('stale');
  expect(botStatus({...bot,last_checked:null},now)).toBe('starting');
  expect(botStatus({...bot,desired_state:'stopped',last_checked:null},now)).toBe('waiting');
 });
 it('shows pending start, stop or update instead of reporting success early',()=>{
  expect(botStatus({...bot,observed_revision:1},now)).toBe('updating');
  expect(botStatus({...bot,observed_state:'stopped',observed_revision:1},now)).toBe('starting');
  expect(botStatus({...bot,desired_state:'stopped',revision:3},now)).toBe('stopping');
  expect(botStatus({...bot,observed_state:'error'},now)).toBe('error');
 });
 it('searches names and filters by platform and actionable errors',()=>{
  const telegram={...bot,id:'2',name:'Учебный помощник',platform:'telegram' as const};
  expect(filterBots([bot,telegram],'учеб','all','all',now)).toEqual([telegram]);
  expect(filterBots([bot,telegram],'','discord','all',now)).toEqual([bot]);
  expect(filterBots([bot,{...telegram,observed_state:'error'}],'','all','attention',now)).toHaveLength(1);
 });
 it('explains validation and security errors without raw API codes',()=>{
  expect(botError('invalid_bot_token','ru')).toContain('токен');
  expect(botError('bot_mfa_step_up_required','en')).toContain('expired');
  expect(botError('validation','ru')).toContain('поля');
  expect(botError('unknown_code','ru')).not.toContain('unknown_code');
 });
});
