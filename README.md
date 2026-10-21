# Backend KerERC

## Export de projet

La purge des archives expirées (statut `expire`, la ligne de journal est conservée) se lance depuis ce dossier :

```bash
python -m api.export.purge
```

À planifier une fois par jour dans Coolify. Le détail du format, des droits et de la variable `URL_FRONT` est dans `docs/EXPORT_PROJET.md`.
