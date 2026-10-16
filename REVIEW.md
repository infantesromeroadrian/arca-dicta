# Revisión del dictado antes de pegar

La extensión local impone revisión satisfactoria antes de copiar o pegar.
`review_gate.py` sustituye la preparación y recuperación del inyector, ejecuta
`review-hook` y utiliza el pegado nativo únicamente después de validar una
respuesta completa. Groq Whisper continúa haciendo STT. No se añade Enter.

Si falla la revisión, conserva **un dictado pendiente en memoria**, separado del
último texto ya revisado. El HUD muestra «Dictado retenido» y se emite un aviso.
**Super+Espacio reintenta el pendiente** en un único hilo acotado, sin
iniciar otra grabación ni bloquear el canal de cancelación. `stop`/`release`
por FIFO en los modos `auto` y `toggle` también finalizan en un único hilo: se conserva la parada nativa,
y una captura nueva queda bloqueada hasta terminar audio, STT y revisión.
Push-to-talk conserva el despacho nativo síncrono y su semántica de enclavado. `record
copy-last` y `record paste-last` también lo revisan antes de entregar; `record
clear-last` lo descarta. Cancelar o cerrar invalida resultados de una revisión
en vuelo, incluso si el STT todavía no ha devuelto texto. Una generación de
la captura se comprueba antes de crear el pendiente; un resultado cancelado
no puede iniciar la revisión ni entregarse. No hay reintentos automáticos ni historial; reiniciar pierde el
pendiente. Un límite de entrada o una configuración incorrecta muestran un
aviso específico: repetir la misma petición no corrige esos problemas.

La intensidad elegida es **más fluida**: corregir ortografía y puntuación,
quitar muletillas y repeticiones accidentales y mejorar moderadamente la
redacción. Una única revisión recibe la instrucción de conservar el idioma
original de cada fragmento: español, inglés o mezcla de ambos, sin traducir
ni unificar el idioma. No
requiere perfiles ni modifica la autodetección del STT. El prompt exige
conservar intención, información, cifras, negaciones y persona gramatical;
mantener literalmente nombres, marcas, términos técnicos, rutas, comandos e
identificadores, sin traducirlos ni castellanizarlos. Las
preguntas y órdenes dictadas se editan como texto; no se contestan ni ejecutan.
El modelo puede conservar una repetición que interprete como énfasis.

## Configuración y reversión

La configuración exigida por el gate es:

```json
"post_transcription_hook": "exec '/absolute/path/to/ARCA Dicta/review-hook'"
```

El `exec` identifica el hook esperado. El gate lo ejecuta directamente, sin
shell, con un límite externo de 5 s. Si la configuración falta o cambia, el
daemon bloquea la entrega; si cambian los contratos privados de hyprwhspr, el
launcher no arranca por una ruta permisiva. Para revertir hay que restaurar el
conjunto local respaldado en una activación posterior expresamente autorizada;
quitar únicamente el hook ya no omite la revisión. La extracción F1 mantuvo el
servicio; la reparación F1.3.3 activó únicamente los dos módulos de revisión y
reinició el mismo servicio, conservando su configuración. La garantía corresponde al daemon adaptado, no al CLI nativo
invocado directamente.

La credencial se hereda del servicio: `OLLAMA_API_KEY`, o como alternativa
`OLLAMA_CLOUD_API_KEY`. No se lee de otros proyectos ni se copia a archivos.
La API se invoca directamente en `https://ollama.com/api/chat`, modelo
`deepseek-v4.1-flash`, sin sufijo `:cloud`, con `think=false`, `stream=false`,
temperatura 0 y salida estructurada `{text: string}`. No se siguen
redirecciones HTTP ni se envían claves a otro origen.

## Límites y comportamiento ante fallos

- Presupuesto total de 4 segundos desde la lectura de stdin, incluidos HTTP,
  validación y salida. El inyector nativo mantiene su límite adicional de 5 s.
- Entrada máxima de 8192 bytes UTF-8; respuesta HTTP de 32768 bytes; texto de
  salida de 12288 bytes y máximo de generación de 512 tokens. Un dictado largo
  puede superar estos límites y queda retenido con un aviso.
- Sin clave, timeout, error, respuesta vacía, JSON mal formado, generación
  truncada o revisión rechazada: el hook termina con código distinto de cero.
  Fallos al ejecutar el hook, timeout externo, stdout vacío o parcial también
  bloquean la entrega. Nunca se copia ni pega el original como fallback.
- Una respuesta válida idéntica al original sí cuenta como revisada: el hook
  devuelve ese texto y código 0. La entrega ocurre una sola vez.
- El pendiente no reemplaza `_last_text`. Si la revisión funciona y falla el
  pegado, la recuperación nativa conserva el texto ya revisado. Si hay un
  pendiente nuevo, copy/paste-last lo revisan antes de usar el portapapeles.
- Locks y generaciones serializan entrega y descarte. Clear/cancel invalida
  una revisión aún en vuelo; no puede retirar un pegado que ya ha comenzado.
- Se contrastan secuencia de cifras, literales técnicos y nombres con
  mayúscula detectables, presencia de negaciones y marcadores de preguntas o
  peticiones, solapamiento léxico y límites de expansión. Se permiten cambios
  de palabras y eliminación de repeticiones; no se exige una secuencia idéntica.
- Los literales protegidos se buscan en todos los tokens del resultado, con
  el mismo caso, aunque cambien de posición. Por ejemplo, quitar «eh» antes
  de «Docker» permite que la marca pase al principio sin considerarla perdida.
  Al unir frases solo se permite poner en minúscula un conector que estaba
  al inicio de una frase: Ahora, Entonces, Además, También, Después, Pero,
  Now, Then, However, Also o But. Debe seguir presente la misma palabra;
  acrónimos, cifras, rutas e identificadores conservan su protección exacta.
- Las guardas son heurísticas: pueden rechazar una mejora correcta y no
  garantizan equivalencia semántica. Un cambio sutil de alcance de una negación,
  una omisión, un nombre dictado en minúsculas o un nombre Titlecase al principio
  del original puede escapar a ellas. Un nombre ambiguo que coincida con uno
  de los conectores anteriores también puede admitir ese cambio de caso. La
  muestra sintética comprueba casos concretos, no calidad general.
- Solo se registra un estado y un código fijo de fallo de revisión, por ejemplo
  `[REVIEW] state=review_failed reason=timeout`. No se registran contenido,
  audio, claves, cuerpos HTTP ni mensajes de excepciones, ni se añade historial.
  Cada revisión envía el texto a Ollama Cloud y consume la cuota del plan. No
  se ha medido coste monetario ni saldo disponible; tampoco se hace un
  reintento automático que duplique consumo o espera.

El hook y el gate conservan estos códigos sin copiar texto fallido a la salida:

| Salida | Motivo | Aviso y acción |
| --- | --- | --- |
| 0 | Revisión validada | Entregar una vez. |
| 1 | `internal-error` | Fallo interno; reintento manual. |
| 2 | `input-limit` | Entrada demasiado larga; descartar o reducir en otra captura. |
| 3 | `invalid-input` | Entrada no admisible; descartar. |
| 4 | `missing-key` | Restaurar la credencial antes de reintentar. |
| 5 | `timeout` | Se agotó el presupuesto; reintento manual. |
| 6 | `auth-error` | El proveedor rechazó la credencial; corregirla antes de reintentar. |
| 7 | `rate-limit` | Esperar y reintentar manualmente. |
| 8 | `provider-error` | Fallo HTTP, de conexión o del proveedor; reintento manual. |
| 9 | `invalid-response` | JSON, esquema o generación incompletos o inválidos; reintento manual. |
| 10 | `fidelity-rejected` | La revisión no superó las guardas; reintento manual. |

El timeout externo también produce `timeout`. No poder ejecutar el hook produce
`hook-failed`. Los motivos que requieren corregir entrada, configuración o
credencial muestran `review_blocked`; los fallos transitorios siguen mostrando
`review_failed` y permiten Super+Espacio. Un código desconocido nunca autoriza
una entrega ni se refleja como texto externo en los logs.

Si el HUD local está validado, su `HYPRWHSPR_LOCAL_HUD=1` permite cambiar el
estado nativo `$XDG_RUNTIME_DIR/hyprwhspr/visualizer_state` de `processing` a
`reviewing`. Solo contiene esa etiqueta. El gate controla los estados `reviewing`, `review_failed` y `review_blocked`
y evita que un éxito u ocultamiento tardío reemplace el aviso. No se guarda
el texto pendiente en el IPC del HUD.

## Evidencia de esta extracción

Se preservan modelo, prompt y límites del baseline estricto. La extracción F1
se verificó offline. La reparación posterior F1.3.3 se describe en
[retained-review.md](docs/validation/retained-review.md): separa pruebas offline,
sondas sintéticas del coordinador, activación verificada y prueba local confirmada
por el usuario. Los benchmarks
históricos privados no se exportan ni se usan como medición del candidato.

```sh
python -B -m unittest test_review test_review_gate test_hud -v
```

Las pruebas incluyen el contrato del inyector nativo con proveedor falso y
pegado simulado, retención ante fallos, límites, privacidad, JSON, invariantes,
recuperación y cancelación, stdin bloqueado y un hook real que expira a los
5 s tras emitir salida parcial. No producen un pegado real en el escritorio.
Los casos bilingües usan textos sintéticos ES, EN y mixtos: verifican las
guardas y el contrato, no la calidad de voz real ni la latencia del proveedor.

La reparación está activada; el usuario confirmó el pegado correcto tras probar
un dictado con Super+Espacio. No hubo observación física independiente ni se
acreditan ambos destinos editor/navegador, p50/p95 u otro equipo. El modo capture
evita el hook y no demuestra ese recorrido.
Véanse los [escenarios pendientes](docs/validation/scenarios.md).

Referencias oficiales del contrato conservado:
[API chat](https://docs.ollama.com/api/chat) y
[Ollama Cloud](https://docs.ollama.com/cloud).
