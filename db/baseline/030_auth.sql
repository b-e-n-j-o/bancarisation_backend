-- Triggers posés par KerERC sur auth.users (hors des schémas dumpés).
CREATE TRIGGER kererc_creer_profil AFTER INSERT ON auth.users FOR EACH ROW EXECUTE FUNCTION prive.creer_profil();

-- Bucket des exports complets (068), dans le schéma storage, hors du dump.
insert into storage.buckets (id, name, public, file_size_limit)
values ('exports-projet', 'exports-projet', false, 2147483648)
on conflict (id) do nothing;
