// Deterministic app-owned calendar icons, generated without image dependencies.
import {deflateSync} from 'node:zlib';
import {mkdirSync,writeFileSync} from 'node:fs';
const table=Array.from({length:256},(_,n)=>{for(let k=0;k<8;k++)n=n&1?0xedb88320^(n>>>1):n>>>1;return n>>>0;});
function crc(bytes){let n=0xffffffff;for(const b of bytes)n=table[(n^b)&255]^(n>>>8);return(n^0xffffffff)>>>0;}
function chunk(type,data){const name=Buffer.from(type),head=Buffer.alloc(4),tail=Buffer.alloc(4);head.writeUInt32BE(data.length);tail.writeUInt32BE(crc(Buffer.concat([name,data])));return Buffer.concat([head,name,data,tail]);}
function icon(size,badge=false){
 const rows=Buffer.alloc((size*4+1)*size);
 for(let y=0;y<size;y++)for(let x=0;x<size;x++){
   const nx=x/size,ny=y/size,px=(size*4+1)*y+1+x*4;
   const body=nx>.24&&nx<.76&&ny>.29&&ny<.76;
   const border=body&&(nx<.285||nx>.715||ny<.34||ny>.715);
   const rule=body&&ny>.41&&ny<.455;
   const rings=ny>.21&&ny<.365&&((nx>.355&&nx<.4)||(nx>.6&&nx<.645));
   const dot=ny>.53&&ny<.59&&((nx>.36&&nx<.42)||(nx>.48&&nx<.54)||(nx>.60&&nx<.66));
   const mark=border||rule||rings||dot;
   const color=badge?(mark?[255,255,255,255]:[0,0,0,0]):mark?[255,255,255,255]:[17,30,58,255];
   for(let k=0;k<4;k++)rows[px+k]=color[k];
 }
 const ihdr=Buffer.alloc(13);ihdr.writeUInt32BE(size);ihdr.writeUInt32BE(size,4);ihdr[8]=8;ihdr[9]=6;
 return Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]),chunk('IHDR',ihdr),chunk('IDAT',deflateSync(rows)),chunk('IEND',Buffer.alloc(0))]);
}
mkdirSync('public/icons',{recursive:true});
for(const [name,size,badge] of [['icon-192',192,false],['icon-512',512,false],['maskable-512',512,false],['apple-touch-icon',180,false],['badge-96',96,true]])writeFileSync('public/icons/'+name+'.png',icon(size,badge));
