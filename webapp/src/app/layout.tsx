import type { Metadata } from 'next';
import localFont from 'next/font/local';
import './globals.css';
import ConnectionExperience from '@/components/ConnectionExperience';
const fahkwang=localFont({
  src:[
    {path:'../../public/fonts/Fahkwang-ExtraLight.ttf',weight:'200',style:'normal'},
    {path:'../../public/fonts/Fahkwang-Regular.ttf',weight:'400',style:'normal'},
    {path:'../../public/fonts/Fahkwang-Medium.ttf',weight:'500',style:'normal'},
    {path:'../../public/fonts/Fahkwang-SemiBold.ttf',weight:'600',style:'normal'},
    {path:'../../public/fonts/Fahkwang-Bold.ttf',weight:'700',style:'normal'},
  ],
  variable:'--font-fahkwang',
  display:'swap',
});
export const metadata:Metadata={title:'garra rufa · Every connection matters',description:'A connected atlas for rare diseases. Explore the evidence, find your people, and take the next step.'};
export default function RootLayout({children}:{children:React.ReactNode}){return <html lang="en"><body className={fahkwang.variable}><ConnectionExperience>{children}</ConnectionExperience></body></html>;}
