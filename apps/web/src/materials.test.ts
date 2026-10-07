import {describe,expect,it} from 'vitest';
import * as study from './study';
import type {User} from './types';

describe('subject material boundaries',()=>{
 it('keeps category/search/page parameters separate from an encoded subject key',()=>{
  expect(study.materialListPath('subject/key','lecture','  chapter & notes  ',40)).toBe('/api/schedule/subjects/subject%2Fkey/materials?limit=20&offset=40&category=lecture&q=chapter+%26+notes');
  expect(study.materialListPath('math','all','',0)).toBe('/api/schedule/subjects/math/materials?limit=20&offset=0');
 });
 it('encodes original filenames and metadata for the raw byte upload contract',()=>{
  const url=new URL(study.materialUploadPath('math',{filename:'Конспект & 1.pdf',title:'  Chapter 1  ',description:'Summary\nDetails',category:'notes'}),'http://campus.test');
  expect(url.pathname).toBe('/api/schedule/subjects/math/materials');
  expect(Object.fromEntries(url.searchParams)).toEqual({filename:'Конспект & 1.pdf',title:'Chapter 1',description:'Summary\nDetails',category:'notes'});
 });
 it('allows only active leadership accounts to expose material write controls',()=>{
  const user={id:'one',role:'student',status:'active'} as User;
  expect(study.canManageMaterials(user)).toBe(false);
  for(const role of ['head','deputy','admin'] as const)expect(study.canManageMaterials({...user,role})).toBe(true);
  expect(study.canManageMaterials({...user,role:'head',status:'pending'})).toBe(false);
  expect(study.canManageMaterials({...user,role:'admin',status:'blocked'})).toBe(false);
  expect(study.canManageMaterials(null)).toBe(false);
 });
 it('rejects empty, oversized, unsafe and unsupported selections before upload',()=>{
  const max=50*1024*1024;
  for(const [name,size,want] of [['empty.pdf',0,'material_empty'],['large.pdf',max+1,'material_too_large'],['image.svg',50,'material_type_not_allowed'],['page.html',50,'material_type_not_allowed'],['../notes.txt',50,'material_filename_invalid'],['bad\\name.txt',50,'material_filename_invalid']] as const){
   expect(study.materialFileError({name,size},max)).toBe(want);
  }
  expect(study.materialFileError({name:'Лекция 1.PDF',size:70000},max)).toBe(null);
  expect(study.materialFileError({name:'scan.heic',size:max},max)).toBe(null);
 });
 it('permits preview only for supported raster MIME types',()=>{
  for(const mime of ['image/png','image/jpeg','image/webp','image/gif'])expect(study.canPreviewMaterial(mime)).toBe(true);
  for(const mime of ['image/svg+xml','image/heic','text/html','application/pdf'])expect(study.canPreviewMaterial(mime)).toBe(false);
 });
 it('uses binary sizes in the account language and a safe default title',()=>{
  expect(study.formatFileSize(0,'en')).toBe('0 B');
  expect(study.formatFileSize(1536,'en')).toBe('1.5 KiB');
  expect(study.formatFileSize(52428800,'ru')).toBe('50 МиБ');
  expect(study.materialTitle('Лекция 1.pdf')).toBe('Лекция 1');
  expect(study.materialTitle('a'.repeat(180)+'.pdf')).toHaveLength(160);
 });
});
