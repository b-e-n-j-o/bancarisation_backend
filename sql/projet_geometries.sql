create table if not exists bancarisation.projet_geometries (
  id uuid primary key default gen_random_uuid(),
  projet_id uuid not null references bancarisation.projets(id) on delete cascade,
  document_id uuid null references bancarisation.documents(id) on delete set null,
  nom text not null,
  feature_index integer not null default 0,
  geometry_type text null,
  geometry_geojson jsonb not null,
  properties jsonb not null default '{}'::jsonb,
  source_fichier text null,
  created_at timestamptz not null default now()
);

create index if not exists projet_geometries_projet_id_idx
  on bancarisation.projet_geometries (projet_id);

create index if not exists projet_geometries_document_id_idx
  on bancarisation.projet_geometries (document_id);

create index if not exists projet_geometries_geometry_geojson_gin_idx
  on bancarisation.projet_geometries using gin (geometry_geojson);
