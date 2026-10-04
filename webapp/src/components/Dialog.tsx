'use client';
import { useEffect,useId,useRef } from 'react';
import { X } from 'lucide-react';
export default function Dialog({title,onClose,children,wide=false,className=''}:{title:string;onClose:()=>void;children:React.ReactNode;wide?:boolean;className?:string}){
  const ref=useRef<HTMLDialogElement>(null);
  const titleId=useId();
  useEffect(()=>{const dialog=ref.current;dialog?.showModal();return()=>dialog?.close();},[]);
  return <dialog ref={ref} aria-labelledby={titleId} className={`dialog ${wide?'wide':''} ${className}`} onCancel={e=>{e.preventDefault();onClose();}} onClick={e=>{if(e.target===e.currentTarget){const r=e.currentTarget.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)onClose();}}}><div className="dialog-top"><h2 id={titleId}>{title}</h2><button type="button" className="icon-button" onClick={onClose} aria-label="Close dialog"><X size={18}/></button></div>{children}</dialog>;
}
