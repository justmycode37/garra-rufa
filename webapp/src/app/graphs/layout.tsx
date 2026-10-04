import type { Metadata } from 'next';
import type { ReactNode } from 'react';
import GraphsShell from '@/components/graphs/GraphsShell';

export const metadata: Metadata = {
  title: 'Knowledge graphs · garra rufa',
  description: 'A patient-facing overview and an evidence graph of existing solutions for rare diseases, or build a new graph from a query.',
};

export default function GraphsLayout({ children }: { children: ReactNode }) {
  return <GraphsShell>{children}</GraphsShell>;
}
