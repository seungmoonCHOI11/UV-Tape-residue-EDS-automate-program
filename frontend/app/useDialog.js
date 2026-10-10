"use client";
import {useEffect,useRef} from 'react';
// Keep keyboard navigation inside the topmost dialog and restore the trigger on close.
export default function useDialog(close){
 const ref=useRef(null),closeRef=useRef(close);closeRef.current=close;
 useEffect(()=>{
  const dialog=ref.current,previous=document.activeElement;if(!dialog)return;
  const targets=()=>[...dialog.querySelectorAll('button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex="0"]')].filter(e=>e.getClientRects().length);
  dialog.tabIndex=-1;(targets()[0]||dialog).focus();
  const key=e=>{const dialogs=[...document.querySelectorAll('[role="dialog"]')];if(dialogs.at(-1)!==dialog)return;if(e.key==='Escape'){e.preventDefault();e.stopImmediatePropagation();closeRef.current();}if(e.key==='Tab'){const list=targets(),first=list[0],last=list.at(-1);if(!first){e.preventDefault();dialog.focus();return}if(e.shiftKey&&(document.activeElement===first||!dialog.contains(document.activeElement))){e.preventDefault();last.focus()}else if(!e.shiftKey&&(document.activeElement===last||!dialog.contains(document.activeElement))){e.preventDefault();first.focus()}}};
  document.addEventListener('keydown',key,true);return()=>{document.removeEventListener('keydown',key,true);if(previous?.isConnected)previous.focus()};
 },[]);return ref;
}
