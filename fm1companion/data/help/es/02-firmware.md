# Cambiar de firmware y volver al original

El FM-1 puede ejecutar distintos firmwares, p. ej. **Melodee**, **Skiff**, **SLOOP**, **KARMA** o el firmware
oficial de M-VAVE. Todo lo necesario está en **una sola** página: [Firmware+](app://page/discover) (barra lateral ›
*Datos y herramientas*).

* **Todos los firmwares** – el catálogo: buscar, ver, probar en el simulador, *Añadir a mis firmwares*.
* **Instalar y versiones** – [aquí](app://page/firmware) instalas, actualizas y cambias: elige versiones
  de un firmware, *Solo descargar* varias versiones (también de un firmware que aún no está en el dispositivo) y
  luego salta entre ellas, *Instalar archivo .fwsc propio …*, *Añadir firmware …* (enlace o repositorio),
  *Volver al original …* y el camino de vuelta desde V16 pasando por V15.
* **Novedades** y **Herramientas y conocimiento** – nuevas versiones de los firmwares que sigues y enlaces útiles.

Si hay una actualización para tu FM-1, también se muestra en el [Resumen](app://page/overview) (*Firmware y actualizaciones*).

## Cómo cambiar

1. **Haz una copia de seguridad** (Ctrl+B). Antes de cada actualización la app también hace una por sí sola (se puede desactivar en los
   ajustes) y recuerda a qué firmware pertenece.
2. En [Firmware+ › Instalar y versiones](app://page/firmware) elige el firmware y la versión, y luego *Instalar en el FM-1
   activo*.
3. **No desconectes la alimentación ni el USB** hasta que la app indique “listo”. Cierra otros programas que envíen MIDI.
4. Si más tarde vuelves a un firmware, la app te ofrece la copia de seguridad correspondiente.

## Un cambio pasa siempre por la V15 original

Cuando **cambias** a otro firmware, la app instala primero el **firmware original V15** de M-VAVE sin modificar
(con suma de comprobación) y después el nuevo firmware: dos instalaciones en un solo plan. Así el nuevo firmware
arranca desde un estado limpio y no quedan restos del anterior (ajustes, proyectos, zonas de samples) en la flash. El
plan de instalación lo indica: “Cambio de X a Y: la app instala primero el firmware original V15 y después Y”.

* **Actualización o vuelta atrás dentro del mismo firmware** (p. ej. SLOOP 2.8.5 → 2.8.6, KARMA 3.1 → 3.0.5):
  directamente, sin el paso por V15. Un fork es un firmware propio: SLOOP → KARMA es un cambio.
* **Firmware no reconocido** (la app no sabe qué funciona en el FM-1): cuenta como cambio, así que pasa por V15.
* **El FM-1 ya funciona con la V15 original**, o **vuelves a la V15 original**: directamente.
* **V16**: como antes: a V16 desde un firmware de la comunidad y la vuelta desde V16 pasan siempre por V15.
* La **copia de seguridad** se hace antes del primer paso, como en toda instalación.
* Si el cambio se detiene tras el paso por V15 (cable, alimentación, cancelación), el FM-1 funciona con la V15
  original. La app recuerda el destino: [Firmware+ › Instalar y versiones](app://page/firmware) muestra
  *Continuar: instalar …*; el paso por V15 no se repite. Si se detuvo *durante* un paso, termínalo primero en la página
  [Rescate](app://page/recovery).
* Los expertos pueden marcar en el plan *Experto: instalar directamente sin el paso por V15*. Está desactivado por
  defecto, no se recuerda y el plan advierte de que pueden quedar restos del firmware anterior. No hay ningún ajuste
  para ello.

## Volver al firmware original

* Melodee incluye un camino **de vuelta al firmware oficial** (V15); antes se hace una copia de seguridad.
* Los archivos y actualizadores oficiales están disponibles en M-VAVE: <https://www.m-vave.com/download>.
* El estado actual del firmware oficial y actualizadores alternativos (también para Linux) se recopilan en
  [awesome-fm-1](https://github.com/cicloid/awesome-fm-1).

## Conviene saber

* Los proyectos y presets de un firmware no encajan automáticamente en otro. Por eso: copias de seguridad por firmware.
* Melodee 1.6 incluye un archivo de recursos (fuente, sonidos de fábrica). A partir de 1.6.0, el cargador de actualización lo escribe
  por sí solo; si aún falta (p. ej. en la primera actualización desde 1.5.1), la app lo escribe después.
* Actualización a **Melodee 1.6.0**: tu copia de seguridad es tu camino de vuelta, los proyectos pueden sonar distinto – consulta
  [Melodee 1.6.0](help:14-melodee-1-6-0).
* Si algo sale mal: [Modo de actualización y rescate](help:03-update-modus).

## Hubs USB

Con un buen cable conectado directamente al ordenador, instalar es lo más sencillo. A través de un **hub USB** la
conexión se corta más a menudo por un momento, o el FM-1 tarda más en volver tras sus reinicios – entonces una
instalación empieza pero no termina. Por eso la app detecta un hub delante del FM-1 y pasa por sí sola al
**modo USB cuidadoso** (se indica en el plan de instalación):

* espera más al cargador de actualización y al reinicio, responde más despacio a las peticiones del FM-1 y solo
  tiene una respuesta en camino a la vez;
* si la conexión se corta mientras escribe, espera al **mismo** FM-1 en modo de actualización y vuelve a empezar
  la escritura (hasta 3 veces) – solo con el cargador que corresponde al paquete, igual que *Terminar la
  instalación* en la página [Rescate](app://page/recovery). Todas las comprobaciones de seguridad siguen igual.

Ajustes › *Modo USB cuidadoso al flashear*: automático (predeterminado), siempre o nunca.

Si aun así no funciona:

1. Conecta el FM-1 **directamente** al ordenador con un cable de datos corto – en una Raspberry Pi mejor a un
   **puerto USB 2 negro**. Si necesitas un hub: uno con **fuente de alimentación propia**, sin otros dispositivos
   que consuman mucho.
2. La [comprobación del sistema](app://action/syscheck) muestra cómo está conectado el FM-1 (hubs, velocidad, USB 2/3) y si
   el **ahorro de energía USB** (autosuspend) está activado para el FM-1 o su hub. La **regla udev** opcional de la
   app lo desactiva: `./install.sh --usb-power-rule` (o la casilla del instalador gráfico), o a mano
   `sudo install -m 0644 packaging/udev/60-fm1-companion.rules /etc/udev/rules.d/` y
   `sudo udevadm control --reload-rules`. El paquete Debian la incluye en `/usr/share/fm1-companion/udev/`.
3. Cada instalación escribe un **registro de flasheo** (cada petición del FM-1, peticiones repetidas, cortes,
   mensajes del kernel de ese momento). Forma parte del informe de errores ([Ayuda › Crear informe de errores](app://action/report)) –
   adjúntalo cuando informes de un problema.
