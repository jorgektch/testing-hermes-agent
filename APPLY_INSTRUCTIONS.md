# Hermes v13.3 — Instalación diferencial manual

**Destino:** `jorgektch/testing-hermes-agent`, con el diferencial **v13.2 ya aplicado**. No ejecuta despliegues, no crea PR y no accede a GitHub, Vercel ni OpenRouter. Los cambios son locales y requieren tu commit manual.

## Contenido

| Archivo | Propósito |
|---|---|
| `apply.py` | Instalador incremental con preflight de anclajes, validación de sintaxis y patch unificado local |
| `backend/app/property_options.py` | Opciones presentadas, ordinales, código/precio, listas con saltos de línea y recuperación segura |
| `backend/tests/test_v13_3_option_selection.py` | Regresiones para el recorrido sintético de búsqueda, opción 2 y detalles |
| `tests/` | Pruebas independientes del nuevo módulo y del instalador (sin conexión) |
| `README.md` | Vista previa del README técnico-comercial actualizado; el instalador actualiza el README existente |
| `frontend/src/app/globals.css` (modificado por el instalador) | Conserva los saltos de línea dentro de los elementos Markdown del chat |
| `COMMIT_MESSAGE.txt` | Propuesta de commit en inglés |

## Antes de aplicar

1. Trabaja desde la raíz de una copia local de tu repositorio. Comprueba que el checkout ya incluye `backend/app/commercial_continuity.py` (introducido por **v13.2**) y que no hay cambios sin guardar: `git status --short`.
2. Extrae este ZIP **fuera** del repositorio, por ejemplo en `~/Downloads/hermes-v13.3-differential`.
3. Ejecuta las pruebas autónomas y el **dry-run** antes de modificar archivos:

```bash
python -m pytest -q ~/Downloads/hermes-v13.3-differential/tests
python ~/Downloads/hermes-v13.3-differential/apply.py --repo "$PWD" --dry-run
```

4. Si el preflight es correcto, aplica y revisa el diferencial:

```bash
python ~/Downloads/hermes-v13.3-differential/apply.py --repo "$PWD"
git diff --check
git status --short
git diff -- README.md backend/app frontend/src/app
```

El instalador deja `v13_3_changes.patch` en la raíz como **copia de revisión**. **No apliques ese patch otra vez**: los cambios ya estarán escritos por `apply.py`.

### Windows / PowerShell

```powershell
py C:\ruta\hermes-v13.3-differential\apply.py --repo . --dry-run
py C:\ruta\hermes-v13.3-differential\apply.py --repo .
```

## Validación completa en tu checkout

```bash
PYTHONPATH=backend python -m pytest -q backend/tests
(cd frontend && npm ci && npm run build)
python -m compileall -q backend/app backend/tests
```

Comprueba manualmente usando **solo leads sintéticos**:

1. `I am looking for a 2-bedroom house` → una ficha H-201, **sin numeración**.
2. `Do you have any with 3 bedrooms?` → opciones `1. H-204` y `2. H-101`, en **líneas separadas** y con disponibilidad.
3. `Option 2 its ok for me` → fase **Details**, H-101 (3 dormitorios, USD 320,000), sin nueva búsqueda H-201 ni agenda.
4. `The first one`, `The $320k house`, `I prefer H-101` → referencia según menú presentado.
5. Una búsqueda nueva reemplaza el menú antiguo. Una búsqueda sin resultados lo invalida; un ordinal ambiguo solicita el código en vez de adivinar.
6. Las preferencias principales de dormitorios/presupuesto y cualquier cita existente se mantienen.

**Alcance de la validación entregada:** 38 pruebas autónomas del módulo y del instalador; sintaxis Python de los nuevos archivos y del patch generado sobre fuentes representativas. **No** se ejecutó la suite de integración completa ni la compilación de Next.js sobre un checkout real de la versión v13.2; haz ambas antes de incorporar el commit.

## Commit manual en inglés

```bash
git add README.md \
  backend/app/property_options.py backend/app/commercial_turn.py \
  backend/app/conversation_context.py backend/app/domain.py \
  backend/app/delivery_policy.py backend/app/hermes_runtime.py \
  backend/app/sms.py backend/tests/test_v13_3_option_selection.py \
  frontend/src/app/globals.css

git commit -m "fix(hermes): preserve numbered property selections and readable listings"
```

No incluyas `v13_3_changes.patch` en el commit (es una copia local de revisión).
