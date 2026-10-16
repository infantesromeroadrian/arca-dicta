# Baseline operativo — F1.3.3

Fecha: 2026-10-16. Owner: coordinador raíz de ARCA Dicta. Aplicabilidad: **APLICA**,
porque la corrección se prepara para el adaptador local actualmente ejecutado.
Alcance autorizado: candidato aislado y activación local acotada de dos módulos.
Veredicto del baseline, activación y sondas sintéticas posteriores: **PASS**.
La prueba local de dictado y pegado quedó **CONFIRMADA POR EL USUARIO** a las
14:09 UTC, sin observación física independiente.

## Objetivo, fuentes y estados

| Estado | Evidencia y límite |
| --- | --- |
| Solicitado | F1.3.3 en el plan canónico: corregir falsos rechazos, conservar revisión obligatoria y distinguir causas sin filtrar contenido. Readback confirmado por raíz antes de implementar. |
| Configurado | STT nativo Groq; revisión Ollama Cloud `deepseek-v4.1-flash`; deadlines 4 s / 5 s; configuración SHA-256 `e0ec27133d046c68f104ebdaac6ca90a0bc9b5674d7a9488102ed51e28514339`. |
| Resuelto | Paquete instalado hyprwhspr `1.49.0-1`; sin resolución o instalación de dependencias nueva. |
| Baseline desplegado | Adaptador local fuera del checkout, identificado por raíz contra snapshot estricto de 22 archivos; servicio activo sin reinicios durante el diagnóstico. |
| Candidato comprobado | Corrección de `review.py` y `review_gate.py`, 120 pruebas offline y revisión protegida. No existe commit, build CI o release de este candidato. |
| Desplegado tras reparación | Únicamente ambos módulos activados en el target original; proceso nuevo active/running, cero reinicios automáticos y hashes comprobados. Config y otros 20 archivos intactos. |

La fuente primaria del despliegue es el runtime inspeccionado por raíz. El
snapshot recuperable es `20261016-handy-hyprwhspr/strict-review-1335/candidate`;
sus hashes relativos están en [source-inventory.json](../provenance/source-inventory.json).
El commit original desplegado no está verificado; se usan hashes de archivo,
sin sustituirlos por un SHA de Git inexistente.

El manifest previo del candidato F1 con corrección P2 era
`a8679c89065eef39a51d3f1dfb2021df079456c4ac96c6c6b783d431c2f4d717`.
Los cambios P2 de `main.py` y `test_config.py` se preservan; no se incluyen en
la activación de esta reparación. Remoto, publicación, merge y release: N/A.

## Baseline comprobado y comparación

| Comprobación | Resultado y procedencia |
| --- | --- |
| Contratos nativos | 12 hashes de core y 14 de HUD conservados respecto al snapshot. |
| Integridad de paquete | Raíz ejecutó `pacman -Qkk hyprwhspr`: 179 archivos, 0 alterados, exit 0 durante este turno. |
| Baseline offline | La extracción anterior registró 99 pruebas del snapshot y 116 del candidato F1/P2; son resultados previos, no una nueva ejecución de esta reparación. |
| Comportamiento real anterior | Sonda sintética breve aceptada en 0.796 s; sonda `Docker` revisada en 0.406 s y rechazada por el validador posicional. Sin pegado. |
| Regresión focal | 4 métodos nuevos, 30 subcasos fallidos antes del cambio. |
| Candidato offline | 120 pruebas PASS en 12.002 s; lint y hashes en [retained-review.md](retained-review.md). |
| Revisión independiente protegida | 26 pruebas focales PASS en 0.091 s, exit 0, sin defectos demostrados sobre manifest `c8be3bb3033cc2e3a28e7c4c00958722719e9a6cedf1657fafc9fa7e82efbd88`. |
| Sondas reales del candidato | Docker al inicio PASS 0.514 s; conector PASS 0.702 s. Una entrega simulada por caso, cero clipboard real, sin pendiente ni auto-submit. |
| Sondas reales posteriores | A las 14:04:53 UTC: Docker PASS 0.715 s; conector PASS 0.553 s. Misma frontera simulada; imports desde live y hashes verificados. |

Las pruebas usan la misma biblioteca nativa instalada y fixtures sintéticos.
No cambia modelo, endpoint, prompt, idioma, tokens máximos, deadlines, política
de retención, reintento manual, pegado nativo ni contratos de compatibilidad.
Solo se permiten el cambio de posición de literales intactos y la excepción
explícita de mayúscula de conectores, más diagnóstico de fallos.

## Gates y rollback

| Gate | Estado |
| --- | --- |
| Baseline desplegado y backup identificados | PASS por hashes y lectura de runtime. |
| Candidato aislado | PASS; hashes finales en manifest, comprobaciones en evidencia. |
| Revisión independiente protegida | PASS sobre candidato fijo; el código comprobado no cambió después. |
| Activación | PASS: servicio detenido, reemplazo atómico exclusivo de dos módulos con modos preservados e inicio del mismo target; marcador strict gate presente. |
| Sonda sintética posterior | PASS por raíz; sin portapapeles ni pegado real. |
| Recorrido físico de dictado | CONFIRMADO POR EL USUARIO: «Sí, se pega correctamente», tras la petición de probar Super+Espacio después de activar. Sin observación independiente. |

Ante fallo de arranque, drift de hashes o regresión demostrada, raíz restaura
ambos módulos desde el snapshot estricto verificado y reinicia el mismo servicio.
Después comprueba servicio, hashes y revisión sintética. La configuración y
las librerías nativas no necesitan rollback porque no se modifican. Reiniciar
pierde el pendiente en memoria: el usuario confirmó que ya no lo necesita.
No fue necesario ejecutar el rollback; raíz volvió a verificar sus 22 hashes
tras la activación. Las actualizaciones documentales posteriores cambian el
manifest completo, no los hashes de los dos módulos revisados y desplegados.

Limitaciones: no hay atribución probada del dictado histórico; persisten límites
heurísticos de equivalencia y nombres iniciales. Sin cambio arquitectónico ni
nuevo proveedor. Los paths personales, PID, secretos y logs privados se omiten.
F1.3.3 puede cerrarse por la confirmación local actual; siguen pendientes las
medidas físicas F1.3.2. Esta comprobación no acredita ambos destinos editor y
navegador, p50/p95, una segunda máquina ni preparación de alpha.
