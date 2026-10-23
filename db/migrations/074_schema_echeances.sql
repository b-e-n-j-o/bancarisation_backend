alter table bancarisation.echeance
  add column statut text not null default 'a_confirmer',  -- a_confirmer | validee
  add column origine text not null default 'ia',           -- ia | manuelle
  add column hypothese text,
  add column modifie_le timestamptz,
  add column modifie_par uuid;
alter table bancarisation.echeance alter column confiance set default 1;
alter table bancarisation.occurrence add column fige boolean not null default false;
-- protège les occurrences touchées à la main
update bancarisation.occurrence set fige = true
where date_realisation is not null or commentaire is not null or modifie_le is not null or origine <> 'ia';