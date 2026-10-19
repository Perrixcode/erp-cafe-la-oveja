# Privacidad, procedencia y publicación

## Compartir

- Fuente final del ERP saneada, manteniendo estructura y funcionalidades existentes.
- README y documentación contrastados con esa fuente.
- Pruebas reproducibles y fixtures explícitamente ficticios.
- Caso de estudio, arquitectura, decisiones y limitaciones verificables.
- Archivos de licencia o atribución de terceros que correspondan, sin reinterpretarlos como licencia global.

## Excluir

- Bases SQLite, respaldos, exports de conversaciones y catálogos privados.
- Credenciales, tokens, secretos, archivos de entorno privados, cookies y sesiones.
- Dominios/IP privados, rutas absolutas de equipos y configuraciones de producción.
- Nombres, correos, teléfonos, direcciones y otros datos reales de trabajadores o clientes.
- Boletas, comprobantes, fotos y firmas reales, incluidos archivos embebidos en HTML o fixtures.
- Capturas con datos reales y resultados de pruebas ajenos al artefacto final.
- Dependencias o assets de procedencia/licencia no confirmada.

## Revisión antes del commit

Revisar tanto archivos visibles como ocultos, archivos comprimidos, contenido embebido, logs y diffs. No basta con agregar patrones a .gitignore: un archivo ya versionado puede seguir publicándose. Evitar copiar el HTML autónomo y las referencias administrativas del simulador por defecto; seleccionar únicamente lo necesario y con procedencia autorizada.

Preservar el README principal, avisos existentes y el historial de Git. No reconstruir fechas ni adjudicar commits a una persona distinta. Si una credencial real ya estuviera publicada, detener su redistribución y avisar al responsable para revocación; no incluirla en el informe ni asumir que borrarla de un archivo la elimina del historial.

La licencia del proyecto continúa pendiente de elección. La licencia de una fuente tipográfica o dependencia no concede derechos sobre el resto del proyecto. Confirmar permiso para publicar todos los componentes antes de incorporar el export final.
