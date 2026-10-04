'use client';

import { useEffect, useId, useState } from 'react';
import { diseases } from '@/lib/knowledge';
import type { ConditionOption } from '@/lib/types';
import styles from './ConditionPicker.module.css';

type Props = {
  defaultValue?: string;
  defaultName?: string;
  disabled?: boolean;
  label?: string;
  required?: boolean;
  createHint?: string;
  onValueChange?: (value: { diseaseId: string; conditionName: string }) => void;
};

const seedOptions: ConditionOption[] = diseases.map(disease => ({
  id: disease.id,
  name: disease.name,
  aliases: [disease.id, disease.shortName],
  curated: true,
  memberCount: 0,
  postCount: 0,
}));

function normalize(value: string) {
  return value.normalize('NFKC').toLocaleLowerCase('en').replace(/[’'`]/g, '').replace(/\+/g, ' plus ').replace(/[^\p{L}\p{N}]+/gu, ' ').trim().replace(/\s+/g, ' ');
}

export default function ConditionPicker({
  defaultValue = '',
  defaultName = '',
  disabled = false,
  label = 'Related condition',
  required = false,
  createHint = 'A community for this condition will be created when you save.',
  onValueChange,
}: Props) {
  const inputId = useId();
  const [options, setOptions] = useState<ConditionOption[]>(seedOptions);
  const [query, setQuery] = useState(() => seedOptions.find(option => option.id === defaultValue)?.name || defaultValue || defaultName);
  const [edited, setEdited] = useState(false);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const response = await fetch('/api/conditions', { signal: controller.signal });
        if (!response.ok) throw new Error('Could not load conditions.');
        const data: { conditions: ConditionOption[] } = await response.json();
        if (!Array.isArray(data.conditions)) throw new Error('Could not load conditions.');
        setOptions(data.conditions);
      } catch {
        if (!controller.signal.aborted) setLoadError(true);
      }
    }
    void load();
    return () => controller.abort();
  }, []);

  const existing = options.find(option => option.id === defaultValue);
  const displayValue = !edited && existing ? existing.name : query;
  const normalized = normalize(displayValue);
  const match = normalized ? options.find(option => [option.name, option.id, ...(option.aliases || [])].some(alias => normalize(alias) === normalized)) : undefined;
  // Keep an existing relationship intact while its name loads, or if the list is unavailable.
  const diseaseId = match?.id || (!edited ? defaultValue : '');
  const conditionName = !diseaseId ? displayValue.trim() : '';
  const isNew = !!conditionName;
  const hint = isNew ? createHint : loadError && !disabled ? 'Suggestions are unavailable. You can still enter a condition.' : '';

  useEffect(() => {
    onValueChange?.({ diseaseId, conditionName });
  }, [diseaseId, conditionName, onValueChange]);

  return <div className={styles.field}>
    <label htmlFor={inputId}>{label}</label>
    <input
      id={inputId}
      className={styles.input}
      list={`${inputId}-options`}
      value={displayValue}
      onChange={event => { setEdited(true); setQuery(event.target.value); }}
      placeholder="Search or enter a condition"
      autoComplete="off"
      minLength={2}
      maxLength={120}
      required={required}
      disabled={disabled}
      aria-describedby={hint ? `${inputId}-hint` : undefined}
    />
    <datalist id={`${inputId}-options`}>
      {options.map(option => <option key={option.id} value={option.name} />)}
    </datalist>
    <input type="hidden" name="diseaseId" value={diseaseId} disabled={disabled} />
    <input type="hidden" name="conditionName" value={conditionName} disabled={disabled} />
    {hint && <p className={styles.hint} id={`${inputId}-hint`} role="status">{hint}</p>}
  </div>;
}
