import {describe,expect,it} from 'vitest';
import {assignmentSubgroup,defaultStudyFilters,filterLessons,instantToLocalInput,lessonCountdown,localInputToInstant,nextLesson,readStudyFilters,saveStudyFilters,studyFilterKey} from './study';
import type {Lesson,User} from './types';

const student={id:'student-a',subgroup:2,role:'student'} as User;
const lesson=(id:string,start:string,end:string,subgroup=0,status:Lesson['status']='confirmed')=>({id,starts_at:start,ends_at:end,subgroup,status} as Lesson);
const storage=()=>{
 const values=new Map<string,string>();
 return {getItem:(key:string)=>values.get(key)??null,setItem:(key:string,value:string)=>{values.set(key,value);}};
};

describe('study filters',()=>{
 it('starts with the account subgroup and includes common lessons',()=>{
  const defaults=defaultStudyFilters(student);
  expect(defaults.subgroup).toBe(2);
  expect(filterLessons([lesson('common','','',0),lesson('one','','',1),lesson('two','','',2)],defaults).map(l=>l.id)).toEqual(['common','two']);
 });
 it('keeps each account and the guest filters separate across reloads',()=>{
  const device=storage();
  saveStudyFilters(device,student,{...defaultStudyFilters(student),subgroup:1,kind:'lab',view:'day',compact:false});
  expect(readStudyFilters(device,student)).toMatchObject({subgroup:1,kind:'lab',view:'day',compact:false});
  expect(readStudyFilters(device,{...student,id:'student-b',subgroup:2})).toMatchObject({subgroup:2,kind:'all'});
  expect(readStudyFilters(device,null)).toMatchObject({subgroup:0,kind:'all'});
  expect(new Set([studyFilterKey(student),studyFilterKey({...student,id:'student-b'}),studyFilterKey(null)]).size).toBe(3);
 });
 it('ignores malformed device preferences and validates saved fields independently',()=>{
  const device=storage();
  device.setItem(studyFilterKey(student),'invalid json');
  expect(readStudyFilters(device,student)).toEqual(defaultStudyFilters(student));
  device.setItem(studyFilterKey(student),JSON.stringify({subgroup:19,kind:'unknown',view:'month',compact:'yes',collapsePast:false,collapseEmpty:false}));
  expect(readStudyFilters(device,student)).toMatchObject({subgroup:2,kind:'all',view:'month',compact:true,collapsePast:false,collapseEmpty:false});
 });
});

describe('assignment account and deadline boundaries',()=>{
 it('limits a student assignment filter to their own subgroup after browsing another schedule',()=>{
  expect(assignmentSubgroup(1,student)).toBe(0);
  expect(assignmentSubgroup(2,student)).toBe(2);
  expect(assignmentSubgroup(1,{...student,role:'head'})).toBe(1);
 });
 it('round-trips a semester deadline independently of the device timezone',()=>{
  expect(instantToLocalInput('2026-10-06T09:30:00Z','Europe/Moscow')).toBe('2026-10-06T12:30');
  expect(localInputToInstant('2026-10-06T12:30','Europe/Moscow')).toBe('2026-10-06T09:30:00.000Z');
  expect(localInputToInstant('2026-10-06T12:30','UTC')).toBe('2026-10-06T12:30:00.000Z');
 });
 it('rejects dates normalized by JavaScript and nonexistent daylight saving times',()=>{
  expect(localInputToInstant('2026-02-30T12:30','Europe/Moscow')).toBeNull();
  expect(localInputToInstant('2026-03-29T02:30','Europe/Berlin')).toBeNull();
 });
});

describe('current and next class',()=>{
 const items=[
  lesson('past','2026-10-06T05:00:00Z','2026-10-06T06:00:00Z'),
  lesson('cancelled','2026-10-06T06:00:00Z','2026-10-06T07:00:00Z',0,'cancelled'),
  lesson('other','2026-10-06T06:00:00Z','2026-10-06T07:00:00Z',1),
  lesson('current','2026-10-06T06:30:00Z','2026-10-06T08:00:00Z',2),
  lesson('next','2026-10-06T08:01:00Z','2026-10-06T09:30:00Z',0),
 ];
 it('chooses an ongoing class before the next one using absolute semester instants',()=>{
  expect(nextLesson(items,new Date('2026-10-06T07:00:00Z'),2)).toMatchObject({lesson:{id:'current'},state:'current',minutes:60});
 });
 it('moves to the next class at the end boundary and rounds remaining minutes up',()=>{
  expect(nextLesson(items,new Date('2026-10-06T08:00:01Z'),2)).toMatchObject({lesson:{id:'next'},state:'upcoming',minutes:1});
 });
 it('ignores cancelled, past, invalid, and other-subgroup classes and sorts unordered input',()=>{
  const later=lesson('later','2026-10-07T06:30:00Z','2026-10-07T08:00:00Z',2);
  expect(nextLesson([later,...items.slice().reverse(),lesson('invalid','bad','bad')],new Date('2026-10-06T08:00:00Z'),2)?.lesson.id).toBe('next');
  expect(nextLesson(items,new Date('2026-10-07T09:00:00Z'),2)).toBeNull();
 });
});

describe('lesson countdown display',()=>{
 it('keeps a sub-hour wait in minutes',()=>{
  expect(lessonCountdown(59)).toEqual({value:59,unit:'minutes',remainder:null,remainderUnit:null});
 });
 it('shows hours and remaining minutes from the hour boundary through 23 hours',()=>{
  expect(lessonCountdown(60)).toEqual({value:1,unit:'hours',remainder:0,remainderUnit:'minutes'});
  expect(lessonCountdown(1439)).toEqual({value:23,unit:'hours',remainder:59,remainderUnit:'minutes'});
 });
 it('switches to days and hours at 24 hours rather than showing a large hour count',()=>{
  expect(lessonCountdown(1440)).toEqual({value:1,unit:'days',remainder:0,remainderUnit:'hours'});
  expect(lessonCountdown(7*1440+22*60+5)).toEqual({value:7,unit:'days',remainder:22,remainderUnit:'hours'});
 });
});
