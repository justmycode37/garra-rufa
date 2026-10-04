export function GarraMark({size=32,className=''}:{size?:number;className?:string}){
  return <img
    src="/brand/garra-rufa-logo-triangles-bold.png"
    width={1774}
    height={887}
    alt=""
    aria-hidden="true"
    draggable={false}
    className={`brand-mark mark-size-${size} ${className}`.trim()}
  />;
}

export function Brand({onClick,small=false}:{onClick?:()=>void;small?:boolean}){
  return <button className={`brand ${small?'small':''}`} onClick={onClick} aria-label="Garra Rufa home">
    <GarraMark size={small?26:32}/>
    <span>garra rufa<span className="brand-period">.</span></span>
  </button>;
}
