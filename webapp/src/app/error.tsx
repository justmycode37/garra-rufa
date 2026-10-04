'use client';
export default function ErrorPage({reset}:{reset:()=>void}){return <main className="error-page"><h1>Let’s find our way back.</h1><p>Something interrupted this page. Your saved workspace is still there.</p><button className="primary" onClick={reset}>Try again</button></main>;}
