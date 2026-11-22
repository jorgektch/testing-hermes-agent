# Hermes v13.3.3 — Upload Ready

**Destino:** [jorgektch/testing-hermes-agent](https://github.com/jorgektch/testing-hermes-agent), rama `main`.

**Base de referencia inspeccionada:** `cccc628b87e3aff6204630c65737dd988ad9cf92` (v13.3.2). Esta entrega es un diferencial con archivos completos, NO un parche ni un instalador.

## Instrucciones para GitHub Upload files

1. Descarga y **descomprime** `hermes_v13_3_3_upload_ready.zip` **fuera del repositorio**.
2. Abre https://github.com/jorgektch/testing-hermes-agent/upload/main
3. Arrastra **el contenido interno de la carpeta descomprimida**, manteniendo exactamente las rutas `backend/app/`, `backend/tests/` y `docs/`.
4. Comprueba en GitHub que `backend/app/__init__.py`, `APPLY_INSTRUCTIONS.md`, `VERSION.txt` y `COMMIT_MESSAGE.txt` figuran como modificados; que los dos nuevos módulos `contextual_choices.py` y `v1333_contextual_choices.py`, la nueva prueba y la documentación figuran como añadidos.
5. Confirma el commit desde GitHub y revisa que se haya ejecutado la validación del despliegue.

**No ejecutes scripts** (`apply.py`, `installer_core.py`, `v1331_transforms.py`), `git apply`, comandos Python ni migraciones antes de subir. El paquete no requiere nuevas variables de entorno ni cambios SQL.

### Importante

- El ZIP **no** contiene la carpeta envolvente de release ni archivos de instalación. Las rutas comienzan directamente en la raíz del repositorio.
- GitHub Upload files debe preservar las carpetas. Si la vista previa muestra archivos Python directamente en la raíz, **no confirmes**; vuelve a arrastrar las carpetas desde el explorador de archivos de un navegador compatible.
- La integración presupone que v13.3.2 ya está desplegada. No incluye ni vuelve a subir sus archivos sin cambios.
- La validación de Hermes/OpenRouter/Turso en Preview queda pendiente, aunque se incluyen regresiones offline para la capa de opciones.

Consulta `docs/V13_3_3_CONTEXTUAL_CHOICES.md` para la arquitectura, los casos de prueba y los invariantes de consentimiento.
