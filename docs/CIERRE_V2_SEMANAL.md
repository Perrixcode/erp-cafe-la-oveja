# Cierre digital V2 · prueba semanal

Adaptación del paquete aprobado `oveja-cierre-v2-migracion.zip`, SHA-256 `1433c04e919fa3743616fa6cbb0e57c535c4c0eb479802cb065ce2732d278528`, revisión del simulador `dab2ff0e24e5cbec02335047833c1e2a6a78599d`. La interfaz base es `cashier-v2.js` y su CSS de ese paquete. El adaptador `cashier-v2-server.js` utiliza las sesiones y API del ERP. No se incorpora el modelo del simulador, fixtures ni almacenamiento del navegador a producción.

## Operación

La cajera selecciona caja y día operativo. El responsable procede del acceso existente. Completa A por denominación y B para el siguiente turno, con B ≤ A en cada denominación. C = A − B; los movimientos ya incluidos en el conteo no se vuelven a aplicar. El día operativo es de lunes a viernes; un viernes puede conservar su sesión abierta durante el fin de semana sin atribuir una falta a la cajera.

Toteat se lee de la caché existente. Las fuentes ausentes, ambiguas o con cortes diferentes quedan pendientes. `collection.cash` no acredita efectivo esperado. MP se transcribe por terminal desde su impreso, sin exigir propinas separadas. La comparación necesita evidencia independiente del mismo terminal y corte. El total de diferencias no compensa diferencias de medios individuales.

Las fotos se adjuntan desde cámara o galería, se comprimen en la interfaz y se guardan como BLOB privado en la base del ERP. Cada tipo de impreso admite un motivo individual de respaldo pendiente. Guardar, avanzar de paso y el guardado automático envían datos al servidor; una respuesta incompleta se reintenta con el mismo identificador. El aviso de guardado requiere confirmación del servidor.

Antes de enviar, revisar el resumen, explicar diferencias comparables y usar «Volver a contar» si corresponde. La conformidad y firma dibujada se ligan al snapshot, versión, cuenta y hora del servidor. La firma no certifica identidad ni validez jurídica. Una corrección invalida la conformidad vigente y conserva las presentaciones anteriores; exige revisar y firmar de nuevo. Después del depósito, entrega o conteo se conserva la declaración custodiada para revisión administrativa.

El envío devuelve un folio y deja el depósito pendiente. Declarar depósito en caja fuerte, entregar físicamente, recibir y contar administrativamente son acciones distintas. El número de bolsa aparece junto al QR; no se limita a una entrega por día y caja. El PDF se muestra en vista previa antes de descargar o imprimir.

La administración puede adjuntar evidencia independiente posterior y registrar una comparación separada. Una sesión inequívoca conocida después puede completar la comparación; nunca modifica la declaración firmada. Las referencias de efectivo requieren apertura, ventas netas antes de movimientos y movimiento neto verificados, con respaldo y corte exacto.

## Correspondencia de requisitos y evidencia

| Requisito | Implementación | Prueba |
|---|---|---|
| Entrada simple y múltiples entregas | API V2, identidad desde sesión, identificador por entrega | `test_multiple_deliveries_same_day_register_and_empty_is_not_zero` |
| A/B por denominación y bolsa A−B | Normalización estricta; proyección V2 separada de cálculo histórico | `test_b_subset_strict_integers_unknown_and_negative_rejected`, `test_bag_is_A_minus_B_no_double_movement_and_legacy_history_kept` |
| Toteat automático y pendiente explícito | `source_summary`, sesión seleccionada por servidor | `test_source_auto_readonly_collection_cash_never_expected`, `test_ambiguous_session_keeps_pending` |
| MP por terminal sin propinas obligatorias | Declaración por terminal; referencia de corte independiente | `test_terminal_zero_unknown_duplicates_and_no_required_tips`, `test_terminal_differences_cannot_cancel_and_admin_preserves_each` |
| Fotos y motivos individuales | Validación de bytes, dimensiones, hash, pertenencia y ACL | `test_photo_durable_digest_metadata_ownership_and_content_validation`, `test_missing_individual_evidence_reason_blocks_review` |
| Resumen, comentario y recuento | Cinco pasos de interfaz aprobada; validación servidor | `test_cashier_v2_ui.cjs`, pruebas de diferencias por terminal y efectivo |
| Firma/conformidad/versiones | Token de revisión, hash canónico, transacción atómica, tablas inmutables | `test_review_invalidated_after_edit_and_revert_unchanged_save_preserves`, `test_correction_new_signature_preserves_previous_snapshot`, `test_metadata_tamper_rejected_even_with_unchanged_snapshot` |
| Depósito separado de custodia | Evento `deposit_safe`; entrega/recepción/conteo existentes | `test_deposit_separate_idempotent_no_reception_and_blocks_correction`, `test_custody_legacy_actions_keep_signed_amount_and_prevent_old_mutation` |
| Folio, aviso y ayuda del número | Interfaz aprobada y respuesta confirmada | Recorrido interfaz/API y pruebas HTTP |
| Vista previa antes de descargar | PDF privado de snapshot, con firma y fotos | `test_get_readonly_and_pdf_no_mutation`, recorrido interfaz/API |
| Persistencia y recuperación | SQLite existente, BLOB, idempotencia y respaldo ampliado | `test_backup_restore_includes_signatures_photos_and_events`, `test_concurrent_submit_idempotent_and_key_payload_mismatch_rejected` |
| Comparación tardía sin modificar firma | Evidencia posterior y eventos de comparación separados | `test_late_source_resolves_missing_session_without_rewriting_signature`, `test_late_reference_photo_requires_admin_and_preserves_signed_photos` |
| Calendario y permisos existentes | Reutiliza reglas y ACL vigentes | `test_weekend_not_new_operational_day_and_friday_remains_open`, pruebas de permisos y HTTP |

## Verificación reproducible y límites

Con las dependencias del ERP instaladas: `PYTHONPATH=tests:. python -B -m unittest test_cashier_v2 test_cashier_v2_http -q`. Para interfaz/API: `ERP_TEST_PYTHON=python node tests/test_cashier_v2_ui.cjs`. Los fixtures usan directorios temporales y cuentas sintéticas; nunca se ejecutan contra la base productiva. Las suites previas de caja, revisión, Toteat, permisos y respaldos siguen incluidas en la regresión.

La comprobación de JavaScript usa DOM mínimo y una API HTTP real aislada, sin abrir navegador. No acredita inspección visual de dispositivos, cámara física, firma con dedo ni impresora. Estas acciones físicas quedan para la prueba semanal autorizada.

## Persistencia, despliegue y reversión

Migración aditiva: siete tablas `cv2_*`, índices y protecciones de evidencia inmutable. No transforma filas históricas, cuentas, permisos ni libros financieros. La evidencia se conserva dentro de la base ya respaldada; la verificación de respaldo comprueba firma, snapshot y hashes de fotos.

El despliegue exige huellas de código/configuración sin cambios concurrentes, pruebas aisladas en el runtime del servidor, respaldo restaurado y verificado, ensayo idempotente de migración y reversión. Solo se reinician ERP web y lector. Las comprobaciones productivas son de lectura: salud, autenticación requerida, hashes de assets, integridad y preservación de datos; no se crean cierres ni movimientos de prueba.

La reversión conserva la base. Antes de que exista evidencia V2 se puede volver al código anterior. Si ya existe evidencia V2, se utiliza la versión compatible pausada, que conserva lectura, firma, PDF e históricos y bloquea nuevas operaciones V2. No debe usarse código antiguo para recalcular nuevas declaraciones V2 ni restaurarse encima de datos posteriores.

## Ampliación administrativa aprobada

La bandeja V2 muestra causas y próxima acción: respaldo ausente o ilegible, respaldo posterior por revisar, fuente de efectivo pendiente, corte de terminal no comparable, depósito pendiente, recepción pendiente y conteo pendiente. No clasifica el cierre administrativo tardío o de fin de semana como fallo de la cajera. La evidencia posterior se consulta junto a la declaración original, sin reemplazarla.

La revisión administrativa tiene actor, fecha, versión ERP y versión/hash de presentación. Las nuevas evidencias relevantes invalidan la vigencia de esa revisión y dejan pendiente una nueva constancia humana. Firmar, comentar o comparar no inserta una revisión ni acepta una diferencia. Una revisión humana puede dejar fuentes pendientes; los importes desconocidos siguen sin convertirse en cero.

Pruebas adicionales: `test_admin_pending_human_review_version_and_late_evidence`, `test_signature_comment_and_comparison_do_not_mark_admin_reviewed` y la prueba de interfaz de bandeja V2. La integración conserva las fuentes históricas por medio, pero el cálculo V2 por terminal no las utiliza para reemplazar la comparación firmada. `all_methods_total` permanece desconocido: el total MP no se presenta como total de ventas.
