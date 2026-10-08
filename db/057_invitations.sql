-- Invitations : pas de statut stocké (dérivé à la lecture).
-- Aucune policy : lecture et écriture uniquement via le module admin (rôle Postgres privilégié).

create table if not exists bancarisation.invitation (
  id               uuid primary key default gen_random_uuid(),
  email            text not null,
  organisation_id  uuid not null references bancarisation.organisations(id) on delete cascade,
  role             text not null check (role in ('admin', 'membre')),
  portee           text not null default 'entite' check (portee in ('entite', 'branche')),
  utilisateur_id   uuid references auth.users(id) on delete set null,
  invite_par       uuid references auth.users(id) on delete set null,
  nouveau_compte   boolean not null,
  revoquee_le      timestamptz,
  created_at       timestamptz not null default now()
);

create index if not exists invitation_organisation_idx
  on bancarisation.invitation (organisation_id);
create index if not exists invitation_email_idx
  on bancarisation.invitation (lower(email));

alter table bancarisation.invitation enable row level security;
