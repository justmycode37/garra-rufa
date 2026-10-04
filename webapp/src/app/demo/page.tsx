import type { Metadata } from 'next';
import DemoFilm from './DemoFilm';

export const metadata: Metadata = {
  title: 'Connect the dots · garra rufa',
  description: 'To understand, means to connect the dots. A short film by garra rufa.',
  robots: { index: false, follow: false },
};

export default function DemoPage() {
  return <DemoFilm/>;
}
