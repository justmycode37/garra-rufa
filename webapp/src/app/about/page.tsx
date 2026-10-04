import type { Metadata } from 'next';
import Link from 'next/link';
import { ArrowLeft, ArrowUpRight, Heart, Microscope, Stethoscope } from 'lucide-react';
import { GarraMark } from '@/components/Brand';
import ReachingHands from '@/components/ReachingHands';
import FishSchool from '@/components/FishSchool';
import { ExploreLink } from '@/components/ConnectionExperience';
import styles from './page.module.css';

export const metadata: Metadata = {
  title: 'Our mission · garra rufa',
  description: 'Connecting rare disease knowledge, research, and lived experience so a question can become a way forward.',
};

export default function AboutPage() {
  return <div className={`${styles.page} mission-page`}>
    <a className="skip-link" href="#mission">Skip to mission</a>
    <header className={styles.header}>
      <ExploreLink className={`brand ${styles.brand}`} aria-label="Garra Rufa home"><GarraMark size={30}/><span>garra rufa<span className="brand-period">.</span></span></ExploreLink>
      <ExploreLink className={styles.back}><ArrowLeft size={15}/><span>Back to explore</span></ExploreLink>
    </header>
    <main id="mission">
      <div className={styles.art}><ReachingHands/></div>
      <section className={styles.intro} aria-labelledby="mission-title">
        <h1 id="mission-title" tabIndex={-1}>Our mission is to connect</h1>
        <p className={styles.lead}>Make rare disease knowledge easier to find, understand, and build on. Together.</p>
        <p className={styles.copy}>A question should be the start of a path. Garra rufa connects information with the people who need it, bringing research, clinical questions, and lived experience into a shared place to explore.</p>
      </section>
      <section className={styles.perspectives} aria-label="Three perspectives, one shared purpose">
        <article className={`${styles.roleCard} lilac`} aria-labelledby="researchers-title">
          <Microscope className={styles.roleIcon} size={28} strokeWidth={1.4} aria-hidden="true"/>
          <h2 id="researchers-title">For researchers</h2>
          <p>Follow the evidence, develop a question, and share what you discover.</p>
          <Link className={`primary ${styles.roleLink}`} href="/?entry=signup&role=researcher">Continue as researcher<ArrowUpRight size={16} aria-hidden="true"/></Link>
        </article>
        <article className={`${styles.roleCard} sea`} aria-labelledby="doctors-title">
          <Stethoscope className={styles.roleIcon} size={28} strokeWidth={1.4} aria-hidden="true"/>
          <h2 id="doctors-title">For doctors</h2>
          <p>Bring relevant knowledge and patient information together to support your next conversation.</p>
          <Link className={`primary ${styles.roleLink}`} href="/?entry=signup&role=doctor">Continue as doctor<ArrowUpRight size={16} aria-hidden="true"/></Link>
        </article>
        <article className={`${styles.roleCard} peach`} aria-labelledby="patients-title">
          <Heart className={styles.roleIcon} size={28} strokeWidth={1.4} aria-hidden="true"/>
          <h2 id="patients-title">For patients</h2>
          <p>Keep your story in one place and explore information you can discuss with your care team.</p>
          <Link className={`primary ${styles.roleLink}`} href="/?entry=signup&role=patient">Continue as patient<ArrowUpRight size={16} aria-hidden="true"/></Link>
        </article>
      </section>
      <section className={styles.nameStory} aria-labelledby="our-name-title">
        <h2 id="our-name-title">garra rufa<span>.</span></h2>
        <p className={styles.nameLead}>Small helpers. Stronger together.</p>
        <div className={styles.nameCopy}>
          <p>Garra rufa, also known as the <a href="https://www.seriouslyfish.com/species/garra-rufa" target="_blank" rel="noreferrer">doctor fish</a>, lives in groups. For us, this little fish is a reminder that helping one another can be a powerful thing.</p>
          <p>Some challenges are too big to face alone. Patients bring lived experience, doctors bring care, and researchers bring discovery. When we come together and share what we know, we can find a way forward. That’s why we’re garra rufa.</p>
        </div>
        <FishSchool/>
      </section>
    </main>
    <footer className={styles.footer}><span>garra rufa</span><span>A way forward, together.</span></footer>
  </div>;
}
