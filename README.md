# Hermes Agent Lab — v13.3.2 Upload Ready

**Repositorio:** [`jorgektch/testing-hermes-agent`](https://github.com/jorgektch/testing-hermes-agent)

Esta es una entrega lista para **GitHub → Add file → Upload files**. No requiere ejecutar `apply.py`, parches, scripts, comandos de terminal ni migraciones. Los archivos de este ZIP son archivos **completos**, organizados desde la raíz del repositorio.

## Comportamiento de producción

1. El agente presenta las propiedades efectivamente coincidentes en líneas separadas: `1. H-204 (3 beds, $295k)` y `2. H-101 (3 beds, $320k)`. Una sola propiedad (H-201) se muestra sin `1.`.
2. El menú presentado se guarda con orden, revisión de búsqueda e ID del mensaje en el estado persistido en Turso (`last_presented_property_options`). El agente no inventa precios ni usa un shortlist viejo para descifrar ordinales.
3. Si el lead responde `The second option`, `Option 2`, `2` o `I choose the $320k house`, se resuelve **H-101** como `property_details`. No se hace nueva búsqueda, se mantienen preferencias, y no se reserva una visita.
4. Un menú nuevo sustituye al anterior; una referencia inválida o ambigua exige aclaración antes de cualquier acción.
5. Las respuestas preservan los saltos de línea reales en la proyección SMS, en los mensajes persistidos y en el historial de la interfaz. Los segmentos físicos GSM se calculan de manera independiente de la burbuja lógica.
6. Hermes sigue eligiendo y ejecutando sus herramientas. El adaptador solamente aplica contratos verificables de referencias, presentación y seguridad comercial; no usa otro modelo extractor.

## Arquitectura de integración

`backend/app/__init__.py` carga al inicio `backend/app/v1332_upload_ready.py`, que vincula los módulos ya existentes de `domain`, `commercial_turn`, `conversation_context`, `delivery_policy`, `sms` y `hermes_runtime`. Se conserva la implementación productiva v13.3 de los demás módulos. La integración es explícita en el arranque y no necesita modificar los archivos del servidor de forma dinámica.

## Pruebas

El archivo `backend/tests/test_v13_3_2_upload_ready.py` incluye los casos de selección ordinal, menú visible persistido, nuevo menú, privacidad de una cita en borrador, texto con saltos de línea y presentación sin ordinal de un único resultado. También deberían continuar pasando las pruebas preexistentes v13.3.

El ZIP no contiene credenciales, datos reales ni tareas automáticas. Los resultados con Hermes/OpenRouter y Turso reales necesitan comprobarse en tu despliegue después de subirlo.
