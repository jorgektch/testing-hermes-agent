# v13.3.2 — GitHub Upload Ready

**No ejecutes `apply.py` ni `installer_core.py` ni `v1331_transforms.py`.** Estas son instrucciones de subida, no un instalador.

1. Descomprime el archivo `hermes_v13_3_2_upload_ready.zip` en tu equipo.
2. Entra a https://github.com/jorgektch/testing-hermes-agent/upload/main
3. Arrastra **los archivos y carpetas que están dentro del ZIP** a la zona de subida; deben conservarse las rutas `backend/app/...`, `backend/tests/...`, `README.md`, `VERSION.txt` y `APPLY_INSTRUCTIONS.md`. No subas una carpeta envolvente con nombre `hermes_v13_3_2_upload_ready`.
4. Comprueba en GitHub que `backend/app/property_options.py` y `backend/app/__init__.py` aparecen como modificados, mientras que `backend/app/v1332_upload_ready.py` y `backend/tests/test_v13_3_2_upload_ready.py` aparecen como nuevos.
5. Realiza el commit desde la propia interfaz y comprueba la nueva implementación en el despliegue.

No se requieren terminal, parches, scripts, cambios de base de datos ni variables nuevas.

**Nota:** si el cargador de GitHub muestra únicamente nombres de archivo sin sus carpetas, detén el commit y utiliza la subida de carpeta desde el navegador compatible, para evitar colocar los archivos en la raíz por error.
