# Restauración de reproducción

El adaptador local sustituye únicamente `duck` al 100 % por mute temporal de
las salidas Pulse existentes al empezar. No cambia ningún volumen de salida ni
de aplicación. `pause` y porcentajes menores conservan el comportamiento nativo.
Las llamadas nativas de stop, cancel y shutdown conservan su coordinación por
sesión y ejecutan la restauración del adaptador.

La regresión cubierta por las pruebas del baseline: un flujo reducido a cero desaparece;
tras un ciclo adicional sin ese flujo caduca su recuperación. Un reemplazo que
hereda cero queda registrado con cero como volumen original. Mutear la salida
evita que el navegador reciba o memorice un volumen reducido.

Se guardan exclusivamente los cambios de mute propios. Las salidas ya muteadas
no se modifican. La identidad combina nombre, driver y propiedades del dispositivo;
un índice reutilizado no autoriza cambiar otra salida. Los errores parciales y
dispositivos ausentes mantienen su restauración pendiente en memoria para la
siguiente llamada de restore. Los errores se registran sin propiedades privadas.

La integración se instala antes de construir la app y queda protegida por los
hashes del main, ciclo de grabación y tres módulos de reproducción de 1.49.0-1.
Un fallo de instalación produce una excepción visible; no cae silenciosamente
al ducking del 100 %. El guard general detiene el daemon adaptado si cambia
el contrato core del paquete. No se modifican archivos de `/usr`, dependencias
ni servicios.

## Verificación offline

```sh
python -B -m unittest -v test_playback
```

Pulse es simulado en todas las pruebas. Se ejecutan métodos extraídos del código
nativo instalado para stop/cancel, restauración de shutdown y carreras de sesión.
La regresión del ducker antiguo se reproduce contra ese archivo real, con un
cliente Pulse falso. No se captura ni guarda voz.

## Límites

- La comprobación audible del servicio real corresponde a la activación posterior.
- No hay monitor nuevo: una salida conectada durante la grabación no se mutea.
  Si una salida desaparece muteada y reaparece después del restore, se reintenta
  en un siguiente ciclo o shutdown mientras viva el proceso; no hay recuperación
  autónoma entre llamadas ni tras SIGKILL, corte de energía o reinicio del proceso.
- El mute temporal también silencia sonidos de confirmación emitidos durante la
  grabación. Los volúmenes siguen intactos y el sonido vuelve al restaurar la salida.
- Pulse no atribuye cambios de mute a su autor. Un mute manual nuevo sobre una
  salida que ya estaba muteada por el adaptador es indistinguible del mute propio.
  Un unmute manual y cualquier cambio de volumen sí se conservan.
- Las limitaciones del ducker nativo siguen presentes en otros porcentajes y en
  el modo pause; esos modos quedan fuera de esta corrección.
