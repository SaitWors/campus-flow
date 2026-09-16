import {flushSync} from 'react-dom';
let active:ViewTransition|undefined;
export function transition(update:()=>void){
 if(!document.startViewTransition||document.hidden||document.documentElement.dataset.reducedMotion==='true'||matchMedia('(prefers-reduced-motion: reduce)').matches||document.querySelector('dialog[open]')){update();return;}
 active?.skipTransition();
 active=document.startViewTransition(()=>flushSync(update));
 void active.ready.catch(()=>{});
 void active.finished.catch(()=>{});
}
