# HUD local de dictado

El panel mide 560 × 124 píxeles. El texto provisional usa Noto Sans a
17 píxeles y dos líneas, con 520 píxeles útiles. Si el dictado es largo,
conserva las palabras recientes y señala el recorte con puntos suspensivos.
El estado y la onda ocupan zonas separadas; el borrador nunca aparece como
resultado final. Usa fondo oscuro al 98 % de opacidad, cian para actividad y
ámbar para revisión retenida. Respeta `gtk-enable-animations` para la actividad
decorativa. La paleta está en el adaptador; no se incluye una hoja CSS personal.

Estados: Escuchando, En pausa, Transcribiendo, Revisando, Listo y Error.
La revisión existe únicamente cuando el hook la realiza realmente.

## Integración

El launcher llama a `hud_bridge.install_hud()` antes de `upstream.main()` y
dentro de su guard de compatibilidad existente. Devuelve un booleano y no
propaga fallos de integración. No modifica `/usr`, configuración ni servicio.

El puente cambia solo `_mic_osd_dir` en una subclase del runner instalado.
El proceso hijo entra por `hud_runtime/mic_osd/main.py`, que carga el main
nativo y sustituye ventana/visualización. Señales, PID, layer-shell, feed,
IPC, temporizadores y cierre continúan en las implementaciones originales.
El fallback de captura del OSD lee el mismo feed, aunque esté ausente o
desactualizado: jamás abre otra entrada de audio.

El guard verifica catorce hashes del contrato privado nativo y la sintaxis
de los tres módulos locales. Ante drift no activa el HUD. Si el renderer
no arranca, el runner prueba una sola vez la UI original. Un fallo de dibujo
abandona la decoración y vuelve al dibujo nativo. No se registran textos.

`HYPRWHSPR_LOCAL_HUD=1` autoriza al hook a escribir la cadena `reviewing`
en el IPC nativo `$XDG_RUNTIME_DIR/hyprwhspr/visualizer_state`, únicamente
al entrar realmente en revisión y cuando el estado anterior sea `processing`.
Sin ese indicador, el hook no emite el estado añadido. El hook no elimina
ni restaura el fichero: el resultado nativo escribe después `success/error`.

## Verificación sin escritorio ni audio

```sh
python -B -m unittest -v test_hud
```

Las pruebas usan Cairo/Pango fuera de pantalla, estados y texto sintéticos,
y comprueban dos líneas a 17 px, dimensiones, conservación del final,
fallback y ausencia de una segunda captura. No sustituyen la comprobación
posterior del compositor y del servicio desplegado.

La prueba física en el compositor queda pendiente. F1 no activa ventanas ni
reinicia el servicio. `ARCA_DICTA_UPSTREAM_LIB` se propaga al puente y al loader
del proceso HUD; la extracción no incorpora los módulos nativos delegados.

## Fases iniciales y continuidad del borrador

`PreviewController` admite el keyword opcional
`status=app._mic_osd_runner.set_state`. El owner del launcher lo añade en
`build_controller`. Usa el IPC nativo `visualizer_state`, sin ficheros nuevos:

- `listening`: la sesión está capturando; el cuerpo vacío dice «Te escucho».
- `decoding`: empieza una inferencia real; si aún no hay borrador, aparece
  «Preparando primeras palabras».
- `preview_unavailable`: el worker ha fallado; aparece «Vista previa no
  disponible. Tu dictado sigue grabándose». El estado no finge recuperación:
  un worker deshabilitado continúa así hasta reiniciar el servicio.

Cada señal se serializa con invalidación y valida token, generación y captura
activa. Una respuesta antigua no puede devolver `processing/reviewing` a un
estado de escucha. El callback de estado es opcional y sus fallos no detienen
la inferencia. No se cambian modelo, idioma, esperas, intervalos ni revisión.

`PreviewDisplay` conserva una copia acotada del borrador únicamente en memoria
de esa ventana. El borrado nativo de `transcript_preview` vacía el texto vivo;
la copia permite mantener las últimas dos líneas en `processing/reviewing`,
atenuadas y marcadas «Provisional». Nunca se usa como texto final ni se pega.
`success/error`, ocultar/cancelar y una nueva sesión eliminan la copia y la
caché de presentación. No se escriben transcripciones ni historial adicionales.

## Revisión obligatoria

El gate local mantiene visibles `review_failed` («Dictado retenido.
Super+Espacio para reintentar») y `review_blocked` («Consulta el aviso para
continuar» para límites/configuración). Se borran los borradores visibles; el
pendiente solo vive en memoria del inyector. Un resultado u ocultamiento tardío
no pisa estos estados. El fallback gráfico nativo los muestra como error y el
aviso nativo contiene la acción concreta.
