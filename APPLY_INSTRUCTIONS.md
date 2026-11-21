# Hermes v13.3.1 — Aplicación local

**Destino exacto:** `jorgektch/testing-hermes-agent`, commit `ce58c928bacbf7621f252aaceb3acac4e06f9ca5` (v13.3). No crea PR, no hace push y no interactúa con Vercel ni OpenRouter.

## Por qué es un diferencial nuevo

En v13.3 se incorporó `backend/app/property_options.py` y su batería de pruebas, pero **no se aplicaron las transformaciones de `apply.py`** a los módulos que lo utilizan. Por ello seguía ejecutándose el formateador antiguo y «The second option» activaba `search_response_recovery`. Esta entrega integra los módulos existentes y actualiza el formato compacto solicitado.

## Aplicación

1. Descarga el ZIP y descomprímelo **fuera del repositorio**. Usa una copia de trabajo limpia en el commit v13.3. El instalador comprueba el SHA de Git y que no existan cambios sin guardar.
2. Desde la raíz del repositorio ejecuta (ajusta la ruta al ZIP):

```bash
python /ruta/hermes_v13_3_1_differential/apply.py --repo "$PWD" --dry-run
python /ruta/hermes_v13_3_1_differential/apply.py --repo "$PWD"

git diff --check
PYTHONPATH=backend python -m pytest -q backend/tests
(cd frontend && npm ci && npm run build)
```

En **PowerShell**:

```powershell
py C:\ruta\hermes_v13_3_1_differential\apply.py --repo . --dry-run
py C:\ruta\hermes_v13_3_1_differential\apply.py --repo .
git diff --check
$env:PYTHONPATH='backend'; py -m pytest -q backend/tests
cd frontend; npm ci; npm run build
```

El instalador produce `v13_3_1_changes.patch` **solamente para inspección**. No lo apliques una segunda vez: los cambios ya están escritos en los archivos. No incluyas dicho patch de inspección en el commit.

## Cambios incluidos

| Archivo | Efecto |
|---|---|
| `backend/app/commercial_turn.py` | La selección ordinal tiene prioridad sobre la búsqueda y puede usar el menú visible |
| `backend/app/conversation_context.py` | Mapea número, ordinal, código o precio al orden real presentado |
| `backend/app/domain.py` | Guarda menú y foco; convierte selección en Details sin crear búsqueda/cita |
| `backend/app/delivery_policy.py` | Recupera selección equivocada; emite menú compacto verificable |
| `backend/app/hermes_runtime.py` | Instrucciones y contexto explícito para Hermes/LLM |
| `backend/app/sms.py` | Preserva saltos de línea en mensajes extensos |
| `backend/app/property_options.py` | Menús de 1 o varias propiedades y verificación de importes |
| `backend/tests/test_v13_3_option_selection.py` | Pruebas integradas, incluido el caso real «The second option» |
| `frontend/src/app/globals.css` | Estilos de listas y párrafos |
| `README.md` | Describe cómo se ejecuta realmente la selección dentro de arquitectura y grafo |

## Validación funcional en Preview (sin leads reales)

1. `I am looking for a 2-bedroom house` → **H-201 (2 beds, $245k)**, sin `1.`.
2. `Do you have any with 3 bedrooms?` → exactamente **`1. H-204 (3 beds, $295k)`** y **`2. H-101 (3 beds, $320k)`**, en líneas diferentes.
3. `The second option` → `property_details`, H-101, sin `search_response_recovery`, sin nueva sesión de búsqueda ni cita.
4. `2`, `Option 2`, `I choose the $320k house` → mismo resultado si el menú previo sigue vigente.
5. Nueva búsqueda reemplaza menú previo; una referencia ordinal no disponible solicita aclaración.
6. La preferencia principal de dormitorios y presupuesto permanece igual; elegir una casa no significa confirmar una visita.

**Validación realizada en el paquete:** 42 pruebas locales (lógica pura y aplicación simulada). **Pendiente en tu entorno:** ejecutar la suite completa del backend, compilar Next.js y probar el agente real (OpenRouter/Turso), porque aquí no se dispone de un checkout integral ejecutable.
