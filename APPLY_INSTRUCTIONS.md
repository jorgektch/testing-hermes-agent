# Hermes v13.2 — Diferencial para aplicación manual

**Sin PR, push, merge ni despliegue.** El ZIP contiene un **instalador diferencial con anclajes verificados** (no es un checkout completo). Debes aplicarlo sobre una copia local de `jorgektch/testing-hermes-agent` en la versión `main` **v13.1** previa a estos cambios. El script solo escribe archivos en el directorio local indicado y genera allí `v13_2_changes.patch` con un `git diff` unificado equivalente. No utiliza claves, conexión de red ni APIs.

## Procedimiento

1. Conserva una copia de seguridad y comprueba que `git status --short` no muestre cambios previos; si los hay, haz commit o stash primero.
2. Descomprime `hermes-v13.2-differential.zip` en una carpeta **fuera** del repositorio. Entra en la raíz de tu repositorio local.
3. Opcionalmente, valida primero el módulo independiente sin acceso al repo: `python -m pytest -q /ruta/al/hermes-v13.2-differential/tests/test_continuity_standalone.py` (24 casos). Después ejecuta la **prevalidación**; si falla, el script no habrá modificado nada.

```bash
# Desde la raíz del repositorio; ajusta la ruta al ZIP descomprimido:
python /ruta/al/hermes-v13.2-differential/apply.py --repo "$PWD" --dry-run
python /ruta/al/hermes-v13.2-differential/apply.py --repo "$PWD"

git diff --check
git status --short
PYTHONPATH=backend python -m pytest -q backend/tests
(cd frontend && npm ci && npm run build)
```

En **PowerShell**, usa la ruta absoluta del script y `--repo .`:

```powershell
py C:\ruta\hermes-v13.2-differential\apply.py --repo . --dry-run
py C:\ruta\hermes-v13.2-differential\apply.py --repo .
```

4. Revisa el contenido de `v13_2_changes.patch` y el `README.md` actualizado. `v13_2_changes.patch` es solo evidencia para inspección y **no** debe aplicarse nuevamente sobre los archivos ya transformados.
5. Si las pruebas pasan, prepara el commit de forma **manual**, usando el texto incluido en `COMMIT_MESSAGE.txt`:

```bash
git add README.md backend/app/commercial_continuity.py backend/app/commercial_turn.py \
  backend/app/conversation_context.py backend/app/domain.py \
  backend/app/delivery_policy.py backend/app/hermes_runtime.py backend/main.py \
  backend/tests/test_v13_2_continuity.py frontend/src/app/page.tsx
git commit -m "feat(hermes): preserve sales context across property searches and visits"
```

## Contenido del diferencial

| Archivo | Cambio |
|---|---|
| `apply.py` | Transformaciones con comprobación de anclajes, vista previa y generación de patch local |
| `backend/app/commercial_continuity.py` | Nuevo módulo: intención de calendario, contexto de visita, preferencias y excepciones |
| `backend/tests/test_v13_2_continuity.py` | Regresiones de las transiciones de H-204, 8 AM, selección parcial, URLs y filtros |
| `README.md` | Guía actualizada para negocio y arquitectura con diagramas Mermaid |
| `COMMIT_MESSAGE.txt` | Mensaje sugerido de commit en inglés |
| `tests/test_continuity_standalone.py` | Pruebas puras repetibles antes de modificar el checkout |

Los cambios de los otros siete archivos se aplican por medio de `apply.py`. No modifica las fuentes sintéticas del catálogo ni datos existentes de la base de datos. Mantiene el historial de conversaciones y citas ya guardado.

## Validación y límites

El diferencial superó una batería de pruebas **puras** de clasificación/continuidad y una simulación local de instalación en seco + aplicación sin red. **No se ha ejecutado toda la suite del repositorio ni la compilación Next.js con el checkout real**, porque el entorno de creación no tenía disponible una copia íntegra del repositorio. Debes ejecutar los comandos anteriores antes de utilizar la versión. Comprueba después con un lead **ficticio** el recorrido: $200k/2 dormitorios → alternativa H-204 → visita H-204 → otras fechas a las 8:00 → aceptación de las 9:00 sin día → fecha explícita → ubicación ilustrativa de Lima. Revisa los eventos de validación y los campos persistidos; no envíes SMS ni hagas citas físicas reales.
