-- Invitation d'un partenaire sans adhésion, et projets ouverts dès l'envoi.
-- role devient nullable (partenaire). type et projets décrivent l'invitation.

alter table bancarisation.invitation
  drop constraint if exists invitation_role_check;

alter table bancarisation.invitation
  alter column role drop not null;

alter table bancarisation.invitation
  add constraint invitation_role_check
  check (role is null or role in ('admin', 'membre'));

alter table bancarisation.invitation
  add column if not exists type text not null default 'bureau'
  check (type in ('bureau', 'partenaire'));

alter table bancarisation.invitation
  add column if not exists projets jsonb not null default '[]'::jsonb;
