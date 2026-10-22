-- Qualité « prestataire » : accès de dépôt, distinct du niveau lecteur / contributeur.
-- La règle qui limite l'écriture à une boîte de réception viendra ensuite.
-- 064 (invitation) ne change pas.

alter table bancarisation.projet_acces drop constraint if exists projet_acces_qualite_check;

alter table bancarisation.projet_acces add constraint projet_acces_qualite_check
  check (
    qualite is null
    or qualite in (
      'operateur',
      'maitre_ouvrage',
      'bureau_etudes',
      'controle',
      'ancien_proprietaire',
      'prestataire'
    )
  );
