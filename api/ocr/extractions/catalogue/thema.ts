import catalogue from './thema.json';

export type ThemaFamilleCode = 'C1' | 'C2' | 'C3';

export interface ThemaMesure {
  code: string;
  intitule: string;
}

export interface ThemaGroupe {
  id: string;
  label: string;
  mesures: ThemaMesure[];
}

export interface ThemaFamille {
  code: ThemaFamilleCode;
  label: string;
  groupes: ThemaGroupe[];
}

export interface ThemaCatalogue {
  version: string;
  source: string;
  familles: ThemaFamille[];
}

export const THEMA_CATALOGUE = catalogue as ThemaCatalogue;

export const THEMA_FAMILLE_STYLES: Record<ThemaFamilleCode, { bg: string; color: string; border: string }> = {
  C1: { bg: '#EAF3DE', color: '#27500A', border: '#C0DD97' },
  C2: { bg: '#E6F1FB', color: '#0C447C', border: '#B5D4F4' },
  C3: { bg: '#FAEEDA', color: '#633806', border: '#FAC775' },
};

export function getThemaFamilles(): ThemaFamille[] {
  return THEMA_CATALOGUE.familles;
}

export function getThemaFamille(code: string): ThemaFamille | undefined {
  return THEMA_CATALOGUE.familles.find((f) => f.code === code);
}

export function getMesuresByFamille(familleCode: string): ThemaMesure[] {
  const famille = getThemaFamille(familleCode);
  if (!famille) return [];
  return famille.groupes.flatMap((g) => g.mesures);
}

export function getMesureByCode(code: string): (ThemaMesure & { famille: ThemaFamilleCode; groupeLabel: string }) | null {
  for (const famille of THEMA_CATALOGUE.familles) {
    for (const groupe of famille.groupes) {
      const mesure = groupe.mesures.find((m) => m.code === code);
      if (mesure) {
        return { ...mesure, famille: famille.code, groupeLabel: groupe.label };
      }
    }
  }
  return null;
}

export function getFamilleFromCode(code: string): ThemaFamilleCode | null {
  const match = code.match(/^(C[123])/);
  return (match?.[1] as ThemaFamilleCode) ?? null;
}

/** Groupes pour un `<select>` avec optgroup (compat planning legacy). */
export function getThemaSelectGroups(): { group: string; options: { value: string; label: string }[] }[] {
  return THEMA_CATALOGUE.familles.flatMap((famille) =>
    famille.groupes.map((groupe) => ({
      group: `${famille.code} — ${famille.label} — ${groupe.label}`,
      options: groupe.mesures.map((m) => ({
        value: m.code,
        label: `${m.code} — ${m.intitule}`,
      })),
    })),
  );
}

export function formatThemaLabel(code: string): string {
  const mesure = getMesureByCode(code);
  if (!mesure) return code;
  return `${mesure.code} — ${mesure.intitule}`;
}
