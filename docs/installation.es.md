# Instalación, diagnóstico y reversión

Esta guía describe el **snapshot de desarrollo 0.1.0-alpha.1**. Su instalador
desde fuentes permite preparar una instalación de prueba; **todavía no hay
GitHub Release**. Siguen pendientes la aceptación física del paquete y la demo.
[English](installation.md).

## Dependencias nativas

Parte de una sesión Hyprland existente en Arch/CachyOS x86_64 con PipeWire/Pulse
y WirePlumber. Instala **hyprwhspr 1.49.0-1** conservando intacta su biblioteca.
La [receta AUR](https://aur.archlinux.org/packages/hyprwhspr) se comprobó en el
commit `7ea4a07c09656c4c84e6cecc9636e969b3fbe553`; la procedencia exacta está en
[dependencies.md](provenance/dependencies.md). El instalador no actualiza ese
paquete, no ejecuta su asistente de setup ni cambia permisos de dispositivos.

Revisa la actualización normal del sistema y prepara estas dependencias:

```sh
sudo pacman -Syu --needed python python-gobject python-cairo python-evdev gtk4 gtk4-layer-shell portaudio libsndfile libpulse wl-clipboard wtype uv
```

Los locks de esta alpha corresponden a **Python 3.14**. Se requiere
`uv >=0.11.14`; se han ejercitado 0.11.14 y 0.13.1. GI/Cairo, GTK4 y evdev son
nativos; el entorno propio del daemon los hereda. El worker usa un entorno
independiente. Las dependencias pip están fijadas por versión y hash y deben
tener ruedas: no se compilan fuentes implícitamente. Los 12 hashes core y 14
del HUD rechazan deriva; una versión upstream más nueva no se acepta sin revisión.

## Construir u obtener un archivo de prueba

Desde la raíz del repositorio, construye en un directorio de salida nuevo:

```sh
build_dir=$(mktemp -d /tmp/arca-dicta-candidate.XXXXXXXX)
python -B tools/build_release.py --output "$build_dir"
cd "$build_dir"
```

Como alternativa, cuando haya una ejecución correcta de [Actions](https://github.com/infantesromeroadrian/arca-dicta/actions/workflows/ci.yml)
para el commit elegido, descarga su artefacto `source-installer-<SHA del commit>`
y descomprime el paquete de artefactos de GitHub. Contiene el tarball, su checksum
y el manifiesto. Estos archivos descargables de pruebas se conservan 14 días;
no son una GitHub Release ni una alpha aceptada.

Desde el directorio que contiene el tarball y su checksum, verifica y extrae:

```sh
sha256sum -c arca-dicta-0.1.0-alpha.1.tar.gz.sha256
tar -xzf arca-dicta-0.1.0-alpha.1.tar.gz
cd arca-dicta-0.1.0-alpha.1
./install install --download-model
./install doctor --no-service
```

Se rechaza instalar directamente desde un checkout sin manifiesto de release
generado. El checksum y el manifiesto detectan alteraciones; no sustituyen una
firma independiente. El [registro de validación](validation/ci.md) identifica
el artefacto probado anteriormente. Cada nuevo build, incluidos los cambios
documentales, tiene su propio manifiesto y checksum.

El prefijo por defecto es `${XDG_DATA_HOME:-$HOME/.local/share}/arca-dicta`.
Para elegir otro, coloca `--prefix` antes del subcomando:

```sh
./install --prefix "$HOME/.local/share/ARCA Dicta" install --download-model
./install --prefix "$HOME/.local/share/ARCA Dicta" doctor --no-service
```

La preparación descarga ruedas Python y aproximadamente 484 MB de pesos;
reserva espacio adicional para dos entornos y la caché. El modelo es
`Systran/faster-whisper-small`, revisión
`536b0662742c02347bc0e980a01041f333bce120`. Se verifican cuatro archivos, incluido
`vocabulary.txt`: la prueba real del subconjunto de tres marcadores no carga
este snapshot. También puedes proporcionar los cuatro archivos verificados
con `install --model-dir /ruta/absoluta/modelo`; ese directorio externo se conserva
al desinstalar.

Preparar exige un prefijo vacío propio y verifica imports. No inicia servicios,
abre micrófono, usa portapapeles, lee credenciales ni modifica configuración del
escritorio. Repetir la misma instalación intacta no tiene efectos adicionales.
Se rechazan archivos modificados y actualizaciones de versión sobre el mismo
prefijo; esta primera alpha exige una instalación nueva para ese caso. Un fallo
limpia el runtime recién creado y las descargas parciales.

## Credenciales y activación explícita

La clave `groq` sigue en el almacén nativo privado de hyprwhspr,
`${XDG_DATA_HOME:-$HOME/.local/share}/hyprwhspr/credentials`. Configúrala por el
procedimiento de credenciales de upstream antes de activar. No guardes un
`rest_api_key` dentro de config.json: se rechaza para evitar que upstream migre
y modifique el almacén durante la transacción. ARCA no copia esas credenciales.

La revisión necesita `OLLAMA_API_KEY` u `OLLAMA_CLOUD_API_KEY`. Se mantienen las
referencias de entorno ya existentes en el servicio. Para un equipo nuevo,
crea privadamente `${XDG_CONFIG_HOME:-$HOME/.config}/arca-dicta/secrets.env`
(directorio 0700, archivo 0600) y escribe la asignación con tu editor. El drop-in
lee ese archivo opcional; el instalador no lo abre. No pongas claves en comandos,
Git, incidencias o capturas.

Cuando quieras sustituir el arranque del servicio existente:

```sh
./install activate
```

Si detecta una unidad personalizada u overrides previos, revisa ese baseline
y ejecuta `./install activate --adopt-existing`. El flag permite adoptar el
contexto existente: sus archivos se respaldan y se conservan. Un override
posterior a `zz-arca-dicta.conf` por orden de nombre bloquea la activación.

Solo se usa **hyprwhspr.service**. Se parchean las claves necesarias de la
configuración nativa: Groq, autodetección, hook de revisión obligatorio, HUD,
silencio completo, sin escritura provisional sin revisar, sin Enter ni archivos
de depuración. Se conservan dispositivos, bindings existentes y campos ajenos.
El CLI y el teclado siguen hablando con el mismo daemon por el IPC nativo.
Detener el servicio pierde el dictado pendiente en memoria: termínalo o
descártalo previamente.

Un config nuevo espera bindings externos. Revisa conflictos antes de añadir
conscientemente uno a Hyprland, por ejemplo:

```ini
bind = SUPER, SPACE, exec, hyprwhspr record toggle
```

El instalador nunca escribe esa línea. Conserva el directorio extraído o utiliza
`<prefijo>/releases/0.1.0-alpha.1/app/install` para administrar la instalación.
Si elegiste prefijo propio, sigue indicando el mismo `--prefix`.

## Diagnóstico, reversión y desinstalación

`./install doctor` comprueba contratos, integridad de archivos y modelo, imports
y metadatos del servicio. `--no-service` omite systemd. Nunca contacta proveedores
ni demuestra validez de claves, captura, HUD visible, pegado o restauración audible.
Los [escenarios físicos](validation/scenarios.md) siguen siendo necesarios.

```sh
./install deactivate
./install uninstall
```

La desactivación restaura la entrada anterior y el estado activo/inactivo previo.
Recupera bytes y modo originales del config si sigue intacto; si cambian campos
ajenos, revierte únicamente las claves gestionadas y conserva esas ediciones.
Un cambio en una clave gestionada o en una definición del servicio se comunica
antes de detenerlo. Reconcilia esa diferencia con el respaldo privado y repite:
no existe un modo de sobrescritura ciega.

El prefijo conserva respaldos privados y un journal durable. Un fallo ordinario
de activación intenta restaurar el baseline. Tras una interrupción, repite
`deactivate`; si informa de conflicto, comprueba el respaldo local antes de
editar. No borres el journal para saltarte el control de instalación activa.

Desinstalar se niega mientras siga activa la instalación. Solo elimina archivos
propios que coinciden con su inventario; preserva modificaciones, archivos
desconocidos y respaldos. Conserva paquetes nativos, credenciales, modelos externos
y bindings globales. No borra recursivamente un directorio previo del usuario.

## Validación registrada y aceptación pendiente

La validación local en Arch pasó 177 tests, Ruff, ShellCheck, los 26 contratos
upstream y dos builds idénticos. Un usuario nuevo de contenedor completó la
preparación, inferencia sintética con el worker local, instalación repetida y
desinstalación. Una activación real separada pasó doctor 35/35 y recuperó el
servicio anterior con los 25 archivos y modos del baseline intactos y sin
cambios de mute/volumen en cuatro salidas. Esa comprobación de activación no
inició captura, inferencia ni llamadas a proveedores.

Son resultados del artefacto congelado anterior, identificado en la
[validación CI](validation/ci.md) y el [registro de reversión](validation/installer-baseline.md).
El runtime existente fue aceptado para uso personal. Quedan por validar el
dictado físico del paquete en editor y navegador, los fallos, la cancelación y
la restauración audible, además de medir la latencia y preparar la demo. La
primera GitHub Release sigue pendiente.
Estas comprobaciones no acreditan latencia física ni calidad de los proveedores.
