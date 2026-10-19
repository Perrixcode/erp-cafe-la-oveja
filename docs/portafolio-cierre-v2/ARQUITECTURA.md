# Arquitectura y decisiones de Cierre V2

## Integración actual

La interfaz aprobada del simulador se adapta mediante `static/cashier-v2-server.js` a la API autenticada del ERP. `erp/cashier_v2.py` valida y recalcula; SQLite conserva borradores, fotos, presentaciones, firma y eventos. No se incorpora IndexedDB, adaptador simulado ni fixtures al flujo productivo.

La migración agrega siete tablas `cv2_*` y protecciones de evidencia inmutable. Las cuentas, permisos, históricos y mecanismos existentes de custodia se conservan. Las fotos son BLOB privados; los accesos requieren autorización. Los tokens de revisión y solicitudes idempotentes pertenecen al servidor.

## Decisiones relevantes

### Efectivo y bolsa

B debe estar contenido en A por denominación, no solo en el total. La bolsa es A menos B. Una fuente ambigua de cobros no se convierte automáticamente en efectivo esperado, ni se descuentan movimientos dos veces.

### Desconocido no equivale a cero

Un valor sin evidencia permanece pendiente. Cero solo significa cero cuando fue declarado o verificado explícitamente. El modelo rechaza cantidades negativas, fraccionarias o fuera de rango seguro.

### Comparación con alcance verificable

Terminal, caja, sesión, día, período y base de cálculo deben corresponder. No se reparte un agregado entre terminales por estimación. Las diferencias permanecen separadas por medio; la falta de datos no atribuye culpabilidad.

### Revisión e integridad

La firma dibujada vincula una instantánea, versión y conformidad. Editar la declaración invalida su firma vigente. Una comparación tardía agrega evidencia sin reescribir retroactivamente la declaración confirmada. Los hashes canónicos, validación de metadatos y controles transaccionales permiten comprobar la consistencia de la evidencia guardada. No acreditan identidad ni ofrecen una garantía criptográfica frente a quien controle el servidor.

### Custodia explícita

Presentar, declarar depósito, entregar, recibir y contar son hechos diferentes. El servidor impide corregir una declaración después del depósito, entrega o conteo mediante el flujo ordinario. Las versiones anteriores y eventos permanecen consultables.

### Persistencia y recuperación

El guardado requiere confirmación del servidor. Se preserva el identificador al reintentar una respuesta perdida. Los respaldos existentes se amplían con comprobación de firma, snapshot y bytes de fotos. La reversión compatible pausa operaciones V2 y conserva lectura del historial; no restaura una base encima de operaciones posteriores.

### Administración

Una comparación calculada no equivale a revisión humana. La constancia administrativa conserva autor, fecha, versión ERP y hash de presentación. Fuentes posteriores invalidan su vigencia sin cambiar la firma. La bandeja identifica fotos pendientes, cortes no comparables y custodia incompleta con acciones explícitas.

## Límites comprobados

No se realizó validación visual con navegador, cámara física, firma con dedo ni impresora. La integración se comprobó desde terminal con un DOM mínimo y una API HTTP real aislada. Las fuentes faltantes y períodos ambiguos continúan pendientes. La firma dibujada no certifica identidad ni validez jurídica.
